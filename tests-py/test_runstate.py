"""Tests of the run state (pause, resume) against a temporary database."""
import json
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import cli, db, runstate  # noqa: E402


class RunStateTest(unittest.TestCase):
    """Run state functions and commands."""

    def setUp(self):
        """Create a temp database."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "haivequeen.db"
        self.conn = db.connect(self.path)

    def tearDown(self):
        """Close and remove the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    def test_pause_blocks_starts(self):
        """pause makes starts_allowed False, resume makes it True again."""
        self.assertTrue(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.pause(self.conn)["state"], "PAUSED")
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.resume(self.conn)["state"], "RUNNING")
        self.assertTrue(runstate.starts_allowed(self.conn))

    def test_pause_keeps_running_agents(self):
        """A pause does not touch a running agent: it keeps running, only new starts are blocked, and the state stays PAUSED."""
        db.add_agent(self.conn, "p", name="A", status="running", pid=1)
        st = runstate.pause(self.conn)
        self.assertEqual((st["state"], st["running_agents"]), ("PAUSED", 1))
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(runstate.pause(self.conn)["state"], "PAUSED")

    def test_request_handoff_flags_only_running_agents(self):
        """pause --now writes the HANDOFF flag into the run folder of every running agent and of no finished one."""
        db.add_agent(self.conn, "p", name="Run", status="running", pid=1)
        db.add_agent(self.conn, "p", name="Old", status="done")
        runs = Path(self.tmp.name) / "runs"
        with mock.patch("haivequeen.platform.runs_dir", return_value=runs):
            self.assertEqual(runstate.request_handoff(self.conn), ["Run"])
            self.assertEqual(runstate.request_handoff(self.conn), [])
        self.assertTrue((runs / "p" / "Run" / "HANDOFF").is_file())
        self.assertFalse((runs / "p" / "Old").exists())

    def test_legacy_drain_values_count_as_a_pause(self):
        """A DRAINING or DRAINED value stored by an older version behaves like PAUSED and can be resumed."""
        for old in ("DRAINING", "DRAINED"):
            self.conn.execute("DELETE FROM kv WHERE project='*' AND key='run-state'")
            self.conn.execute("INSERT INTO kv (project,key,value,updated_at) VALUES ('*','run-state',?,'2026-01-01T00:00:00')", (json.dumps({"state": old, "since": None}),))
            self.assertEqual(runstate.get_state(self.conn)["state"], "PAUSED", old)
            self.assertFalse(runstate.starts_allowed(self.conn), old)
            self.assertEqual(runstate.resume(self.conn)["state"], "RUNNING", old)

    def test_starts_allowed_command_scheduler_states(self):
        """The command reports the scheduler state and permits starts only while running."""
        args = cli.build_parser().parse_args(["starts-allowed"])
        flag = Path(self.tmp.name) / "STOP"
        for pid, stopped, paused, expected in (
                (0, False, False, (False, "STOPPED")),
                (0, True, False, (False, "STOPPED")),
                (123, True, False, (False, "STOPPING")),
                (123, False, True, (False, "PAUSED")),
                (123, False, False, (True, "RUNNING"))):
            with self.subTest(pid=pid, stopped=stopped, paused=paused):
                runstate.pause(self.conn) if paused else runstate.resume(self.conn)
                flag.write_text("stop", encoding="utf-8") if stopped else flag.unlink(missing_ok=True)
                output = io.StringIO()
                with mock.patch("haivequeen.loop.loop_running", return_value=pid), \
                     mock.patch("haivequeen.loop.stop_flag", return_value=flag), \
                     contextlib.redirect_stdout(output):
                    self.assertEqual(args.fn(args, self.conn), 0)
                result = json.loads(output.getvalue())
                self.assertEqual((result["allowed"], result["state"]), expected)

    def test_survives_restart(self):
        """The state is read back through a new connection."""
        db.add_agent(self.conn, "p", name="A", status="running", pid=1)
        runstate.pause(self.conn)
        self.conn.close()
        self.conn = db.connect(self.path)
        st = runstate.get_state(self.conn)
        self.assertEqual((st["state"], st["running_agents"]), ("PAUSED", 1))

    def test_commands(self):
        """The bzz commands print JSON and starts-allowed follows the state."""
        env = dict(os.environ, BZZ_CONFIG_DIR=str(Path(self.tmp.name) / "cfg"),
                   BZZ_DATA_DIR=str(Path(self.tmp.name) / "cmd"))

        def orch(*args):
            """Run haivequeen/cli.py and parse stdout."""
            r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=env,
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return json.loads(r.stdout)

        self.assertEqual(orch("starts-allowed"), {"allowed": False, "state": "STOPPED"})
        self.assertEqual(orch("pause")["state"], "PAUSED")
        self.assertEqual(orch("starts-allowed"), {"allowed": False, "state": "STOPPED"})
        self.assertEqual(orch("run-state")["running_agents"], 0)
        self.assertEqual(orch("resume")["state"], "RUNNING")


    # A recycled PID of a process Windows denies access to (a system service) must not hold the run open.
    def test_inaccessible_process_is_not_counted_as_running(self):
        """A finished agent whose PID now belongs to a process that cannot be opened is not counted; an openable one with unreadable start time still is."""
        from unittest import mock
        from haivequeen import platform as osplat
        db.add_agent(self.conn, "p", name="A", phase="queen", status="done", pid=123, ended_at="2026-01-01T12:00:00")
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_start_time", create=True, return_value=None), \
             mock.patch.object(osplat, "process_inaccessible", create=True) as denied:
            denied.return_value = True
            self.assertEqual(runstate.running_agents(self.conn), 0)
            denied.return_value = False
            self.assertEqual(runstate.running_agents(self.conn), 1)

    # A reused PID must not count as a running agent, while a still-running runner remains protected.
    def test_reused_pid_is_not_counted_as_running(self):
        """Ignore only a process proven to have started after the agent ended."""
        from unittest import mock
        from haivequeen import platform as osplat
        from datetime import datetime
        db.add_agent(self.conn, "p", name="A", phase="queen", status="done", pid=123,
                     ended_at="2026-01-01T12:00:00")
        with mock.patch.object(osplat, "process_alive", return_value=True), \
             mock.patch.object(osplat, "process_inaccessible", create=True, return_value=False), \
             mock.patch.object(osplat, "process_start_time", create=True) as started:
            started.return_value = datetime(2026, 1, 1, 12, 10)
            self.assertEqual(runstate.running_agents(self.conn), 0)
            started.return_value = datetime(2026, 1, 1, 11, 59)
            self.assertEqual(runstate.running_agents(self.conn), 1)
            started.return_value = None
            self.assertEqual(runstate.running_agents(self.conn), 1)
if __name__ == "__main__":
    unittest.main()
