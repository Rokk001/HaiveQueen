"""Tests of the limits that replaced the v1 mutex and time limit scenarios (test-limits.ps1) on the SQLite runtime.

Mutex (v1: one process holds the agents lock, a second one must give up instead of waiting forever; test and live lock names differ):
  v2: several processes write into the same database at once without losing or breaking state; a writer blocked by another
  process gives up after the busy timeout; a test runtime never touches the live database.
Time limit (v1: a stuck test or command is ended after the limit, the whole process tree):
  v2: a hanging command (event hook, brain) is ended after its timeout with its whole process tree; a hanging agent is ended
  after the stall limit and its row ends as failed; an agent row never stays on running when the database write fails.
"""
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
from orchlib import brain, db, events, runtime  # noqa: E402
from orchlib import platform as osplat  # noqa: E402
from test_agent_runner import RunnerBase  # noqa: E402

WRITER = '''import sys
sys.path.insert(0, sys.argv[1])
from orchlib import db, runtime
conn = db.connect(sys.argv[2])
for i in range(int(sys.argv[4])):
    runtime.append_event(conn, "demo", "writer %s line %d" % (sys.argv[3], i), kind="rounds", agent="w" + sys.argv[3])
    runtime.upsert_agent(conn, "demo", "W" + sys.argv[3], {"status": "running", "lastLine": "line %d" % i})
'''

HOLDER = '''import sys, time
sys.path.insert(0, sys.argv[1])
from orchlib import db
conn = db.connect(sys.argv[2])
conn.execute("BEGIN IMMEDIATE")
print("held", flush=True)
time.sleep(float(sys.argv[3]))
conn.execute("ROLLBACK")
'''

# Starts a child that writes its pid and sleeps, then sleeps itself: the command hangs with a grandchild behind it.
HANGING = '''import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time,sys,os; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(300)", sys.argv[1]])
time.sleep(300)
'''


def wait_gone(pid_file: Path, seconds: float = 10.0) -> bool:
    """True when the process whose pid is in the file is gone within the time (False when it never wrote its pid or still lives)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pid_file.is_file() and pid_file.read_text().strip():
            pid = int(pid_file.read_text().strip())
            if not osplat.process_alive(pid):
                return True
        time.sleep(0.2)
    return False


class MutexTest(unittest.TestCase):
    """Several processes on one database (the v2 form of the agents lock tests)."""

    def setUp(self):
        """A temp database with the demo project."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "o.db"
        conn = db.connect(self.path)
        db.upsert_project(conn, "demo", self.tmp.name, "FF8800", 0)
        conn.close()
        self.script = Path(self.tmp.name) / "writer.py"
        self.script.write_text(WRITER, encoding="utf-8")

    def tearDown(self):
        """Remove the temp database."""
        self.tmp.cleanup()

    def test_parallel_writers_lose_nothing(self):
        """Six processes write events and agent rows at the same time: every line arrives, the file passes the integrity check."""
        procs = [subprocess.Popen([sys.executable, "-I", str(self.script), str(ROOT), str(self.path), str(n), "40"],
                                  stderr=subprocess.PIPE, text=True) for n in range(6)]
        errors = [p.communicate(timeout=120)[1] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0] * 6, errors)
        conn = db.connect(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE text LIKE 'writer %'").fetchone()[0], 6 * 40)
        for n in range(6):
            self.assertEqual(runtime.get_agent(conn, "demo", "W%d" % n)["last_line"], "line 39")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_blocked_writer_gives_up_after_the_limit(self):
        """Another process holds the write lock for 8 s: a writer with a 1 s busy timeout fails after about 1 s, it neither hangs nor succeeds."""
        holder = Path(self.tmp.name) / "holder.py"
        holder.write_text(HOLDER, encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-I", str(holder), str(ROOT), str(self.path), "8"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            with mock.patch.object(db, "BUSY_TIMEOUT_MS", 1000):
                conn = db.connect(self.path)
            t0 = time.monotonic()
            with self.assertRaises(Exception) as ctx:
                runtime.append_event(conn, "demo", "blocked", kind="rounds", agent="x")
            took = time.monotonic() - t0
            conn.close()
            self.assertIn("locked", str(ctx.exception))
            self.assertGreaterEqual(took, 0.8, "the writer must wait for the limit first")
            self.assertLess(took, 6, "the writer must give up long before the holder lets go")
        finally:
            p.kill()
            p.communicate()

    def test_test_runtime_is_separate_from_live(self):
        """With ORCH_DATA_DIR set the database path lies inside that folder, so a lock on the live database cannot reach a test."""
        from orchlib import config
        with mock.patch.dict(os.environ, {"ORCH_DATA_DIR": self.tmp.name}):
            self.assertEqual(Path(config.db_path()).parent.resolve(), Path(self.tmp.name).resolve())
            inside = Path(config.db_path()).resolve()
        self.assertNotEqual(inside, Path(osplat.data_dir()).resolve() / "orchestrator.db")


class CommandTimeoutTest(unittest.TestCase):
    """A hanging command is ended after its limit together with everything it started."""

    def setUp(self):
        """A temp folder with the hanging script."""
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "hang.py"
        self.script.write_text(HANGING, encoding="utf-8")
        self.pid_file = self.dir / "child.pid"

    def tearDown(self):
        """Remove the temp folder (a leftover grandchild is ended first)."""
        if self.pid_file.is_file() and self.pid_file.read_text().strip():
            osplat.kill_process_tree(int(self.pid_file.read_text().strip()))
        self.tmp.cleanup()

    def test_event_command_timeout_ends_the_tree(self):
        """run_command reports TimedOut after the limit and the grandchild of the command is gone."""
        t0 = time.monotonic()
        res = events.run_command([sys.executable, str(self.script), str(self.pid_file)], self.dir, 3)
        self.assertTrue(res["TimedOut"])
        self.assertLess(time.monotonic() - t0, 30)
        self.assertTrue(wait_gone(self.pid_file), "the grandchild of the timed out command still runs")

    def test_brain_timeout_ends_the_tree(self):
        """The brain program that hangs is ended after its timeout with its grandchild and reported as timed out."""
        code, out, err, timed_out = brain._run([sys.executable, str(self.script), str(self.pid_file)], "prompt", str(self.dir), 3)
        self.assertTrue(timed_out)
        self.assertTrue(wait_gone(self.pid_file), "the grandchild of the timed out brain still runs")


class StallTest(RunnerBase):
    """A hanging agent (alive but silent) is ended after the stall limit."""

    def test_silent_agent_is_ended_and_failed(self):
        """The fake CLI prints the start of a stream and then hangs for 300 s: with a 4 s stall limit the runner ends it, the row is failed with the reason."""
        self.write_config('agentStallSeconds = 4')
        self.prepare("S-1")
        t0 = time.monotonic()
        r = self.run_runner("S-1", "claude", '{"type": "system", "subtype": "init"}\n', env={"FAKE_HANG": "300"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.monotonic() - t0, 60, "the runner waited for the hanging agent")
        row = self.row("S-1")
        self.assertEqual(row["status"], "failed")
        self.assertIn("stalled", row["last_line"])
        self.assertIn("4", row["last_line"])

    def test_stall_limit_off(self):
        """agentStallSeconds = 0 switches the watch off: a slow agent that finishes by itself is still done."""
        self.write_config('agentStallSeconds = 0')
        self.prepare("S-2")
        from test_agent_runner import claude_stream
        r = self.run_runner("S-2", "claude", claude_stream(), env={"FAKE_HANG": "3"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row("S-2")["status"], "done")


class RowNeverStaysRunningTest(unittest.TestCase):
    """The v1 'agents lock error after 3 tries' scenario: a failing database write never leaves the row on running."""

    def test_three_failures_set_the_row_to_failed(self):
        """Three failed writes end with a failed row that names the reason; a write that works returns True at once."""
        calls = []
        failures = [3]

        def fake_upsert(conn, project, name, props):
            """Fail the first three calls, record every call."""
            calls.append(dict(props))
            if failures[0] > 0:
                failures[0] -= 1
                raise RuntimeError("database is locked")

        me = types.SimpleNamespace(conn=None, project="demo", name="X", events=[])
        me.event = lambda text, kind="": me.events.append(text)
        with mock.patch.object(runtime, "upsert_agent", fake_upsert):
            self.assertFalse(ar.AgentRun.set_agent_retry(me, {"status": "done"}, attempts=3, delay=0))
            self.assertEqual(calls[-1]["status"], "failed")
            self.assertIn("after 3 tries", calls[-1]["lastLine"])
            calls.clear()
            self.assertTrue(ar.AgentRun.set_agent_retry(me, {"status": "running"}, attempts=3, delay=0))
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
