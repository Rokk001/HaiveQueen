"""F3 of the follow-up fixes: owner stop during a self-update (F-11), stale update lock at start (F-12), refused start never silent (F-13)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import db, gui, loop, overview, selfupdate, updates  # noqa: E402
from test_selfupdate import NEW, OLD, FakeGit, SelfUpdateBase  # noqa: E402

WORKER_LINE = "python -m haivequeen self-update --worker"


def owner_stop():
    """What `bzz stop` writes: a STOP flag without the self-update marker."""
    loop.stop_flag().write_text("stop requested 2026-11-09T10:00:00\n", encoding="utf-8")


class OwnerStopTest(SelfUpdateBase):
    """F-11: the owner stops HaiveQueen while the worker waits."""

    def run_worker(self, git, round_done=True, alive=None, loop_pid=None):
        """Start run_update in a thread with its own connection; returns (thread, result dict)."""
        out = {}

        def work():
            """The worker thread."""
            conn = db.connect(Path(self.tmp.name) / "haivequeen.db")
            git.conn = conn if round_done else None
            try:
                out["res"] = selfupdate.run_update(conn, live=self.live, run=git, sleep=lambda s: time.sleep(0.005), poll=0.01, round_timeout=20.0,
                                                   alive=alive, loop_pid=loop_pid)
            finally:
                conn.close()
        t = threading.Thread(target=work)
        t.start()
        return t, out

    def wait_for(self, condition):
        """Poll up to 5 seconds."""
        deadline = time.time() + 5
        while not condition() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(condition())

    def test_stop_while_waiting_for_agents_skips_the_update(self):
        """Nothing was changed: the result is skipped, the lock is gone, no git command beyond rev-parse ran."""
        db.add_agent(self.conn, "ODP", name="Busy", phase="work", status="running", pid=21)
        git = FakeGit()
        t, out = self.run_worker(git, alive=lambda pid: True, loop_pid=lambda: 0)
        self.wait_for(lambda: loop.stop_flag().exists())
        time.sleep(0.1)
        owner_stop()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(out["res"]["result"], "skipped")
        self.assertEqual(git.words(), ["rev-parse"])
        self.assertFalse(selfupdate.lock_path().exists())
        row = self.row()
        self.assertTrue(row["ended"])
        self.assertTrue(row["note"].startswith("skipped, stopped by the owner"))

    def test_stop_while_waiting_for_the_first_round_keeps_the_update(self):
        """The update is in place: no rollback, result ok with the stop in the note, lock gone."""
        git = FakeGit()
        t, out = self.run_worker(git, round_done=False, alive=lambda pid: False, loop_pid=lambda: 4242 if git.started else 0)
        self.wait_for(lambda: git.started)
        time.sleep(0.1)
        owner_stop()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(out["res"]["result"], "ok")
        self.assertNotIn("reset", git.words())
        self.assertIn("stopped by the owner during the wait", self.row()["note"])
        self.assertEqual(self.row()["result"], "ok")
        self.assertEqual(self.row()["new_commit"], NEW)
        self.assertFalse(selfupdate.lock_path().exists())

    def test_own_stop_flag_is_no_owner_stop(self):
        """The flag the worker writes itself carries its marker and ends nothing."""
        loop.stop_flag().write_text("stop requested x (self-update)\n", encoding="utf-8")
        self.assertFalse(selfupdate.owner_stopped())
        owner_stop()
        self.assertTrue(selfupdate.owner_stopped())

    def test_dashboard_shows_both_results_as_stopped_not_failed(self):
        """self_update_line says "stopped by the owner" and does not use the red failed colour."""
        for result, note in (("failed", "skipped, stopped by the owner"), ("ok", "updated to abc; stopped by the owner during the wait")):
            parts = overview.self_update_line({"result": result, "note": note, "ended": "2026-11-09T10:00:00", "new_commit": "abcdef0123"})
            text = "".join(p[0] for p in parts)
            self.assertIn("stopped by the owner", text)
            self.assertNotIn("failed", text)
            self.assertNotIn("red", [p[1] for p in parts])

    def test_skipped_update_is_offered_again(self):
        """After the skipped install the header offers the release again, with no failure mark."""
        row = {"id": 5, "ended": "2026-11-09T10:00:00", "result": "failed", "note": "skipped, stopped by the owner"}
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"BZZ_CONFIG_DIR": folder}):
            state = {"available": {"version": "1.2.3"}, "installing": {"version": "1.2.3", "after_row": 4, "since": "x"}}
            updates.save_state(state)
            self.assertEqual(updates.settle_install(row), "stopped")
            after = updates.load_state()
            self.assertNotIn("installing", after)
            self.assertFalse(after.get("failed"))
            self.assertEqual((after.get("available") or {}).get("version"), "1.2.3")


class StaleLockTest(SelfUpdateBase):
    """F-12: a stale lock is cleared at every start."""

    def lock(self, pid=4242):
        """Write the lock file."""
        selfupdate.lock_path().write_text(str(pid), encoding="ascii")

    def clear(self, alive=True, cmdline=WORKER_LINE, now=None, head=NEW, killed=None):
        """clear_stale_lock with stubbed process lookups."""
        return selfupdate.clear_stale_lock(self.conn, alive=lambda pid: alive, cmdline=lambda pid: cmdline, now=now,
                                           kill=(killed.append if killed is not None else (lambda pid: None)), head=lambda: head)

    def test_dead_process_is_cleared_and_logged(self):
        """A lock whose process does not run is deleted and one event is written."""
        self.lock()
        with mock.patch.object(loop.osplat, "runs_dir", return_value=Path(self.tmp.name) / "runs"):
            why = self.clear(alive=False)
        self.assertTrue(why)
        self.assertFalse(selfupdate.lock_path().exists())
        self.assertTrue(self.conn.execute("SELECT 1 FROM events WHERE text LIKE '%stale%lock%'").fetchone())

    def test_another_program_is_cleared_but_not_killed(self):
        """A reused process id of another program: the lock goes, the program is left alone."""
        self.lock()
        killed = []
        self.assertTrue(self.clear(cmdline="notepad.exe file.txt", killed=killed))
        self.assertFalse(selfupdate.lock_path().exists())
        self.assertEqual(killed, [])

    def test_worker_past_its_round_limit_is_ended(self):
        """A worker whose wait for the first round passed the limit is ended and its lock deleted."""
        self.lock()
        selfupdate.round_path().write_text(str(time.time() - selfupdate.ROUND_TIMEOUT - 600), encoding="ascii")
        killed = []
        self.assertTrue(self.clear(killed=killed))
        self.assertEqual(killed, [4242])
        self.assertFalse(selfupdate.lock_path().exists())

    def test_living_worker_inside_its_limit_still_blocks(self):
        """A living worker waiting for agents, or inside its round limit, keeps its lock."""
        self.lock()
        killed = []
        self.assertEqual(self.clear(killed=killed), "")
        selfupdate.round_path().write_text(str(time.time() - 60), encoding="ascii")
        self.assertEqual(self.clear(killed=killed), "")
        self.assertTrue(selfupdate.lock_path().exists())
        self.assertEqual(killed, [])

    def test_applied_update_is_kept_and_its_row_closed(self):
        """The live folder moved on: no rollback, the open row is closed with the stale lock note."""
        self.lock()
        row_id = selfupdate.start_row(self.conn, OLD)
        self.assertTrue(self.clear(alive=False, head=NEW))
        row = dict(self.conn.execute("SELECT * FROM self_updates WHERE id=?", (row_id,)).fetchone())
        self.assertTrue(row["ended"])
        self.assertEqual((row["result"], row["new_commit"]), ("ok", NEW))
        self.assertIn("ended at start: stale lock", row["note"])

    def test_start_clears_the_stale_lock_and_goes_on(self):
        """`start` deletes the stale lock and runs the loop."""
        self.lock(4242)
        args = Namespace(foreground=True, rounds=1, round_minutes=None, keep_pause=True, json=True, desktop=False)
        with mock.patch.dict(os.environ), mock.patch.object(loop.osplat, "process_alive", return_value=False), \
                mock.patch.object(loop, "run_loop", return_value=0) as run_loop, mock.patch("builtins.print"):
            os.environ.pop(selfupdate.RESTART_VAR, None)
            loop.cmd_start(args, self.conn)
        run_loop.assert_called_once()
        self.assertFalse(selfupdate.lock_path().exists())


class RefusedStartTest(SelfUpdateBase):
    """F-13: a refused start shows its reason."""

    def test_bzz_start_prints_the_reason_and_exits_1(self):
        """`bzz start` prints the refusal reason and returns 1."""
        args = Namespace(foreground=True, rounds=1, round_minutes=None, keep_pause=True, json=True, desktop=False)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch.object(selfupdate, "clear_stale_lock", return_value=""), \
                mock.patch("builtins.print") as printed:
            code = loop.cmd_start(args, self.conn)
        self.assertEqual(code, 1)
        self.assertIn("waiting for running agents", json.loads(printed.call_args.args[0])["error"])

    def test_start_refusal_text_from_the_output(self):
        """The reason is the error of the last JSON line, or the last error line of a crash."""
        out = 'noise\n{"ok": false, "updating": true, "error": "An update is waiting for running agents; HaiveQueen starts again after it."}\n'
        self.assertIn("waiting for running agents", gui.start_refusal(1, out, ""))
        self.assertEqual(gui.start_refusal(0, '{"ok": true}', ""), "")
        self.assertIn("boom", gui.start_refusal(1, "", "Traceback\nTimeoutError: boom"))

    def test_dashboard_start_shows_a_refusal_in_a_message(self):
        """Start clicked, start refused: the dashboard opens a message with the reason."""
        shown = []
        stub = mock.Mock()
        stub.inform.side_effect = lambda text, *a, **k: shown.append(text)
        done = Namespace(returncode=1, stdout='{"ok": false, "error": "Waiting for the previous stop to finish."}\n', stderr="")
        with mock.patch.object(gui.subprocess, "run", return_value=done), \
                mock.patch.object(gui.gui_async, "run_async", side_effect=lambda widget, work, done_cb, failed=None, **k: done_cb(work())):
            gui.Dashboard.start(stub)
        self.assertEqual(len(shown), 1)
        self.assertIn("previous stop", shown[0])

    def test_dashboard_start_without_refusal_shows_nothing(self):
        """A start that went through shows no message."""
        stub = mock.Mock()
        done = Namespace(returncode=0, stdout='{"ok": true}\n', stderr="")
        with mock.patch.object(gui.subprocess, "run", return_value=done), \
                mock.patch.object(gui.gui_async, "run_async", side_effect=lambda widget, work, done_cb, failed=None, **k: done_cb(work())):
            gui.Dashboard.start(stub)
        stub.inform.assert_not_called()


if __name__ == "__main__":
    unittest.main()
