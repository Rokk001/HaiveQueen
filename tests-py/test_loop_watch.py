"""A loop that ended unexpectedly is reported and restarted while it is down, not only at the next manual start."""
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from bzztest import RoundCase, runtime
from haivequeen import faults, loop, reattach, web_service


class LoopWatchTest(RoundCase):
    def setUp(self):
        super().setUp()
        self.add_project("HaiveQueen")
        loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
        loop.lock_file().write_text("2000000000", encoding="ascii")   # a killed loop leaves its lock behind
        loop.write_state(self.conn, "idle", datetime(2026, 1, 1), None)
        boot = patch.object(loop, "system_boot_time", return_value=None)
        boot.start()
        self.addCleanup(boot.stop)

    def faults(self):
        return [f for f in faults.global_faults(self.conn) if f["kind"] == "loop_vanished"]

    def test_vanished_loop_is_reported_once_and_restarted(self):
        restart = Mock()
        self.assertEqual(loop.watch_loop(self.conn, restart), "restarted")
        self.assertEqual(loop.watch_loop(self.conn, restart), "restarted")
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(len(self.faults()), 1)
        self.assertIn("Loop ended unexpectedly (pid 2000000000", self.faults()[0]["text"])
        self.assertTrue(any("pid 2000000000" in e["text"] for e in runtime.list_events(self.conn, "HaiveQueen")))

    def test_restart_limit_leaves_the_fault_for_the_owner(self):
        restart = Mock()
        now = datetime(2026, 11, 17, 12, 0)
        results = [loop.watch_loop(self.conn, restart, now + timedelta(minutes=i)) for i in range(loop.LOOP_RESTART_LIMIT + 1)]
        self.assertEqual(results[-1], "reported")
        self.assertEqual(restart.call_count, loop.LOOP_RESTART_LIMIT)
        self.assertEqual(loop.watch_loop(self.conn, restart, now + timedelta(hours=2)), "restarted")   # an hour later it tries again

    def test_stop_clean_end_and_living_loop_are_no_unexpected_end(self):
        restart = Mock()
        loop.stop_flag().write_text("stop requested", encoding="utf-8")
        self.assertEqual(loop.watch_loop(self.conn, restart), "")
        loop.stop_flag().unlink()
        loop.write_state(self.conn, "ended", datetime(2026, 1, 1), None)
        self.assertEqual(loop.watch_loop(self.conn, restart), "")
        loop.write_state(self.conn, "idle", datetime(2026, 1, 1), None)
        with patch.object(loop.osplat, "process_alive", return_value=True):
            self.assertEqual(loop.watch_loop(self.conn, restart), "")
        loop.lock_file().unlink()
        self.assertEqual(loop.watch_loop(self.conn, restart), "")
        restart.assert_not_called()
        self.assertEqual(self.faults(), [])

    def test_restarted_loop_does_not_report_the_same_end_again(self):
        loop.watch_loop(self.conn, Mock())
        with patch.object(loop, "_run_loop", return_value=0):
            loop.run_loop(self.conn, self.machine)
        self.assertEqual(len(self.faults()), 1)

    def test_agent_of_the_ended_loop_is_failed_by_the_restarted_loop(self):
        runtime.upsert_agent(self.conn, "HaiveQueen", "Ada-69", {"status": "running", "phase": "bee", "pid": 2000000001})
        loop.watch_loop(self.conn, Mock())
        reattach.reattach(self.conn, "HaiveQueen", lambda pid: False)   # what the restarted loop does first
        self.assertNotEqual(runtime.get_agent(self.conn, "HaiveQueen", "Ada-69")["status"], "running")

    def test_web_service_watch_never_raises(self):
        with patch.object(loop, "watch_loop", side_effect=RuntimeError("broken")):
            web_service._watch_loop()
