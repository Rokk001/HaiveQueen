"""Unexpected loop ends leave evidence; normal restarts do not file faults."""
from datetime import datetime
from unittest.mock import patch

from bzztest import RoundCase, runtime
from haivequeen import faults, loop, runstate


# Exercise loop ownership, crash reporting and guarded wait updates.
class LoopEndTest(RoundCase):
    """Exercise loop ownership, crash reporting and guarded wait updates."""

    # Register the fault owner in the isolated runtime.
    def setUp(self):
        """Register the fault owner in the isolated runtime."""
        super().setUp()
        self.add_project("HaiveQueen")

    # Return the loop faults in this test database.
    def questions(self):
        """Return the shown loop faults in this test database; a vanished loop belongs to no Apiary, so it is never an inbox question."""
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen", None, faults.FAULT_PLAN, "loop_vanished"), [])
        return [f for f in faults.global_faults(self.conn) if f["kind"] == "loop_vanished"]

    # A dead loop with no STOP and no clean end files one fault on takeover.
    def test_stale_lock_files_one_fault(self):
        """A dead loop with no STOP and no clean end files one fault on takeover."""
        loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
        loop.lock_file().write_text("2000000000", encoding="ascii")
        loop.write_state(self.conn, "idle", datetime(2026, 1, 1), None)
        with patch.object(loop, "system_boot_time", return_value=None), patch.object(loop, "_run_loop", return_value=0):
            loop.run_loop(self.conn, self.machine)
            loop.run_loop(self.conn, self.machine)
        self.assertEqual(len(self.questions()), 1)
        text = "Loop ended unexpectedly (pid 2000000000, last state idle since 2026-01-01T00:00:00)"
        self.assertIn(text, self.questions()[0]["text"])
        self.assertIn(text, self.log("HaiveQueen"))
        self.assertTrue(any(text in e["text"] for e in runtime.list_events(self.conn, "HaiveQueen")))

    # A boot after the last loop heartbeat explains the stale lock.
    def test_system_restart_does_not_report_a_vanished_loop(self):
        """A boot after the last loop heartbeat explains the stale lock."""
        loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
        loop.lock_file().write_text("2000000000", encoding="ascii")
        loop.write_state(self.conn, "idle", datetime(2026, 1, 1), None)
        with patch.object(loop, "system_boot_time", return_value=datetime(2026, 1, 2)), patch.object(loop, "_run_loop", return_value=0):
            loop.run_loop(self.conn, self.machine)
        self.assertEqual(self.questions(), [])

    # A clean loop records ended and restarting, even with its old lock, files nothing.
    def test_clean_end_and_restart_no_fault(self):
        """A clean loop records ended and restarting, even with its old lock, files nothing."""
        with patch.object(loop, "run_round"), patch.object(loop, "daily_export"):
            loop.run_loop(self.conn, self.machine, rounds=1)
            self.assertEqual(loop.read_state(self.conn)["status"], "ended")
            loop.lock_file().write_text("2000000000", encoding="ascii")
            loop.run_loop(self.conn, self.machine, rounds=1)
        self.assertEqual(self.questions(), [])
        self.assertFalse(loop.lock_file().exists())

    # An initial refresh exception is logged, filed and re-raised after releasing the lock.
    def test_refresh_crash_file_and_lock_cleanup(self):
        """An initial refresh exception is logged, filed and re-raised after releasing the lock."""
        with patch.object(loop, "run_round"), patch.object(loop, "daily_export"), \
                patch.object(runstate, "get_state", side_effect=RuntimeError("refresh broke")), \
                patch.object(loop.faulthandler, "enable") as enable, \
                patch.object(loop.faulthandler, "disable"):
            with self.assertRaisesRegex(RuntimeError, "refresh broke"):
                loop.run_loop(self.conn, self.machine, rounds=1)
            handle = enable.call_args_list[0].kwargs["file"]
        files = list(loop.osplat.data_dir().glob("crash-*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(str(files[0]), handle.name)
        self.assertTrue(handle.closed)
        self.assertIn("Traceback", files[0].read_text(encoding="utf-8"))
        self.assertIn("refresh broke", self.log("HaiveQueen"))
        self.assertEqual(len(self.questions()), 1)
        self.assertFalse(loop.lock_file().exists())
        self.assertNotEqual(loop.read_state(self.conn)["status"], "ended")

    # Both wait operations log transient failures and the loop can still end cleanly.
    def test_wait_failures_do_not_stop_loop(self):
        """Both wait operations log transient failures and the loop can still end cleanly."""
        calls = []

        # Request STOP after entering the round wait.
        def sleep(_seconds):
            """Request STOP after entering the round wait."""
            calls.append(True)
            loop.stop_flag().write_text("stop", encoding="ascii")

        with patch.object(loop, "run_round"), patch.object(loop, "daily_export"), \
                patch.object(loop.usage, "refresh_claude_usage", side_effect=RuntimeError("usage broke")), \
                patch.object(runstate, "get_state", side_effect=[{"state": "RUNNING"}, RuntimeError("wait refresh broke")]):
            loop.run_loop(self.conn, self.machine, sleep=sleep)
        self.assertEqual(len(calls), 1)
        self.assertIn("ROUND ERROR usage broke", self.log())
        self.assertIn("ROUND ERROR wait refresh broke", self.log())
        self.assertEqual(loop.read_state(self.conn)["status"], "ended")
        self.assertEqual(self.questions(), [])

    def test_version_switch_releases_loop_without_waiting_for_agents(self):
        with patch.object(loop, "version_switch_requested", side_effect=[False, True, True, True]), \
                patch.object(loop, "run_round"), patch.object(loop, "daily_export"), \
                patch.object(loop, "active_count", return_value=1), \
                patch.object(loop, "spawn_detached") as spawn:
            loop.run_loop(self.conn, self.machine, sleep=lambda _: None)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(loop.read_state(self.conn)["status"], "ended")
        self.assertFalse(loop.lock_file().exists())

    def test_successor_starts_after_lock_release_and_keeps_pause(self):
        calls = []
        def start(argv, log):
            self.assertFalse(loop.lock_file().exists())
            calls.append(argv)
        with patch.object(loop, "_run_loop", return_value=loop.VERSION_HANDOVER), \
                patch.object(loop, "spawn_detached", side_effect=start):
            self.assertEqual(loop.run_loop(self.conn, self.machine), 0)
        self.assertEqual(calls[0][-3:], ["start", "--foreground", "--keep-pause"])
        self.assertEqual(calls[0][-4], str(loop.TOOLS_DIR.parent.parent / "launcher.py"))

    def test_live_jobs_survive_switch_and_are_reattached_by_successor(self):
        import subprocess
        import sys
        from haivequeen import db
        from haivequeen import orchestrator, proctree

        root = self.base / "install"
        old, new = root / "versions" / "old", root / "versions" / "new"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        pointer = root / "active-version"
        pointer.write_text("old", encoding="utf-8")
        jobs = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"]) for _ in range(2)]
        try:
            for name, phase, job in zip(("Queen", "Agent"), ("queen", "bee"), jobs):
                db.add_agent(self.conn, "testproj", name=name, phase=phase, status="running", pid=job.pid)
            seen = []
            def old_round(*args, **kwargs):
                self.assertTrue(all(job.poll() is None for job in jobs))
                pointer.write_text("new", encoding="utf-8")
            def new_round(*args, **kwargs):
                seen.extend(self.conn.execute("SELECT name, status, pid FROM agents WHERE project='testproj' ORDER BY name").fetchall())
                self.assertTrue(all(job.poll() is None for job in jobs))
            with patch.object(proctree, "sweep"), patch.object(loop, "daily_export"), \
                    patch.object(loop, "spawn_detached") as spawn, patch.object(loop, "TOOLS_DIR", old), \
                    patch.object(loop, "run_round", side_effect=old_round), \
                    patch.object(loop, "active_count", side_effect=AssertionError("handover waited for jobs")):
                loop.run_loop(self.conn, self.machine, rounds=1)
            self.assertEqual(spawn.call_count, 1)
            self.assertFalse(loop.lock_file().exists())
            with patch.object(proctree, "sweep"), patch.object(loop, "daily_export"), \
                    patch.object(loop, "TOOLS_DIR", new), patch.object(loop, "run_round", side_effect=new_round), \
                    patch.object(loop, "active_count", return_value=0), patch.object(orchestrator, "running_jobs", return_value=False):
                loop.run_loop(self.conn, self.machine, rounds=1)
            self.assertEqual([(r[0], r[1]) for r in seen], [("Agent", "running"), ("Queen", "running")])
            self.assertEqual({r[2] for r in seen}, {job.pid for job in jobs})
        finally:
            for job in jobs:
                job.terminate()
            for job in jobs:
                job.wait(timeout=10)

    def test_active_pointer_selects_when_old_loop_yields(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "versions" / "old"
            old.mkdir(parents=True)
            pointer = root / "active-version"
            pointer.write_text("old\n", encoding="utf-8")
            with patch.object(loop, "TOOLS_DIR", old):
                self.assertFalse(loop.version_switch_requested())
                pointer.write_text("new\n", encoding="utf-8")
                self.assertTrue(loop.version_switch_requested())
