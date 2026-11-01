"""Missing overviews recover independently of Queen starts and keep live panes."""
import os
from unittest import mock

from bzztest import RoundCase
from haivequeen import loop, runstate
from haivequeen.terminal import PaneInfo, TerminalError, pane_title


# Simulate terminal closure and recovery without opening a real terminal.
class DashboardSessionRecoveryTests(RoundCase):
    """Exercise both the periodic repair and the already-running start path."""

    # Keep only terminal effects mocked; use the real test database and locks.
    def setUp(self):
        """Create a terminal facade whose records can disappear between rounds."""
        super().setUp()
        self.terminal = mock.Mock()
        self.panes = []
        self.terminal.list_panes.side_effect = lambda window: list(self.panes)
        self.terminal.open_window.return_value = "HaiveQueen.0"
        self.terminal.split_pane.return_value = "HaiveQueen.2"

    # A closed terminal needs the overview before any Apiary or Queen can start.
    def test_empty_terminal_recovers_dashboard_before_apiary(self):
        """Rebuilding an Apiary starts with the overview and splits from it."""
        loop.ensure_apiary_pane(self.env(), self.terminal, "HaiveQueen", [])
        self.assertEqual(self.terminal.open_window.call_args.args[-1], "dashboard")
        self.assertEqual(self.terminal.split_pane.call_args.args[-2], "HaiveQueen.0")
        self.terminal.close_pane.assert_not_called()

    # Recover only the missing overview when an agent still occupies the window.
    def test_live_agent_is_kept_when_dashboard_is_missing(self):
        """Recovery splits a live pane without closing or restarting it."""
        self.panes.append(PaneInfo("HaiveQueen.1", "Hive: occupied", 123, True))
        pane = loop.maintain_dashboard(self.machine, self.terminal)
        self.assertEqual(pane, "HaiveQueen.2")
        self.assertEqual(self.terminal.split_pane.call_args.args[-2], "HaiveQueen.1")
        self.terminal.open_window.assert_not_called()
        self.terminal.close_pane.assert_not_called()

    # Repeated repair must leave an existing overview alone.
    def test_existing_dashboard_is_not_duplicated(self):
        """Two maintenance passes keep the existing overview and all agents."""
        self.panes.append(PaneInfo("HaiveQueen.0", pane_title("all projects", "Overview", "Dashboard", "-", True), 123, True))
        for _ in range(2):
            self.assertEqual(loop.maintain_dashboard(self.machine, self.terminal), "HaiveQueen.0")
        self.terminal.open_window.assert_not_called()
        self.terminal.split_pane.assert_not_called()
        self.terminal.close_pane.assert_not_called()

    # A pause prevents agent starts, not access to the overview.
    def test_pause_still_allows_dashboard_recovery(self):
        """Repair keeps the pause while opening the missing overview."""
        runstate.pause(self.conn, "test")
        loop.maintain_dashboard(self.machine, self.terminal)
        self.terminal.open_window.assert_called_once()
        self.assertEqual(runstate.get_state(self.conn)["state"], "PAUSED")

    # A requested stop must never reopen a window the loop is closing.
    def test_pending_stop_prevents_recovery(self):
        """Maintenance leaves the terminal untouched while stopping."""
        loop.stop_flag().write_text("stop", encoding="utf-8")
        self.assertEqual(loop.maintain_dashboard(self.machine, self.terminal), "")
        self.terminal.list_panes.assert_not_called()

    # A round must repair the overview even if scheduling cannot start a Queen.
    def test_round_repairs_dashboard_before_scheduling(self):
        """Automatic repair does not depend on provider slots or a ready plan."""
        with mock.patch.dict(os.environ, {"BZZ_TEST_NOSTART": ""}), \
                mock.patch("haivequeen.worktrees.prune", return_value={}), \
                mock.patch("haivequeen.hives.schedule_round", return_value={"skipped": "PAUSED"}) as schedule:
            result = loop.run_round(self.conn, self.machine, self.envs(), terminal=self.terminal)
        self.assertEqual(result["skipped"], "PAUSED")
        self.terminal.open_window.assert_called_once()
        schedule.assert_called_once()

    # A previous pane record with a dead process cannot suppress repair.
    def test_dead_dashboard_record_is_replaced(self):
        """Closing the terminal leaves records, but only live ones count."""
        self.panes.append(PaneInfo("HaiveQueen.0", pane_title("all projects", "Overview", "Dashboard", "-", True), 123, False))
        loop.maintain_dashboard(self.machine, self.terminal)
        self.terminal.open_window.assert_called_once()
        self.terminal.close_pane.assert_not_called()

    # A missing tmux session cannot be listed until the root window is recreated.
    def test_missing_terminal_session_is_recreated(self):
        """A missing session is recovered instead of propagating its listing error."""
        self.terminal.list_panes.side_effect = TerminalError("no session")
        self.assertEqual(loop.maintain_dashboard(self.machine, self.terminal), "HaiveQueen.0")
        self.terminal.open_window.assert_called_once()
        self.terminal.close_pane.assert_not_called()

    # Detached sessions retain their root pane record after terminal closure.
    def test_round_recovers_a_recorded_session_with_default_backend(self):
        """A stored dashboard record enables automatic default-backend repair."""
        record = loop.osplat.runs_dir() / '_panes' / 'dashboard' / 'pane.json'
        record.parent.mkdir(parents=True)
        record.write_text('{}', encoding='utf-8')
        with mock.patch.dict(os.environ, {"BZZ_TEST_NOSTART": ""}), \
                mock.patch.object(loop, 'get_terminal', return_value=self.terminal), \
                mock.patch('haivequeen.worktrees.prune', return_value={}), \
                mock.patch('haivequeen.hives.schedule_round', return_value={}):
            loop.run_round(self.conn, self.machine, self.envs())
        self.terminal.open_window.assert_called_once()

    # Standalone foreground commands must not create an unrelated terminal.
    def test_round_without_session_does_not_open_a_terminal(self):
        """A foreground round with no prior window remains without a window."""
        with mock.patch.dict(os.environ, {"BZZ_TEST_NOSTART": ""}), \
                mock.patch.object(loop, 'get_terminal') as backend, \
                mock.patch('haivequeen.worktrees.prune', return_value={}), \
                mock.patch('haivequeen.hives.schedule_round', return_value={}):
            loop.run_round(self.conn, self.machine, self.envs())
        backend.assert_not_called()
