"""Regression tests for PID reuse in Hive scheduling and handoff."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from haivequeen import hives, platform as osplat, runtime
from bzztest import REL, RoundCase


# Verify that a reused process ID cannot block a Hive.
class ReusedPidTests(RoundCase):
    """A finished agent's PID can belong to a different process."""

    # A reused Queen PID must not prevent the next Queen from starting.
    def test_scheduler_ignores_reused_queen_pid(self):
        """A later unrelated process does not count as the old Queen."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("old", phase="queen", status="failed", pid=777,
                   ended=1, interrupted="vanished")
        with mock.patch.object(osplat, "process_start_time", return_value=datetime.now() + timedelta(minutes=1)):
            self.round(alive=lambda pid: pid == 777)
        rows = runtime.list_agents(self.conn, "testproj", "active")
        self.assertTrue(any(a["name"] != "old" and a["phase"] == "queen"
                            and a["status"] in ("starting", "running") for a in rows))

    # A reused Bee PID must not prevent a handoff.
    def test_handoff_ignores_reused_bee_pid(self):
        """A later unrelated process is not a Bee that must finish."""
        self.agent("old-bee", phase="bee", status="done", pid=777,
                   ended=1)
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_start_time", return_value=datetime.now() + timedelta(minutes=1)):
            self.assertTrue(hives.can_handoff(self.conn, "testproj", REL))


    # Starting a replacement must never terminate a process with a reused PID.
    def test_dequeen_does_not_stop_reused_pid(self):
        """Only the old agent is ignored; the unrelated process is untouched."""
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("old-queen", phase="queen", status="done", pid=777, ended=1)
        stop = mock.Mock(return_value=True)
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_start_time", return_value=datetime.now() + timedelta(minutes=1)):
            hives.dequeen(self.conn, "testproj", REL, stop=stop)
        stop.assert_not_called()


# Verify exact process identities and conservative handling of unknown identities.
class ProcessIdentityTests(unittest.TestCase):
    """A PID is paired with the process creation identity."""

    # An existing agent is kept, a different process is rejected.
    def test_birth_identity_distinguishes_processes(self):
        """Matching identities count, mismatching ones never do."""
        from haivequeen import agent_process
        row = {"status": "done", "pid": 777, "processIdentity": "win:123"}
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_identity", return_value="win:123"):
            self.assertTrue(agent_process.is_live(row))
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_identity", return_value="win:456"):
            self.assertFalse(agent_process.is_live(row))

    # If the operating system cannot provide evidence, do not assume a running agent ended.
    def test_unknown_identity_keeps_agent(self):
        """Missing identity information conservatively keeps the process counted."""
        from haivequeen import agent_process
        row = {"status": "running", "pid": 777, "processIdentity": "win:123"}
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_identity", return_value=None), \
             mock.patch.object(osplat, "process_start_time", return_value=None):
            self.assertTrue(agent_process.counts(row))

    # The host provides a stable identity for the currently running process.
    def test_current_process_has_stable_identity(self):
        """Windows and Linux report a repeatable process birth identity."""
        import os
        if not (osplat.is_windows() or osplat.is_linux()):
            self.skipTest("process birth identity is available on Windows and Linux")
        token = osplat.process_identity(os.getpid())
        self.assertTrue(token)
        self.assertEqual(token, osplat.process_identity(os.getpid()))

if __name__ == "__main__":
    unittest.main()