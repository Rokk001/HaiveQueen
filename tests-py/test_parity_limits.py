"""Tests that carry over checks of the old PowerShell limits test that the Python suite did not cover yet."""
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import agent_runner as ar  # noqa: E402
from orchlib import config, db, runtime  # noqa: E402
from test_limits import HOLDER  # noqa: E402


class LiveLockParityTest(unittest.TestCase):
    """A write lock held on the live database never blocks a run on the test runtime."""

    def test_live_lock_held_test_runtime_still_writes(self):
        """Another process holds the write lock of a database in the 'live' folder; a test runtime in its own folder
        (ORCH_DATA_DIR) has a different file and writes at once, well below the busy timeout."""
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live" / "o.db"
            live.parent.mkdir()
            conn = db.connect(live)
            db.upsert_project(conn, "demo", tmp, "FF8800", 0)
            conn.close()
            holder = Path(tmp) / "holder.py"
            holder.write_text(HOLDER, encoding="utf-8")
            p = subprocess.Popen([sys.executable, "-I", str(holder), str(ROOT), str(live), "20"], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "held")
                with mock.patch.dict(os.environ, {"ORCH_DATA_DIR": str(Path(tmp) / "test-data")}):
                    test_db = Path(config.db_path())
                self.assertNotEqual(test_db.resolve(), live.resolve())
                conn = db.connect(test_db)
                db.upsert_project(conn, "demo", tmp, "FF8800", 0)
                t0 = time.monotonic()
                runtime.append_event(conn, "demo", "through", kind="rounds", agent="x")
                took = time.monotonic() - t0
                conn.close()
                self.assertLess(took, 5, "the test runtime waited for the lock of the live database")
            finally:
                p.kill()
                p.communicate()


class RowRetryParityTest(unittest.TestCase):
    """Update retry against a real database: the same three scenarios as the old Update-AgentRetry checks."""

    def setUp(self):
        """A temp database with the demo project and a stand-in for the agent run."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "demo", self.tmp.name, "FF8800", 0)
        runtime.upsert_agent(self.conn, "demo", "LOCKTEST", {"status": "running", "lastLine": "starting"})
        self.me = types.SimpleNamespace(conn=self.conn, project="demo", name="LOCKTEST", event=lambda text, kind="": None)

    def tearDown(self):
        """Close and remove the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    def test_free_write_goes_through_at_once(self):
        """A write that works returns True after one try and the row holds the requested fields."""
        self.assertTrue(ar.AgentRun.set_agent_retry(self.me, {"status": "running", "lastLine": "working"}, attempts=3, delay=0))
        row = runtime.get_agent(self.conn, "demo", "LOCKTEST")
        self.assertEqual(row["last_line"], "working")

    def test_three_failed_writes_leave_a_failed_row_with_reason_and_end_time(self):
        """The first three writes fail, the fourth (the fallback) works: the row is failed with the reason and an end time, not running;
        the retries wait with the default delay between the tries (two pauses of 2 s for three tries)."""
        real = runtime.upsert_agent
        failures = [3]

        def flaky(conn, project, name, props, *args, **kwargs):
            """Fail the first three calls, then write for real."""
            if failures[0] > 0:
                failures[0] -= 1
                raise RuntimeError("database is locked")
            return real(conn, project, name, props, *args, **kwargs)

        sleeps = []
        with mock.patch.object(runtime, "upsert_agent", flaky), mock.patch.object(ar.time, "sleep", sleeps.append):
            self.assertFalse(ar.AgentRun.set_agent_retry(self.me, {"status": "done", "exitCode": 0}, attempts=3))
        self.assertEqual(sleeps, [2.0, 2.0])
        row = runtime.get_agent(self.conn, "demo", "LOCKTEST")
        self.assertEqual(row["status"], "failed")
        self.assertIn("after 3 tries", row["last_line"])
        self.assertTrue(row.get("ended_at") or row.get("endedAt"), row)


if __name__ == "__main__":
    unittest.main()
