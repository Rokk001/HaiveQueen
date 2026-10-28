"""Tests of the pane layout and the pane command lock (the v2 form of the v1 pane tests B and E).

B: only the chief and dashboard panes are open and the first agent starts: its pane splits the dashboard pane, the next agent splits the first one.
E: pane commands come from the loop round (launch_agent) and from `bzz start`; both take pane.lock, so two processes never send them at the same
   time, and of several loops started at the same moment exactly one takes loop.lock.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

from bzztest import RoundCase
from haivequeen import loop
from haivequeen.terminal import pane_title
from haivequeen.terminal.headless import Headless

ONE = [{"id": "P1", "title": "first"}]
SLEEP = [sys.executable, "-c", "import time;time.sleep(60)"]


class RecordingHeadless(Headless):
    """Headless backend that remembers the target and direction of every split."""

    def __init__(self, runs_dir):
        """Start with an empty record."""
        super().__init__(runs_dir)
        self.splits = []

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
        """Record the split, then do it."""
        self.splits.append((target, direction))
        return super().split_pane(window, title, colour, command, direction, size, target, name)


class PaneLayoutTest(RoundCase):
    """Test B: chief and dashboard panes are open, no agent runs, an agent starts."""

    def setUp(self):
        """A dummy runner that sets the pid of its agent row, a recording headless terminal with the chief and the dashboard pane."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        self.plan("IN_PROGRESS", ONE)
        self.seed()
        dummy = self.base / "dummy_runner.py"
        dummy.write_text(
            "import os, sys, time\nsys.path.insert(0, %r)\nfrom haivequeen import config, db, runtime\n"
            "conn = db.connect(config.db_path())\nruntime.upsert_agent(conn, sys.argv[1], sys.argv[2], {'pid': os.getpid(), 'status': 'running'})\ntime.sleep(30)\n"
            % str(loop.TOOLS_DIR), encoding="utf-8")
        self.orig = loop.runner_argv
        loop.runner_argv = lambda env, spec: [sys.executable, str(dummy), env.name, spec["name"]]
        self.term = RecordingHeadless(self.base / "panes")
        self.chief = self.term.open_window("Orchestrator", pane_title("all projects", "CHIEF", "Orchestrator", "m", True), SLEEP, name="chief")
        self.dash = self.term.split_pane("Orchestrator", pane_title("all projects", "Overview", "Dashboard", "-", True), "white", SLEEP,
                                         "vertical", 0.8, self.chief, "dashboard")
        self.term.splits.clear()

    def tearDown(self):
        """End every process of the panes."""
        loop.runner_argv = self.orig
        for p in self.term.list_panes("Orchestrator"):
            if p.alive:
                self.term.close_pane(p.id)
        super().tearDown()

    def test_first_agent_splits_dashboard_next_splits_first(self):
        """The first agent pane is a split of the dashboard pane (beside it); the second agent splits the first agent pane; all panes stay open."""
        self.round(terminal=self.term, poll=0.2, tries=100)
        first = self.ag("DUMMY-P1-work")
        self.assertEqual(self.term.splits, [(self.dash, "horizontal")])
        self.assertEqual(first["status"], "running")
        spec = {"name": "DUMMY-P2-work", "person": "Zed", "model_long": "Claude Sonnet 5.5 medium"}
        loop.launch_agent(self.envs()[0], self.term, "Orchestrator", spec, settle=0, poll=0.2, tries=100)
        second = self.ag("DUMMY-P2-work")
        self.assertEqual(self.term.splits[-1], (first["pane_id"], "horizontal"))
        ids = [p.id for p in self.term.list_panes("Orchestrator") if p.alive]
        self.assertEqual(sorted(ids), sorted([self.chief, self.dash, first["pane_id"], second["pane_id"]]))
        self.assertEqual(len(set(ids)), 4)

    def test_without_dashboard_the_chief_pane_is_split(self):
        """With the dashboard pane gone the first agent pane splits the chief pane."""
        self.term.close_pane(self.dash)
        loop.launch_agent(self.envs()[0], self.term, "Orchestrator",
                          {"name": "DUMMY-P1-work", "person": "Max", "model_long": "Claude Sonnet 5.5 medium"}, settle=0, poll=0.2, tries=100)
        self.assertEqual(self.term.splits, [(self.chief, "horizontal")])


HOLD = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom haivequeen import loop\n"
        "with loop.pane_lock():\n    open(sys.argv[2], 'a').write('in %s\\n' % sys.argv[3])\n    time.sleep(0.6)\n    open(sys.argv[2], 'a').write('out %s\\n' % sys.argv[3])\n")
TAKE = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom haivequeen import loop\n"
        "res = loop._take_pid_lock(loop.lock_file(), own_ok=True)\nprint(res, flush=True)\ntime.sleep(2.5)\n")


class PaneLockTest(RoundCase):
    """Test E: pane commands and the loop lock across processes."""

    def run_procs(self, code, count, extra=()):
        """Start `count` Python processes at once with the given code; returns their outputs."""
        procs = [subprocess.Popen([sys.executable, "-c", code, str(loop.TOOLS_DIR)] + [str(a) for a in extra] + [str(i)],
                                  stdout=subprocess.PIPE, text=True, env=os.environ.copy()) for i in range(count)]
        return [p.communicate(timeout=60)[0].strip() for p in procs]

    def test_two_processes_send_pane_commands_one_after_the_other(self):
        """Two processes that both hold pane.lock never overlap: in, out, in, out."""
        log = self.base / "pane.log"
        self.run_procs(HOLD, 2, [log])
        words = [line.split()[0] for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(words, ["in", "out", "in", "out"])
        self.assertFalse(loop.pane_lock_file().exists(), "the lock is released")

    def test_only_one_of_several_loops_takes_the_loop_lock(self):
        """Six processes try to take loop.lock at the same moment: exactly one gets it, the others see its pid."""
        results = self.run_procs(TAKE, 6)
        self.assertEqual(results.count("0"), 1, results)
        holders = {r for r in results if r != "0"}
        self.assertEqual(len(holders), 1)
        self.assertTrue(int(holders.pop()) > 0)

    def test_lock_of_a_dead_process_is_taken_over(self):
        """A lock file that names a process that is gone does not block."""
        loop.pane_lock_file().parent.mkdir(parents=True, exist_ok=True)
        loop.pane_lock_file().write_text("2000000000", encoding="ascii")
        with loop.pane_lock(timeout=5):
            self.assertEqual(loop.pane_lock_file().read_text(encoding="ascii"), str(os.getpid()))
        self.assertFalse(loop.pane_lock_file().exists())

    def test_pane_lock_times_out_while_another_process_holds_it(self):
        """A living holder keeps the lock: the second caller raises TimeoutError instead of sending its pane command."""
        holder = subprocess.Popen(SLEEP)
        try:
            loop.pane_lock_file().parent.mkdir(parents=True, exist_ok=True)
            loop.pane_lock_file().write_text(str(holder.pid), encoding="ascii")
            with self.assertRaises(TimeoutError):
                with loop.pane_lock(timeout=0.5, poll=0.05):
                    self.fail("must not get the lock")
        finally:
            holder.kill()
            holder.wait()

    def start(self):
        """Call `bzz start` (window mode, headless backend) in this process and return its JSON output."""
        args = argparse.Namespace(foreground=False, terminal="headless", rounds=0, round_minutes=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.cmd_start(args, self.conn)
        return json.loads(buf.getvalue())

    def test_second_start_does_not_open_a_second_window(self):
        """With a loop running, or a window start pending, `bzz start` opens no pane."""
        holder = subprocess.Popen(SLEEP)
        try:
            loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
            loop.lock_file().write_text(str(holder.pid), encoding="ascii")
            self.assertTrue(self.start()["running"])
        finally:
            holder.kill()
            holder.wait()
        loop.lock_file().unlink()
        loop.start_pending_file().write_text("1", encoding="ascii")
        self.assertTrue(self.start()["starting"])
        self.assertFalse((loop.osplat.runs_dir() / "_panes").exists(), "no pane was opened")


if __name__ == "__main__":
    unittest.main()
