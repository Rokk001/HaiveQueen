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
        """Return pending and visible loop faults in this test database."""
        return (runtime.list_questions(self.conn, "HaiveQueen", "fault_check", faults.FAULT_PLAN, "loop_vanished") +
                runtime.list_questions(self.conn, "HaiveQueen", "open", faults.FAULT_PLAN, "loop_vanished"))

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
        self.assertEqual(self.questions()[0]["status"], "fault_check")
        text = "Loop ended unexpectedly (pid 2000000000, last state idle since 2026-01-01T00:00:00)"
        self.assertIn(text, self.questions()[0]["question"])
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
                patch.object(runstate, "settle", side_effect=RuntimeError("settle broke")), \
                patch.object(runstate, "get_state", side_effect=[{"state": "RUNNING"}, RuntimeError("wait refresh broke")]):
            loop.run_loop(self.conn, self.machine, sleep=sleep)
        self.assertEqual(len(calls), 1)
        self.assertIn("ROUND ERROR settle broke", self.log())
        self.assertIn("ROUND ERROR wait refresh broke", self.log())
        self.assertEqual(loop.read_state(self.conn)["status"], "ended")
        self.assertEqual(self.questions(), [])
