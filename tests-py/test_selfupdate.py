"""Tests of the self-update (K5, K6): table, detached worker, waiting, update steps, rollback. Nothing touches the real STOP flag, lock file or agents:
every test uses a temp data folder, a scripted git runner and stubbed processes."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import cli, db, loop, selfupdate  # noqa: E402
from haivequeen import platform as osplat  # noqa: E402

OLD, NEW = "a" * 40, "b" * 40


class FakeGit:
    """Scripted runner for git, pip and `start`: records every call, HEAD moves with merge/reset, `fail` maps a command word to an exit code."""

    def __init__(self, conn=None, changed="README.md", fail=None, head=OLD, target=NEW):
        self.calls, self.head, self.target, self.changed, self.fail, self.conn = [], head, target, changed, fail or {}, conn
        self.started = False   # True once a `start` ran: from then on the new loop "runs"

    def words(self):
        """The recorded calls as short strings (git subcommand, 'pip' or 'start')."""
        return [c[0] for c in self.calls]

    def __call__(self, argv, cwd):
        """Answer one command like run_command: (exit code, output)."""
        if argv[0] == "git":
            sub = [a for a in argv[3:]]   # after: git -C <live>
            name = sub[0]
            self.calls.append((name + (" " + sub[1] if name in ("merge", "reset", "push") else ""), str(cwd)))
            if self.fail.get(name):
                return self.fail[name], "git %s failed" % name
            if name == "rev-parse":
                return 0, self.head + "\n"
            if name == "merge":
                self.head = self.target
            if name == "reset":
                self.head = sub[2]
            if name == "diff":
                return 0, self.changed + "\n"
            return 0, ""
        if "pip" in argv:
            self.calls.append(("pip", str(cwd)))
            return self.fail.get("pip", 0), "pip out"
        self.calls.append(("start", str(cwd)))
        self.start_argv = list(argv)
        self.started = True
        if self.fail.get("start"):
            return self.fail["start"], "start failed"
        if self.conn is not None:   # the new loop runs and ends its first round
            loop.write_state(self.conn, "idle", datetime.now(), None)
        return 0, ""


class SelfUpdateBase(unittest.TestCase):
    """A temp data folder with its own database; the environment variable is restored afterwards."""

    def setUp(self):
        """Point BZZ_DATA_DIR at a temp folder and open a database there."""
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "haivequeen.db")
        self.addCleanup(self.conn.close)
        self.live = Path(self.tmp.name) / "live"

    def update(self, git, **kw):
        """run_update with the scripted runner, no real sleeping, a living new loop."""
        args = dict(live=self.live, run=git, sleep=lambda s: time.sleep(0.005), poll=0.01, round_timeout=2.0,
                    loop_pid=lambda: 4242 if getattr(git, "inner", git).started else 0)
        args.update(kw)
        return selfupdate.run_update(self.conn, **args)

    def row(self):
        """The newest self_updates row as a dict."""
        return dict(self.conn.execute("SELECT * FROM self_updates ORDER BY id DESC LIMIT 1").fetchone())

    def stub_faults(self):
        """Replace haivequeen.faults (written by another step) with a recording stub; returns the list of calls."""
        calls = []
        mod = types.ModuleType("haivequeen.faults")
        mod.file_fault = lambda conn, project, kind, text: calls.append((project, kind, text))
        patcher = mock.patch.dict(sys.modules, {"haivequeen.faults": mod})
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class TableTest(SelfUpdateBase):
    """The table self_updates (schema.sql and migration)."""

    def test_table_and_columns(self):
        """A new database has self_updates with the columns of K5."""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(self_updates)")]
        self.assertEqual(cols, ["id", "started", "ended", "old_commit", "new_commit", "result", "note"])

    def test_result_values_are_limited(self):
        """result accepts ok, rollback, failed (and NULL while running) and nothing else."""
        for value in ("ok", "rollback", "failed", None):
            self.conn.execute("INSERT INTO self_updates (started, result) VALUES ('t', ?)", (value,))
        with self.assertRaises(Exception):
            self.conn.execute("INSERT INTO self_updates (started, result) VALUES ('t', 'maybe')")

    def test_migration_from_version_5(self):
        """A database of schema version 5 gets the table and keeps its rows."""
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        raw = sqlite3.connect(path)
        raw.executescript("CREATE TABLE kv (project TEXT NOT NULL, key TEXT NOT NULL, value TEXT, updated_at TEXT, PRIMARY KEY (project, key));"
                          "INSERT INTO kv VALUES ('p', 'k', 'v', 't'); PRAGMA user_version = 5;")
        raw.commit()
        raw.close()
        conn = db.connect(path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM self_updates").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT value FROM kv").fetchone()[0], "v")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        finally:
            conn.close()


class SpawnTest(SelfUpdateBase):
    """The command only spawns a detached worker and returns at once."""

    def test_live_folder_is_the_package_folder(self):
        """The live folder comes from the location of the package and no machine path is written into the module."""
        self.assertEqual(selfupdate.live_folder(), Path(selfupdate.__file__).resolve().parent.parent)
        text = Path(selfupdate.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Users", text)
        self.assertNotIn("GitHub", text)

    def test_spawn_windows_flags(self):
        """On Windows the worker gets no handles, close_fds, the three creation flags, cwd = live folder and the module call of the interpreter."""
        with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(subprocess, "Popen") as popen:
            self.assertEqual(selfupdate.spawn_worker(self.live), {"how": "detached"})
        argv = popen.call_args[0][0]
        kw = popen.call_args[1]
        self.assertEqual(argv[1:], ["-m", "haivequeen", "self-update", "--worker"])
        self.assertEqual(argv[0], osplat.python_executable())
        self.assertEqual(kw["cwd"], str(self.live))
        self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]), (subprocess.DEVNULL,) * 3)
        self.assertTrue(kw["close_fds"])
        self.assertEqual(kw["creationflags"], 0x00000008 | 0x00000200 | 0x01000000)

    def test_spawn_default_cwd_is_the_live_folder(self):
        """Without an argument the worker starts in the folder of the package."""
        with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(subprocess, "Popen") as popen:
            selfupdate.spawn_worker()
        self.assertEqual(popen.call_args[1]["cwd"], str(selfupdate.live_folder()))

    def test_spawn_falls_back_to_schtasks(self):
        """When Windows refuses the breakaway flag, a one-shot task is created and run."""
        with mock.patch.object(osplat, "is_windows", return_value=True), \
                mock.patch.object(subprocess, "Popen", side_effect=PermissionError("breakaway")), \
                mock.patch.object(subprocess, "run") as run:
            self.assertEqual(selfupdate.spawn_worker(self.live), {"how": "schtasks"})
        first, second = run.call_args_list[0][0][0], run.call_args_list[1][0][0]
        self.assertEqual(first[:2], ["schtasks", "/create"])
        self.assertIn("self-update --worker", first[first.index("/tr") + 1])
        self.assertIn(str(self.live), first[first.index("/tr") + 1])
        self.assertEqual(second[:2], ["schtasks", "/run"])

    def test_spawn_posix_new_session(self):
        """Elsewhere the worker gets its own session."""
        with mock.patch.object(osplat, "is_windows", return_value=False), mock.patch.object(subprocess, "Popen") as popen:
            selfupdate.spawn_worker(self.live)
        self.assertTrue(popen.call_args[1]["start_new_session"])

    def test_command_returns_at_once(self):
        """`self-update` without --worker spawns and prints; it never runs the update itself."""
        with mock.patch.object(selfupdate, "spawn_worker", return_value={"how": "detached"}) as spawn, \
                mock.patch.object(selfupdate, "run_update") as upd, mock.patch("builtins.print") as pr:
            code = cli.main(["self-update"])
        self.assertEqual(code, 0)
        spawn.assert_called_once_with()
        upd.assert_not_called()
        self.assertEqual(json.loads(pr.call_args[0][0]), {"ok": True, "how": "detached"})

    def test_worker_flag_runs_the_update(self):
        """`self-update --worker` runs the update in this process; exit 0 for ok and rollback, 1 otherwise."""
        for result, code in (("ok", 0), ("rollback", 0), ("failed", 1), ("locked", 1)):
            with mock.patch.object(selfupdate, "run_update", return_value={"result": result, "note": "", "old": "", "new": ""}), \
                    mock.patch("builtins.print"):
                self.assertEqual(cli.main(["self-update", "--worker"]), code, result)


class WaitTest(SelfUpdateBase):
    """The update waits for loop and agents of both projects and ends nothing."""

    def agent(self, project, name, pid, phase="work", status="running"):
        """Insert a running agent row."""
        return db.add_agent(self.conn, project, name=name, phase=phase, status=status, pid=pid)

    def test_busy_agents_counts_living_processes_only(self):
        """A row with a living process is busy, a row whose process is gone counts as ended, a fresh row without pid is starting."""
        self.agent("ODP", "Alive", 11)
        self.agent("ODP", "Gone", 12)
        db.add_agent(self.conn, "HaiveQueen", name="Starting", status="starting")
        db.add_agent(self.conn, "HaiveQueen", name="Old", status="starting", started_at="2020-01-01T00:00:00")
        self.agent("ODP", "Done", 13, status="done")
        self.assertEqual(sorted(selfupdate.busy_agents(self.conn, alive=lambda pid: pid == 11)), ["Alive", "Starting"])

    def test_dead_process_row_does_not_hold_the_update(self):
        """An agent row whose process is gone does not make the update wait."""
        self.agent("ODP", "Ghost", 99)
        git = FakeGit(self.conn)
        res = self.update(git, alive=lambda pid: False)
        self.assertEqual(res["result"], "ok")

    def test_update_waits_for_agents_of_both_projects_and_ends_none(self):
        """With a running ODP agent and a HaiveQueen agent nothing but rev-parse happens and STOP exists; each agent ends by itself, then the update runs."""
        self.agent("ODP", "Odp", 21)
        self.agent("HaiveQueen", "Hq", 22)
        living = {21, 22}
        git = FakeGit()
        out = {}

        def work():
            """The worker thread with its own connection."""
            conn = db.connect(Path(self.tmp.name) / "haivequeen.db")
            git.conn = conn
            try:
                out["res"] = selfupdate.run_update(conn, live=self.live, run=git, sleep=lambda s: time.sleep(0.005), poll=0.01, round_timeout=5.0,
                                                   alive=lambda pid: pid in living, loop_pid=lambda: 4242 if git.started else 0)
            finally:
                conn.close()

        t = threading.Thread(target=work)
        t.start()
        deadline = time.time() + 5
        while not loop.stop_flag().exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(loop.stop_flag().exists())
        time.sleep(0.2)
        self.assertEqual(git.words(), ["rev-parse"])
        self.assertTrue(selfupdate.lock_path().exists())
        living.discard(21)   # the ODP agent ends by itself; the other still runs
        time.sleep(0.2)
        self.assertEqual(git.words(), ["rev-parse"])
        self.assertIsNone(self.row()["result"])
        living.discard(22)
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(out["res"]["result"], "ok")
        self.assertFalse(selfupdate.lock_path().exists())

    def test_integrator_row_reaches_done_while_the_update_waits(self):
        """The integrator starts the update through the command, finishes its own row (done) and the update still waits for the ODP agent."""
        odp = self.agent("ODP", "Odp", 31)
        integ = self.agent("HaiveQueen", "Integ", 32, phase="integrate")
        living = {31, 32}
        git = FakeGit()
        threads = []

        def fake_spawn(live=None):
            """Stands in for the detached process: the worker runs in a thread of its own."""
            def work():
                """The worker with its own connection."""
                conn = db.connect(Path(self.tmp.name) / "haivequeen.db")
                git.conn = conn
                try:
                    selfupdate.run_update(conn, live=self.live, run=git, sleep=lambda s: time.sleep(0.005), poll=0.01, round_timeout=5.0,
                                          alive=lambda pid: pid in living, loop_pid=lambda: 4242 if git.started else 0)
                finally:
                    conn.close()
            t = threading.Thread(target=work)
            threads.append(t)
            t.start()
            return {"how": "detached"}

        with mock.patch.object(selfupdate, "spawn_worker", fake_spawn), mock.patch("builtins.print"):
            caller = threading.Thread(target=lambda: selfupdate.cmd_self_update(argparse.Namespace(worker=False), self.conn))
            caller.start()
            caller.join(5)
            self.assertFalse(caller.is_alive(), "the command must return at once")
        db.finish_agent(self.conn, integ, "Integrated.\nDone.")   # the integrator reports and ends
        living.discard(32)
        time.sleep(0.3)
        self.assertEqual(self.conn.execute("SELECT status FROM agents WHERE id=?", (integ,)).fetchone()[0], "done")
        self.assertEqual(git.words(), ["rev-parse"])   # the update still waits for the ODP agent
        self.assertIsNone(self.row()["result"])
        self.assertEqual(self.conn.execute("SELECT status FROM agents WHERE id=?", (odp,)).fetchone()[0], "running")
        living.discard(31)
        threads[0].join(10)
        self.assertFalse(threads[0].is_alive())
        self.assertEqual(self.row()["result"], "ok")

    def test_running_loop_holds_the_update(self):
        """While a loop process is alive the update waits, even with no agent."""
        git = FakeGit(self.conn)
        busy = [True, True]   # the first two looks find a loop

        def loop_pid():
            """A loop for two looks, then none, then the new one after the start."""
            if git.started:
                return 4242
            return 4242 if busy and busy.pop() else 0
        res = self.update(git, loop_pid=loop_pid)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(busy, [])
        self.assertEqual(git.words()[:2], ["rev-parse", "fetch"])

    def test_second_run_is_refused_by_the_lock(self):
        """With the lock held by a living process the run ends at once: no row, no flag, no command."""
        selfupdate.lock_path().write_text(str(os.getpid()), encoding="ascii")
        git = FakeGit()
        res = self.update(git)
        self.assertEqual(res["result"], "locked")
        self.assertEqual(git.calls, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM self_updates").fetchone()[0], 0)
        self.assertFalse(loop.stop_flag().exists())
        self.assertTrue(selfupdate.lock_path().exists())   # the lock of the other run stays


class UpdateTest(SelfUpdateBase):
    """The update steps and the rollback."""

    def test_restart_keeps_a_pause(self):
        """The restart runs `start --keep-pause`, so a Queen that was paused before the self-update stays paused."""
        git = FakeGit(self.conn)
        self.update(git)
        self.assertEqual(git.start_argv[-2:], ["start", "--keep-pause"])

    def test_happy_path(self):
        """Steps in order, in the live folder, push with the allowed command, no pip without pyproject change, result ok in the row."""
        git = FakeGit(self.conn)
        res = self.update(git)
        self.assertEqual(git.words(), ["rev-parse", "fetch", "merge --ff-only", "rev-parse", "push origin", "diff", "start"])
        self.assertEqual({c[1] for c in git.calls}, {str(self.live)})
        self.assertEqual((res["result"], res["old"], res["new"]), ("ok", OLD, NEW))
        row = self.row()
        self.assertEqual((row["result"], row["old_commit"], row["new_commit"]), ("ok", OLD, NEW))
        self.assertTrue(row["started"] and row["ended"])
        self.assertTrue(loop.stop_flag().exists())   # `start` removes it; the scripted start does not
        self.assertFalse(selfupdate.lock_path().exists())

    def test_push_command_is_the_allowed_one(self):
        """The push is exactly `git -C <live> push origin main`."""
        seen = []
        inner = FakeGit(self.conn)

        def run(argv, cwd):
            """Record the argv, then answer like the scripted runner."""
            seen.append(argv)
            return inner(argv, cwd)
        run.inner = inner
        self.update(run)
        self.assertIn(["git", "-C", str(self.live), "push", "origin", "main"], seen)
        self.assertIn(["git", "-C", str(self.live), "merge", "--ff-only", "origin/v2"], seen)

    def test_pip_only_when_pyproject_changed(self):
        """pip install -e runs when pyproject.toml is in the diff, before the start."""
        git = FakeGit(self.conn, changed="haivequeen/x.py\npyproject.toml")
        self.assertEqual(self.update(git)["result"], "ok")
        self.assertEqual(git.words()[-2:], ["pip", "start"])

    def test_already_current_pushes_nothing(self):
        """When origin/v2 brings nothing new the loop is started again without push or pip, result ok."""
        git = FakeGit(self.conn, target=OLD)
        res = self.update(git)
        self.assertEqual(res["result"], "ok")
        self.assertNotIn("push origin", git.words())
        self.assertEqual(git.words()[-1], "start")

    def test_fetch_failure_changes_nothing_and_restarts(self):
        """A failing fetch is result failed, nothing is reset or pushed and the stopped loop is started again."""
        git = FakeGit(self.conn, fail={"fetch": 1})
        res = self.update(git)
        self.assertEqual(res["result"], "failed")
        self.assertEqual(git.words(), ["rev-parse", "fetch", "start"])
        self.assertEqual(self.row()["result"], "failed")

    def test_push_failure_rolls_back_and_files_the_question(self):
        """A failing push resets to the old commit (reset --keep), starts again, files kind self_update_rollback and ends with result rollback."""
        faults = self.stub_faults()
        git = FakeGit(self.conn, fail={"push": 1})
        res = self.update(git)
        self.assertEqual(res["result"], "rollback")
        self.assertEqual(git.words(), ["rev-parse", "fetch", "merge --ff-only", "rev-parse", "push origin", "diff", "reset --keep", "start"])
        self.assertEqual(git.head, OLD)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][:2], ("HaiveQueen", "self_update_rollback"))
        self.assertIn("push", faults[0][2])
        row = self.row()
        self.assertEqual((row["result"], row["old_commit"], row["new_commit"]), ("rollback", OLD, NEW))

    def test_rollback_reset_uses_the_previous_commit(self):
        """The reset names the commit main had before the update."""
        seen = []
        inner = FakeGit(self.conn, fail={"push": 1})
        self.stub_faults()

        def run(argv, cwd):
            """Record the argv, then answer like the scripted runner."""
            seen.append(argv)
            return inner(argv, cwd)
        run.inner = inner
        self.update(run)
        self.assertIn(["git", "-C", str(self.live), "reset", "--keep", OLD], seen)

    def test_rollback_installs_again_when_pyproject_differed(self):
        """pip install -e runs again after the reset when pyproject.toml differed between the commits."""
        self.stub_faults()
        git = FakeGit(self.conn, changed="pyproject.toml", fail={"push": 1})
        self.assertEqual(self.update(git)["result"], "rollback")
        self.assertEqual(git.words()[-3:], ["reset --keep", "pip", "start"])

    def test_failed_start_rolls_back(self):
        """If the new code does not start, the old commit is restored; when the start fails again too the result is failed."""
        self.stub_faults()
        git = FakeGit(self.conn, fail={"start": 1})
        res = self.update(git)
        self.assertEqual(res["result"], "failed")
        self.assertEqual(git.head, OLD)
        self.assertEqual(git.words().count("start"), 2)

    def test_failed_start_keeps_the_whole_error_text(self):
        """The full output of the failed loop start reaches the note, the event log and the open question, not only its first characters."""
        faults = self.stub_faults()
        inner = FakeGit(self.conn, fail={"start": 1})
        text = "Traceback " + "x" * 600 + " END-OF-ERROR"

        def run(argv, cwd):
            """Answer the loop start with a long error text, everything else like the scripted runner."""
            code, out = inner(argv, cwd)
            return (code, text) if "start" in argv else (code, out)
        run.inner = inner
        self.update(run)
        self.assertIn("END-OF-ERROR", self.row()["note"])
        self.assertIn("END-OF-ERROR", self.conn.execute("SELECT text FROM events WHERE kind='self-update' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertIn("END-OF-ERROR", faults[0][2])

    def test_error_in_first_round_keeps_the_new_code(self):
        """An ERROR line of the rounds log written after the start (a pane that did not start) is no reason for a rollback: result ok, the whole text is logged and filed."""
        faults = self.stub_faults()
        inner = FakeGit(self.conn)

        def run(argv, cwd):
            """After the start of the new loop a round error is logged."""
            res = inner(argv, cwd)
            if inner.calls[-1][0] == "start" and inner.words().count("start") == 1:
                db.add_event(self.conn, "ODP", "rounds", "ROUND ERROR pane HaiveQueen.3 did not start within 20 s " + "x" * 300 + " END-OF-ERROR")
            return res
        run.inner = inner
        res = self.update(run)
        self.assertEqual(res["result"], "ok")
        self.assertNotIn("reset --keep", inner.words())
        self.assertEqual(inner.head, NEW)
        self.assertEqual(faults[0][:2], ("HaiveQueen", "self_update_pane"))
        self.assertIn("END-OF-ERROR", faults[0][2])
        self.assertIn("END-OF-ERROR", self.row()["note"])
        self.assertIn("END-OF-ERROR", self.conn.execute("SELECT text FROM events WHERE kind='self-update' ORDER BY id DESC LIMIT 1").fetchone()[0])

    def test_vanished_in_first_round_keeps_the_new_code(self):
        """A 'vanished' line after the start is reported, not rolled back."""
        self.stub_faults()
        inner = FakeGit(self.conn)

        def run(argv, cwd):
            """After the start of the new loop an agent is marked as vanished."""
            res = inner(argv, cwd)
            if inner.calls[-1][0] == "start" and inner.words().count("start") == 1:
                db.add_event(self.conn, "ODP", "note", "Ada: runner process vanished (ended by executor or crashed), marked failed")
            return res
        run.inner = inner
        self.assertEqual(self.update(run)["result"], "ok")

    def test_old_error_lines_do_not_count(self):
        """An ERROR line from before the update is no reason for a rollback."""
        db.add_event(self.conn, "ODP", "rounds", "ROUND ERROR long ago")
        self.assertEqual(self.update(FakeGit(self.conn))["result"], "ok")

    def test_the_start_does_not_count_beekeeper_tabs(self):
        """The loop has no pane of its own: the first-round check neither asks the terminal nor takes a `panes` argument."""
        import inspect
        self.assertNotIn("panes", inspect.signature(selfupdate.first_round_problems).parameters)
        self.assertNotIn("panes", inspect.signature(selfupdate.run_update).parameters)
        self.assertFalse(hasattr(selfupdate, "queen_panes"))
        self.assertEqual(self.update(FakeGit(self.conn))["result"], "ok")

    def test_no_finished_round_rolls_back(self):
        """A loop that never finishes its first round within the time limit is rolled back."""
        self.stub_faults()
        inner = FakeGit(None)   # the scripted start writes no loop state
        res = self.update(inner, round_timeout=0.1)
        self.assertEqual(res["result"], "rollback")
        self.assertIn("first round", self.row()["note"])

    def test_rollback_without_faults_module_still_reports(self):
        """When haivequeen.faults does not exist (step S4 not merged) the rollback still completes and the note says the question was not filed."""
        with mock.patch.dict(sys.modules, {"haivequeen.faults": None}):
            git = FakeGit(self.conn, fail={"push": 1})
            res = self.update(git)
        self.assertEqual(res["result"], "rollback")
        self.assertIn("question not filed", res["note"])

    def test_reset_failure_is_failed(self):
        """If the reset itself fails the result is failed and nothing is started."""
        self.stub_faults()
        git = FakeGit(self.conn, fail={"push": 1, "reset": 1})
        res = self.update(git)
        self.assertEqual(res["result"], "failed")
        self.assertNotIn("start", git.words())

    def test_faults_module_is_not_part_of_this_step(self):
        """selfupdate imports file_fault only inside the rollback function."""
        lines = Path(selfupdate.__file__).read_text(encoding="utf-8").splitlines()
        self.assertFalse([l for l in lines if l.startswith(("import ", "from ")) and "faults" in l])
        self.assertIn("    from haivequeen.faults import file_fault", lines)


if __name__ == "__main__":
    unittest.main()
