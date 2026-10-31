"""Regression tests for closure flags through the runner command and round fallback."""
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from bzztest import RoundCase
from test_agent_runner import RunnerBase, claude_stream
from haivequeen import agent_runner as ar, loop
from haivequeen.terminal import PaneInfo, TerminalError


# Exercise the production runner command and both closure writes.
class RunnerClosureTest(RunnerBase):
    """Exercise the production runner command and both closure writes."""

    # Build the actual scheduler runner command using the isolated test runtime.
    def command(self):
        """Build the actual scheduler runner command using the isolated test runtime."""
        env = SimpleNamespace(name="demo", run_dir=lambda name: self.runs / name)
        spec = {"name": "F1-work", "provider": "claude", "protocol": "claude", "plan": "plans/demo.md",
                "package": "F1", "phase": "work", "cwd": str(self.ws), "prompt_file": str(self.prompt)}
        return loop.runner_argv(env, spec)

    # The real pane command selects wrapper mode.
    def test_runner_argv_wraps(self):
        """The real pane command selects wrapper mode."""
        self.assertIn("--wrap", self.command())

    # The production bootstrap runs a fake CLI and persists closure on completion.
    def test_runner_command_sets_closed(self):
        """The production bootstrap runs a fake CLI and persists closure on completion."""
        self.write_config("paneCloseDelaySeconds = 0\n")
        self.prepare("F1-work")
        (self.base / "stream.txt").write_text(claude_stream(), encoding="utf-8")
        result = subprocess.run(self.command(), cwd=str(self.ws), env=self.env, capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.row("F1-work")["status"], "done")
        self.assertTrue(self.row("F1-work")["paneClosed"])

    # Both sides of the close delay persist the closure flag in order.
    def test_closure_written_before_and_after_delay(self):
        """Both sides of the close delay persist the closure flag in order."""
        calls = []
        args = SimpleNamespace(project="demo", name="F1-work")
        with patch.object(ar.subprocess, "run"), patch.object(ar.db, "connect"), \
                patch.object(ar.runtime, "upsert_agent", side_effect=lambda *a, **kw: calls.append("closed")), \
                patch.object(ar.time, "sleep", side_effect=lambda delay: calls.append("delay")), \
                patch.object(loop, "hold_pane_lock_until_exit"):
            self.assertEqual(ar.pane_wrap(args, []), 0)
        self.assertEqual(calls, ["closed", "delay", "closed"])

    # A failed first write is retried after the delay and lock failures are logged.
    def test_write_and_lock_failures_are_logged(self):
        """A failed first write is retried after the delay and lock failures are logged."""
        args = SimpleNamespace(project="demo", name="F1-work")
        with patch.object(ar.subprocess, "run"), patch.object(ar.db, "connect"), \
                patch.object(ar.runtime, "upsert_agent", side_effect=[RuntimeError("write failure"), None]) as write, \
                patch.object(ar.runtime, "append_event") as log, patch.object(ar.time, "sleep"), \
                patch.object(loop, "hold_pane_lock_until_exit", side_effect=RuntimeError("lock failure")):
            self.assertEqual(ar.pane_wrap(args, []), 0)
        self.assertEqual(write.call_count, 2)
        self.assertEqual([c.args[2] for c in log.call_args_list],
                         ["paneClosed write failed: write failure", "pane lock failed: lock failure"])


# Round recovery uses the pane launcher, rather than the ended runner's pid.
class RoundClosureTest(RoundCase):
    """Round recovery uses the pane launcher, rather than the ended runner's pid."""

    # Ended rows with a dead launcher close; live, running and unknown panes stay untouched.
    def test_dead_launcher_closes_only_ended_panes(self):
        """Ended rows with a dead launcher close; live, running and unknown panes stay untouched."""
        terminal = SimpleNamespace(list_panes=lambda window: [PaneInfo("dead", "", 123, False), PaneInfo("live", "", 124, True)])
        for name, status, pane in [("done", "done", "dead"), ("failed", "failed", "dead"),
                                   ("live", "done", "live"),
                                   ("unknown", "done", "missing")]:
            self.agent(name, status=status, pane_id=pane, paneClosed=False, pid=999, started=1)
        self.round(terminal=terminal, alive=lambda pid: True)
        for name in ("done", "failed"):
            self.assertTrue(self.ag(name)["paneClosed"])
        for name in ("live", "unknown"):
            self.assertFalse(self.ag(name)["paneClosed"])

    # A closed running pane is recorded as a technical interruption.
    def test_closed_running_pane_is_interrupted(self):
        """A closed running pane is recorded as a technical interruption."""
        terminal = SimpleNamespace(list_panes=lambda window: [PaneInfo("dead", "", 123, False)])
        self.agent("running", status="running", pane_id="dead", paneClosed=False, pid=999, started=1)
        self.round(terminal=terminal, alive=lambda pid: True)
        row = self.ag("running")
        self.assertTrue(row["paneClosed"])
        self.assertEqual(row["interrupted"], "vanished")
        self.assertEqual(row["status"], "failed")

    # Detect a closed pane even when a round cannot start agents.
    def test_closed_pane_during_skipped_round(self):
        """A low-memory round still records a closed running pane."""
        terminal = SimpleNamespace(list_panes=lambda window: [PaneInfo("dead", "", 123, False)])
        self.agent("running", status="running", pane_id="dead", paneClosed=False, pid=999, started=1)
        self.round(terminal=terminal, alive=lambda pid: True, free_ram=lambda: 0)
        row = self.ag("running")
        self.assertTrue(row["paneClosed"])
        self.assertEqual(row["interrupted"], "vanished")

    # A dry round preserves the closure flag.
    def test_dry_round_does_not_mark_closed(self):
        """A dry round preserves the closure flag."""
        self.agent("done", pane_id="dead", paneClosed=False)
        terminal = SimpleNamespace(list_panes=lambda window: [PaneInfo("dead", "", 123, False)])
        self.round(terminal=terminal, dry_run=True)
        self.assertFalse(self.ag("done")["paneClosed"])

    # An unavailable pane registry does not close a pane or abort the round.
    def test_terminal_failure_is_logged(self):
        """An unavailable pane registry does not close a pane or abort the round."""
        self.agent("done", pane_id="dead", paneClosed=False)
        terminal = SimpleNamespace(list_panes=lambda window: (_ for _ in ()).throw(TerminalError("unavailable")))
        self.round(terminal=terminal)
        self.assertFalse(self.ag("done")["paneClosed"])
        self.assertIn("pane closure check failed: unavailable", self.log())
