"""Tests of the re-attach after an orchestrator restart."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, reattach  # noqa: E402


class ReattachTest(unittest.TestCase):
    """Living agents stay running, vanished ones fail."""

    def setUp(self):
        """Create a temp database with a living, a vanished and a pid-less invented agent."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "haivequeen.db"
        self.conn = db.connect(self.path)
        db.add_agent(self.conn, "p", name="Live", status="running", pid=111)
        db.add_agent(self.conn, "p", name="Gone", status="running", pid=222)
        db.add_agent(self.conn, "p", name="NoPid", status="running")
        db.add_agent(self.conn, "p", name="Fin", status="done", pid=333)
        db.add_agent(self.conn, "q", name="Other", status="running", pid=444)

    def tearDown(self):
        """Close and remove the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    def status(self, name):
        """Return the status of an agent."""
        return self.conn.execute("SELECT status FROM agents WHERE name=?", (name,)).fetchone()[0]

    def test_reattach(self):
        """Only the project's running agents without a living process fail, with one event each; repeat is a no-op."""
        res = reattach.reattach(self.conn, "p", lambda pid: pid == 111)
        self.assertEqual((res["kept"], res["failed"]), (["Live"], ["Gone", "NoPid"]))
        self.assertEqual([self.status(n) for n in ("Live", "Gone", "NoPid", "Fin", "Other")],
                         ["running", "failed", "failed", "done", "running"])
        row = self.conn.execute("SELECT last_line, ended_at FROM agents WHERE name='Gone'").fetchone()
        self.assertEqual(row[0], reattach.REASON)
        self.assertTrue(row[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='failed'").fetchone()[0], 2)
        self.assertEqual(reattach.reattach(self.conn, "p", lambda pid: pid == 111)["failed"], [])

    def test_command(self):
        """bzz reattach takes the living PIDs from --alive-pids."""
        self.conn.close()
        env = dict(os.environ, BZZ_CONFIG_DIR=str(Path(self.tmp.name) / "cfg"),
                   BZZ_DATA_DIR=str(self.path.parent))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), "reattach", "--project", "p",
                            "--alive-pids", "111,999"], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["kept"], ["Live"])
        self.conn = db.connect(self.path)


if __name__ == "__main__":
    unittest.main()
