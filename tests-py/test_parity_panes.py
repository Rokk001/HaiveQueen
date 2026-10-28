"""Checks of the old PowerShell pane test (A, C, D) that the Python pane tests did not cover yet.

A: no live pane -> the window is opened, nothing is split.
C: only a running agent pane is a split target; the pane of an ended agent (about to close) is never targeted.
D: panes that are no longer alive (stale records) are not trusted: the window is opened again.
Headless backend only, no real terminal window, no model.
"""
import os
import sys
import unittest

from bzztest import RoundCase
from haivequeen import loop
from haivequeen.terminal import pane_title
from test_panes import ONE, SLEEP, RecordingHeadless


class OpenRecordingHeadless(RecordingHeadless):
    """Headless backend that also remembers every window it opens."""

    def __init__(self, runs_dir):
        """Start with an empty record of opened windows."""
        super().__init__(runs_dir)
        self.opens = []

    def open_window(self, window, title, command, colour=None, name=None):
        """Record the window start, then do it."""
        self.opens.append(window)
        return super().open_window(window, title, command, colour, name)


class ParityPanesTest(RoundCase):
    """Pane targets of launch_agent in a temp runtime with a dummy runner."""

    def setUp(self):
        """A dummy runner that sets the pid of its agent row and an empty recording headless terminal."""
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
        self.term = OpenRecordingHeadless(self.base / "panes")

    def tearDown(self):
        """End every process of the panes."""
        loop.runner_argv = self.orig
        for p in self.term.list_panes("Orchestrator"):
            if p.alive:
                self.term.close_pane(p.id)
        super().tearDown()

    def launch(self, name):
        """Launch the pane of one agent through launch_agent and return its pane id."""
        spec = {"name": name, "person": "Max", "model_long": "Claude Sonnet 5.5 medium"}
        return loop.launch_agent(self.envs()[0], self.term, "Orchestrator", spec, settle=0, poll=0.2, tries=100)

    def test_no_live_pane_opens_the_window(self):
        """Test A: without any pane the agent pane opens the window; focus or split of a missing pane is never sent."""
        self.launch("DUMMY-P1-work")
        self.assertEqual(self.term.opens, ["Orchestrator"])
        self.assertEqual(self.term.splits, [])

    def test_stale_pane_records_open_the_window_again(self):
        """Test D: the only recorded pane is dead (its process is gone): it is not a split target, the window is opened."""
        stale = self.term.open_window("Orchestrator", pane_title("all projects", "CHIEF", "Orchestrator", "m", True), SLEEP, name="chief")
        self.term.close_pane(stale)
        self.term.opens.clear()
        self.launch("DUMMY-P1-work")
        self.assertEqual(self.term.opens, ["Orchestrator"])
        self.assertEqual(self.term.splits, [])
        self.assertNotIn(stale, [p.id for p in self.term.list_panes("Orchestrator") if p.alive])

    def test_only_a_running_agent_pane_is_the_split_target(self):
        """Test C: the running agent pane is split; the wider pane of an ended agent, which is about to close, is never targeted."""
        chief = self.term.open_window("Orchestrator", pane_title("all projects", "CHIEF", "Orchestrator", "m", True), SLEEP, name="chief")
        run_pane = self.term.split_pane("Orchestrator", "run", "white", SLEEP, "horizontal", 0.5, chief, "c-run")
        done_pane = self.term.split_pane("Orchestrator", "done", "white", SLEEP, "horizontal", 0.5, chief, "c-done")
        self.term.splits.clear()
        me = os.getpid()
        self.agent("C-run", status="running", pid=me, pane_id=run_pane, paneCols=100, paneRows=50)
        self.agent("C-done", status="done", pid=me, pane_id=done_pane, paneCols=200, paneRows=50)
        self.launch("DUMMY-P1-work")
        self.assertEqual(self.term.splits, [(run_pane, "horizontal")])
        self.assertNotIn(done_pane, [t for t, _ in self.term.splits])


if __name__ == "__main__":
    unittest.main()
