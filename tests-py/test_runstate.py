"""Tests of the run state (pause, resume, drain) against a temporary database."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import db, runstate  # noqa: E402


class RunStateTest(unittest.TestCase):
    """Run state functions and commands."""

    def setUp(self):
        """Create a temp database."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "orchestrator.db"
        self.conn = db.connect(self.path)

    def tearDown(self):
        """Close and remove the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    def drained_events(self):
        """Return the number of `drained` events."""
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='drained'").fetchone()[0]

    def test_pause_blocks_starts(self):
        """pause makes starts_allowed False, resume makes it True again."""
        self.assertTrue(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.pause(self.conn)["state"], "PAUSED")
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.resume(self.conn)["state"], "RUNNING")
        self.assertTrue(runstate.starts_allowed(self.conn))

    def test_drain_waits_for_agents(self):
        """drain stays DRAINING while an agent runs; one `drained` event when the last one ends."""
        db.add_agent(self.conn, "p", name="A", status="running", pid=1)
        self.assertEqual(runstate.drain(self.conn)["state"], "DRAINING")
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.settle(self.conn)["state"], "DRAINING")
        self.assertEqual(self.drained_events(), 0)
        self.conn.execute("UPDATE agents SET status='done'")
        self.assertEqual(runstate.settle(self.conn)["state"], "DRAINED")
        runstate.settle(self.conn)
        runstate.get_state(self.conn)
        self.assertEqual(self.drained_events(), 1)
        self.assertFalse(runstate.starts_allowed(self.conn))

    def test_drain_without_agents_and_transitions(self):
        """drain with no agent drains at once; pause after drain gives PAUSED; drain from DRAINED does nothing."""
        self.assertEqual(runstate.drain(self.conn)["state"], "DRAINED")
        self.assertEqual(runstate.drain(self.conn)["state"], "DRAINED")
        self.assertEqual(self.drained_events(), 1)
        self.assertEqual(runstate.pause(self.conn)["state"], "PAUSED")
        self.assertEqual(runstate.drain(self.conn)["state"], "DRAINED")
        self.assertEqual(runstate.resume(self.conn)["state"], "RUNNING")

    def test_survives_restart(self):
        """The state is read back through a new connection."""
        db.add_agent(self.conn, "p", name="A", status="running", pid=1)
        runstate.drain(self.conn)
        self.conn.close()
        self.conn = db.connect(self.path)
        st = runstate.get_state(self.conn)
        self.assertEqual((st["state"], st["running_agents"]), ("DRAINING", 1))

    def test_commands(self):
        """The orch commands print JSON and starts-allowed follows the state."""
        env = dict(os.environ, ORCH_CONFIG_DIR=str(Path(self.tmp.name) / "cfg"),
                   ORCH_DATA_DIR=str(Path(self.tmp.name) / "cmd"))

        def orch(*args):
            """Run orch.py and parse stdout."""
            r = subprocess.run([sys.executable, "-I", str(ROOT / "orch.py"), *args], env=env,
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return json.loads(r.stdout)

        self.assertEqual(orch("starts-allowed"), {"allowed": True, "state": "RUNNING"})
        self.assertEqual(orch("pause")["state"], "PAUSED")
        self.assertEqual(orch("starts-allowed"), {"allowed": False, "state": "PAUSED"})
        self.assertEqual(orch("run-state")["running_agents"], 0)
        self.assertEqual(orch("drain")["state"], "DRAINED")
        self.assertEqual(orch("resume")["state"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
