"""Tests of the display fixes after the first live look at V2: slim Apiary head, equal Hive widths, a frame for every Hive, the running
Queen in the head, and plain female Queen names without a number."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from bzztest import REL, RoundCase
from haivequeen import db, hivepane, hives, loop, runtime
from haivequeen.terminal import pane_title
from haivequeen.terminal.headless import Headless

SLEEP = [sys.executable, "-c", "import time;time.sleep(60)"]


def plain(lines):
    """Text of frame lines without colours, one string per line."""
    return ["".join(t for t, _ in parts) for parts in lines]


class FrameCase(unittest.TestCase):
    """One Hive in a temporary database; the Apiary has a colour."""

    def setUp(self):
        """One Hive of the Apiary ODP."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / 'db')
        db.upsert_project(self.conn, 'ODP', None, 'FF9F43', 1)
        self.plan = 'plans/AI-LEVELS-PLAN.md'
        hives.create(self.conn, 'ODP', self.plan, 'claude', 'FF9F43')

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def queen(self, name, status, **extra):
        """A Queen row of the Hive."""
        row = {'plan': self.plan, 'phase': 'queen', 'status': status, 'person': name, 'type': 'claude', 'modelShort': 'Son 5.5 med.', 'started_at': db.now()}
        row.update(extra)
        runtime.upsert_agent(self.conn, 'ODP', name, row)

    def lines(self, **kw):
        """The frame lines at 60 columns."""
        return hivepane.frame_lines(self.conn, 'ODP', self.plan, 60, **kw)


class FrameFixTest(FrameCase):
    """Items 2 and 3: every Hive pane has a coloured frame; the running Queen is shown although older Queens of the Hive are in the list."""

    # Keep the running Queen visible while a DONE plan's pane is retained.
    def test_done_plan_shows_still_running_queen(self):
        """A plan becoming DONE does not hide its still-running Queen."""
        self.queen('Ada', 'running')
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES (?,?,?)", ('ODP', self.plan, 'DONE'))
        self.assertIn('Queen: Ada', '\n'.join(plain(self.lines())))

    # Keep the Queen identity until its process has actually exited.
    def test_done_status_with_live_process_shows_queen(self):
        """A reported result does not hide a still-running Queen process."""
        self.queen('Ada', 'done', pid=os.getpid())
        self.assertIn('Queen: Ada', '\n'.join(plain(self.lines())))

    # Prefer the active Queen to an older runner that has not exited.
    def test_active_queen_wins_over_old_live_runner(self):
        """A previous live runner does not hide the current Queen."""
        self.queen('Gina', 'done', pid=os.getpid())
        self.queen('Ada', 'running')
        self.assertIn('Queen: Ada', '\n'.join(plain(self.lines())))

    def test_running_bee_is_shown_without_a_queen(self):
        """A narrow Hive still shows a running Bee."""
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': 'Bea', 'task': 'work'})
        self.assertIn('Bee: Bea work', '\n'.join(plain(self.lines())))

    # Retain a Bee until its process exits after reporting a result.
    def test_done_bee_with_live_process_is_shown(self):
        """A Bee process that is still alive remains in its Hive frame."""
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'done', 'person': 'Bea', 'task': 'work', 'pid': os.getpid()})
        self.assertIn('Bee: Bea work', '\n'.join(plain(self.lines())))

    def test_ended_bee_is_not_shown(self):
        """Only running Bees appear in the Hive pane."""
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'done', 'person': 'Bea', 'task': 'work'})
        self.assertNotIn('Bee: Bea', '\n'.join(plain(self.lines())))

    def test_ended_process_bee_is_not_restored_by_queen_note(self):
        """An old progress note cannot restore an ended Bee process."""
        self.queen('Ada', 'running')
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'done', 'person': 'Bea', 'task': 'work'})
        self.conn.execute("INSERT INTO packages(project,plan_path,id) VALUES ('ODP',?,'P1')", (self.plan,))
        db.add_event(self.conn, 'ODP', 'note', 'Bea P1: step 1/3 work', agent='Ada')
        self.assertNotIn('Bee: Bea', '\n'.join(plain(self.lines())))

    def test_waiting_hive_shows_wait_for_place(self):
        """A Hive waiting for a place names that state in its head."""
        self.assertIn('Queen: wartet auf Platz', '\n'.join(plain(self.lines())))

    def test_very_narrow_head_fits_its_pane(self):
        """Even a very narrow Hive frame never wraps beyond its pane width."""
        for width in range(1, 7):
            with self.subTest(width=width):
                self.assertTrue(all(len(line) <= width for line in plain(hivepane.frame_lines(self.conn, 'ODP', self.plan, width))))

    def test_milestone_render_in_very_narrow_pane_terminates(self):
        """A one-column pane can render a Queen milestone without hanging."""
        from haivequeen import header
        text = header.render(hivepane.frame_lines(self.conn, 'ODP', self.plan, 1), 1, 10, [('white', 'work')], True)
        self.assertIn('work'[0], text)

    def test_short_pane_shows_heads_and_only_fitting_bees(self):
        """A three-row Hive pane keeps its heads and cuts excess Bee rows."""
        import io
        from unittest import mock
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': 'Bea'})
        runtime.upsert_agent(self.conn, 'ODP', 'Cleo', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': 'Cleo'})
        conn = self.conn
        class Keep:
            """Use the test database without closing it."""
            def __getattr__(self, name):
                """Forward database operations."""
                return getattr(conn, name)
            def close(self):
                """Keep the test connection open."""
        keys = mock.MagicMock()
        with mock.patch.object(hivepane.db, 'connect', return_value=Keep()), \
             mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((60, 3))), \
             mock.patch.object(hivepane.header, 'render', return_value='') as render:
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0, max_ticks=1, keys=keys)
        lines = plain(render.call_args.args[0])
        self.assertEqual(len(lines), 3)
        self.assertIn('Hive:', lines[0])
        self.assertIn('Queen:', lines[1])
        self.assertIn('Bee: Bea', lines[2])

    # Publish a fresh Hive pane height when the layout requests it.
    def test_hive_pane_acknowledges_height_measurement(self):
        import io
        from unittest import mock
        runtime.kv_set(self.conn, 'ODP', 'hive-measure-request-' + Path(self.plan).stem, '123')
        conn = self.conn
        class Keep:
            """Keep the test connection open after the pane exits."""
            def __getattr__(self, name):
                """Forward database operations."""
                return getattr(conn, name)
            def close(self):
                """Leave the connection open."""
        with mock.patch.object(hivepane.db, 'connect', return_value=Keep()), \
             mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((60, 17))):
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0, max_ticks=1, keys=mock.MagicMock())
        self.assertEqual(runtime.kv_get(self.conn, 'ODP', 'hive-rows-' + Path(self.plan).stem), '17')
        self.assertEqual(runtime.kv_get(self.conn, 'ODP', 'hive-measure-ack-' + Path(self.plan).stem), '123')

    # Verify that a short Hive pane displays the pending command confirmation.
    def test_short_pane_shows_command_confirmation(self):
        """A three-row Hive shows the confirmation below its two head lines."""
        import io
        from unittest import mock
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': 'Bea'})
        conn = self.conn
        class Keep:
            """Keep the test connection open."""
            def __getattr__(self, name):
                """Forward database operations."""
                return getattr(conn, name)
            def close(self):
                """Keep the connection open."""
        keys = mock.MagicMock()
        keys.__enter__.return_value = keys
        keys.get.side_effect = ['x', None]
        with mock.patch.object(hivepane.db, 'connect', return_value=Keep()), \
             mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((60, 3))), \
             mock.patch.object(hivepane.header, 'render', return_value='') as render:
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0.01, max_ticks=2, keys=keys)
        self.assertIn('nochmal', '\n'.join(plain(render.call_args.args[0])))

    # Verify that Bee lines do not hide a pending confirmation in a compact pane.
    def test_compact_pane_shows_confirmation_with_many_bees(self):
        """A compact Hive retains its confirmation when Bee lines fill the frame."""
        import io
        from unittest import mock
        for name in ('Bea', 'Cleo', 'Dina'):
            runtime.upsert_agent(self.conn, 'ODP', name, {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': name})
        self.conn.execute("UPDATE projects SET colour='yellow' WHERE name='ODP'")
        conn = self.conn
        class Keep:
            """Keep the test connection open."""
            def __getattr__(self, name):
                """Forward database operations."""
                return getattr(conn, name)
            def close(self):
                """Keep the connection open."""
        keys = mock.MagicMock()
        keys.__enter__.return_value = keys
        keys.get.side_effect = ['x', None]
        with mock.patch.object(hivepane.db, 'connect', return_value=Keep()), \
             mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((60, 5))), \
             mock.patch.object(hivepane.header, 'render', return_value='') as render:
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0.01, max_ticks=2, keys=keys)
        self.assertIn('nochmal', '\n'.join(plain(render.call_args.args[0])))

    def test_paused_queenless_hive_shows_pause(self):
        """Global pause takes priority over the queenless state."""
        from haivequeen import runstate
        self.conn.execute("UPDATE hives SET state='queenless' WHERE plan_path=?", (self.plan,))
        runstate.pause(self.conn)
        self.assertIn('Queen: pausiert', '\n'.join(plain(self.lines())))

    def test_blocked_hive_shows_reason_before_wait_state(self):
        """A reported integration block takes priority over the wait state."""
        self.conn.execute("UPDATE hives SET state='owner_check' WHERE plan_path=?", (self.plan,))
        runtime.queue_upsert(self.conn, 'ODP', 'branch', 'abc', self.plan, 'blocked', 'compile failed')
        self.assertIn('Queen: blockiert: compile failed', '\n'.join(plain(self.lines())))

    def test_multiline_block_reason_stays_on_one_head_line(self):
        """A multiline error cannot wrap the Hive head."""
        runtime.queue_upsert(self.conn, 'ODP', 'branch', 'abc', self.plan, 'blocked', 'compile failed\nsecond line')
        self.assertTrue(all('\n' not in line for line in plain(self.lines())))

    def test_running_queen_is_shown_even_when_queue_is_blocked(self):
        """The live Queen name takes precedence over a stored block."""
        runtime.queue_upsert(self.conn, 'ODP', 'branch', 'abc', self.plan, 'blocked', 'compile failed')
        self.queen('Ada', 'running')
        text = '\n'.join(plain(self.lines()))
        self.assertIn('Queen: Ada', text)
        self.assertNotIn('blockiert:', text)

    def test_hive_without_queen_has_a_frame_in_the_apiary_colour(self):
        """A Hive without a running Queen draws a small box: top, Hive line, Queen line with the state, bottom; all in the Apiary colour."""
        self.conn.execute("UPDATE hives SET state='answer' WHERE plan_path=?", (self.plan,))
        lines = self.lines()
        text = plain(lines)
        self.assertEqual(len(text), 4)
        self.assertTrue(text[0].startswith(hivepane.TOP) and text[3].startswith(hivepane.BOTTOM))
        self.assertIn('Hive: AI-LEVELS-PLAN', text[1])
        self.assertIn('Queen: wartet auf Antwort', text[2])
        self.assertEqual({c for parts in lines for t, c in parts if t.strip(' │┌┐└┘─')}, {'FF9F43'})
        self.assertEqual(len(self.lines(message='X nochmal')), 5)

    def test_the_running_queen_wins_over_an_older_one(self):
        """An ended Queen of the same Hive that is listed first does not hide the running Queen: 'Queen: Ada', never 'Queen: -'."""
        self.queen('Gina', 'done', ended_at=db.now())
        self.queen('Ada', 'running')
        self.conn.execute("UPDATE hives SET state='running' WHERE plan_path=?", (self.plan,))
        queen = next(t for t in plain(self.lines()) if 'Queen:' in t)
        self.assertIn('Queen: Ada', queen)
        self.assertIn('Son 5.5 med.', queen)

    def test_an_ended_queen_alone_does_not_show_a_bare_dash(self):
        """With only ended Queens and a Hive marked running, the Queen line is not a bare dash."""
        self.queen('Gina', 'done', ended_at=db.now())
        self.conn.execute("UPDATE hives SET state='running' WHERE plan_path=?", (self.plan,))
        queen = next(t for t in plain(self.lines()) if 'Queen:' in t)
        self.assertNotRegex(queen, r'Queen: -\s*│?\s*$')


class HeadPaneTest(FrameCase):
    """The head pane overwrites its line instead of clearing the screen, so no copies pile up in the scrollback."""

    def test_apiary_head_fits_narrow_width(self):
        """The Apiary head remains on one line when its name does not fit."""
        text = plain(hivepane.head_lines(self.conn, 'ODP', 6))[0]
        self.assertLessEqual(len(text), 6)
        self.assertTrue(text.endswith('…'))

    # Keep the Apiary visible when its final plan ends.
    def test_head_remains_when_only_plan_is_done(self):
        """An Apiary remains visible after its last plan is done."""
        import io
        from unittest import mock
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES (?,?,?)", ('ODP', self.plan, 'DONE'))
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'db'):
            out = io.StringIO()
            hivepane.run_head('ODP', out=out, interval=0, max_ticks=1)
        self.assertIn('Apiary ODP', out.getvalue())

    def test_done_apiary_head_stays_with_occupied_pane(self):
        """The head stays while an agent still occupies the Apiary block."""
        import io
        from unittest import mock
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES (?,?,?)", ('ODP', self.plan, 'DONE'))
        self.queen('Ada', 'running', pane_id='W.1')
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'db'):
            out = io.StringIO()
            hivepane.run_head('ODP', out=out, interval=0, max_ticks=1)
        self.assertIn('Apiary ODP', out.getvalue())

    # Stop an empty Apiary head once its registration is removed.
    def test_removed_apiary_head_stops(self):
        """An unregistered Apiary without an agent no longer draws its head."""
        import io
        from unittest import mock
        self.conn.execute("DELETE FROM projects WHERE name='ODP'")
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'db'):
            out = io.StringIO()
            hivepane.run_head('ODP', out=out, interval=0, max_ticks=1)
        self.assertEqual(out.getvalue(), '')

    def test_the_head_never_clears_the_screen(self):
        """run_head writes the line with a cursor move and an erase to the end of the line, never the screen clear."""
        import io
        from unittest import mock
        out = io.StringIO()
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'db'):
            hivepane.run_head('ODP', out=out, interval=0, max_ticks=2)
        self.assertNotIn('\x1b[2J', out.getvalue())
        self.assertIn('Apiary ODP', out.getvalue())
        self.assertIn('\x1b[?7l', out.getvalue())


class PlacementCase(RoundCase):
    """A headless terminal in which Hive panes are opened; every split is recorded as (target, direction, size)."""

    def setUp(self):
        """Headless terminal with a Beekeeper pane; pane commands are sleepers."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        from unittest import mock
        self.term = Headless(self.base / "panes")
        self.chief = self.term.open_window("HaiveQueen", pane_title("all projects", "QUEEN", "Queen", "m", True), SLEEP, name="chief")
        self.patches = [mock.patch("haivequeen.loop.hive_argv", return_value=SLEEP), mock.patch("haivequeen.loop.apiary_argv", return_value=SLEEP)]
        for p in self.patches:
            p.start()
        self.calls = []
        orig = self.term.split_pane
        self.term.split_pane = lambda *a, **k: (self.calls.append((a[6], a[4], a[5])), orig(*a, **k))[1]

    def tearDown(self):
        """End every pane process."""
        for p in self.patches:
            p.stop()
        for pane in self.term.list_panes("HaiveQueen"):
            if pane.alive:
                self.term.close_pane(pane.id)
        super().tearDown()


class SplitSizeTest(PlacementCase):
    """Items 1 and 5: slim head, equal widths."""

    # Count consecutive failed pane starts across Apiaries.
    def test_pane_start_failures_reset_after_success(self):
        """Only consecutive failed splits count towards window recovery."""
        from unittest import mock
        from haivequeen.terminal import TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.split_pane.side_effect = [TerminalError('pane W.1 did not start within 20 s')] * 3 + ['W.1']
        for count in (1, 2, 3):
            with self.assertRaises(TerminalError):
                loop.split_tracked(env, term, 'W', 'title', 'white', [], 'horizontal', 0.5, 'W.0', 'name')
            self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-start-failures'), str(count))
        self.assertEqual(loop.split_tracked(env, term, 'W', 'title', 'white', [], 'horizontal', 0.5, 'W.0', 'name'), 'W.1')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-start-failures'), '0')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # An invalid split target is not a failed pane start.
    def test_invalid_split_target_does_not_count_as_pane_start_failure(self):
        """Only a pane that fails to start contributes to recovery."""
        from unittest import mock
        from haivequeen.terminal import TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.split_pane.side_effect = TerminalError('split target is no longer live')
        with self.assertRaises(TerminalError):
            loop.split_tracked(env, term, 'W', 'title', 'white', [], 'horizontal', 0.5, 'W.0', 'name')
        self.assertIsNone(runtime.kv_get(self.conn, '*', 'pane-start-failures'))

    # A pending repair waits until every occupied pane is free.
    def test_window_repair_waits_for_occupied_panes(self):
        """The repair leaves a running agent and the pending flag untouched."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id='W.1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True), PaneInfo('W.1', 'Hive', 2, True)]
        self.assertFalse(loop.repair_pane_window(env, term, 'W'))
        term.close_pane.assert_not_called()
        term.split_pane.assert_not_called()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # Rebuild the dashboard using the existing window as the anchor.
    def test_window_repair_rebuilds_without_opening_a_window(self):
        """An unoccupied window gets a new dashboard before the old one closes."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.name = 'headless'
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True)]
        term.split_pane.return_value = 'W.1'
        self.assertTrue(loop.repair_pane_window(env, term, 'W'))
        term.open_window.assert_not_called()
        term.split_pane.assert_called_once()
        term.close_pane.assert_called_once_with('W.0')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '0')

    # A missing dashboard must not cause a new terminal window.
    def test_window_repair_without_dashboard_stays_pending(self):
        """Repair waits if the existing window has no dashboard anchor."""
        from unittest import mock
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = []
        self.assertFalse(loop.repair_pane_window(env, term, 'W'))
        term.open_window.assert_not_called()
        term.split_pane.assert_not_called()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # An idle Hive can anchor recovery when the dashboard has disappeared.
    def test_window_repair_uses_idle_hive_as_anchor(self):
        """The replacement dashboard opens in the existing window before the idle Hive closes."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.1', loop.hive_pane_title('testproj', REL, True), 1, True)]
        term.split_pane.return_value = 'W.2'
        self.assertTrue(loop.repair_pane_window(env, term, 'W'))
        self.assertEqual(term.split_pane.call_args.args[6], 'W.1')
        term.close_pane.assert_called_once_with('W.1')
        term.open_window.assert_not_called()

    # A legacy Beekeeper pane also anchors an otherwise empty window.
    def test_window_repair_uses_idle_legacy_pane_as_anchor(self):
        """A legacy pane keeps the existing window open during dashboard recovery."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.1', loop.queen_pane_title('m', False), 1, True)]
        term.split_pane.return_value = 'W.2'
        self.assertTrue(loop.repair_pane_window(env, term, 'W'))
        term.open_window.assert_not_called()
        self.assertEqual(term.split_pane.call_args.args[6], 'W.1')

    # A failed replacement leaves the original dashboard and repair request intact.
    def test_failed_window_repair_preserves_dashboard_and_request(self):
        """A failed replacement does not close the last dashboard."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo, TerminalError
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True)]
        term.split_pane.side_effect = TerminalError('pane did not start')
        with self.assertRaises(TerminalError):
            loop.repair_pane_window(env, term, 'W')
        term.close_pane.assert_not_called()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # A failed old-dashboard close must not reuse the new dashboard's registry name.
    def test_failed_old_dashboard_close_advances_repair_generation(self):
        """A subsequent retry uses a different pane name after a close failure."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo, TerminalError
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True)]
        term.split_pane.return_value = 'W.1'
        term.close_pane.side_effect = TerminalError('close failed')
        with self.assertRaises(TerminalError):
            loop.repair_pane_window(env, term, 'W')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'dashboard-repair-generation'), '0')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # A retry after a partial repair removes older unused dashboards.
    def test_window_repair_closes_all_previous_dashboards(self):
        """A successful retry leaves only the new dashboard."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True), PaneInfo('W.1', 'Dashboard', 2, True)]
        term.split_pane.return_value = 'W.2'
        self.assertTrue(loop.repair_pane_window(env, term, 'W'))
        self.assertEqual([c.args[0] for c in term.close_pane.call_args_list], ['W.0', 'W.1'])

    # The normal pane maintenance pass performs a pending repair.
    def test_pane_maintenance_performs_pending_window_repair(self):
        """An idle maintenance pass replaces the dashboard after repeated failures."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        term = mock.Mock()
        term.name = 'headless'
        term.list_panes.return_value = [PaneInfo('W.0', 'Dashboard', 1, True)]
        term.split_pane.return_value = 'W.1'
        hives.ensure_hive_panes(self.conn, [env], term, heads_only=True)
        term.close_pane.assert_called_once_with('W.0')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '0')

    def test_the_first_hive_leaves_the_head_slim(self):
        """The Hive below the head takes almost all of the height: the head keeps one or two lines."""
        env = self.envs()[0]
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        below_head = [c for c in self.calls if c[1] == "vertical"]
        self.assertEqual(len(below_head), 1)
        self.assertGreaterEqual(below_head[0][2], 0.9)
        self.assertLess(below_head[0][2], 1.0)

    # A recreated head must not reuse its predecessor's measurements.
    def test_new_apiary_head_discards_stale_dimensions(self):
        """A recreated head must not reuse its predecessor's measurements."""
        env = self.envs()[0]
        runtime.kv_set(self.conn, 'testproj', 'apiary-rows', '4')
        runtime.kv_set(self.conn, 'testproj', 'apiary-columns', '20')
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(env, self.term, 'HaiveQueen', live)
        self.assertFalse(runtime.kv_get(self.conn, 'testproj', 'apiary-rows'))
        self.assertFalse(runtime.kv_get(self.conn, 'testproj', 'apiary-columns'))

    # A four-row block splits into a head and a three-row Hive.
    def test_short_apiary_block_leaves_one_row_for_its_head(self):
        """A four-row block splits into a head and a three-row Hive."""
        env = self.envs()[0]
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(env, self.term, 'HaiveQueen', live)
        runtime.kv_set(self.conn, 'testproj', 'apiary-rows', '4')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertEqual(self.calls[-1][2], 0.75)

    def test_hives_of_an_apiary_get_equal_heights(self):
        """Four idle Hive rows share the available height equally."""
        env = self.envs()[0]
        plans = [REL] + ["other/%s-PLAN.md" % c for c in "BCD"]
        for i, plan in enumerate(plans):
            hives.create(self.conn, "testproj", plan, "claude", "4FA8FF" if i == 0 else "C77DFF")
        for plan in plans:
            loop.ensure_hive_pane(env, self.term, "HaiveQueen", plan)
        widths = [1.0]
        for _, direction, size in [c for c in self.calls if c[1] == "vertical"][1:]:   # the first vertical split opens the first Hive below the head
            last = widths[-1]
            widths[-1] = last * (1 - size)
            widths.append(last * size)
        self.assertEqual(len(widths), 4)
        for w in widths:
            self.assertAlmostEqual(w, 0.25, places=6)

    def test_more_hives_than_counted_still_open(self):
        """A Hive that appears after the counted ones is split in half and never raises."""
        env = self.envs()[0]
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", "x/B-PLAN.md")
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", "x/C-PLAN.md")
        self.assertEqual(len([p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_hive_title(p.title)]), 3)


class QueenNameTest(PlacementCase):
    """Item 4: a Queen carries a plain female name that no running agent uses, never a number suffix."""

    def test_names_of_old_agents_are_reused_without_a_number(self):
        """Every list name was an agent of the project long ago: the new Queen still shows a plain name, her row alone carries the suffix."""
        for n in loop.AGENT_NAMES:
            self.agent(n, phase="work", status="done", person=n, started=3000, ended=2900)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(terminal=self.term, poll=0.01, tries=1)
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertIn(queens[0]["person"], loop.AGENT_NAMES)
        self.assertNotRegex(queens[0]["person"], r"-\d")
        events = [e["text"] for e in db.rows(self.conn, "SELECT text FROM events WHERE text LIKE '%started%'")]
        self.assertTrue(any(queens[0]["person"] + " started" in e for e in events), events)
        self.assertFalse(any(re.search(r"-\d+ started", e) for e in events), events)
        prompt = (self.env().run_dir(queens[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("--name %s " % queens[0]["name"], prompt)   # the Queen updates her own row, so the prompt names the row key

    def test_the_list_is_long_enough_for_many_running_agents(self):
        """The list of female names is longer than the number of agents that can run at once, and has no duplicates."""
        self.assertGreaterEqual(len(loop.AGENT_NAMES), 40)
        self.assertEqual(len(set(loop.AGENT_NAMES)), len(loop.AGENT_NAMES))

    def test_no_running_agent_shares_a_name(self):
        """When every list name but one is held by a running agent, the new agent gets the free one."""
        free = loop.AGENT_NAMES[-1]
        for n in loop.AGENT_NAMES[:-1]:
            self.agent(n, phase="work", status="running", person=n, started=5)
        self.assertEqual(loop.agent_identity(self.conn)["Person"], free)


class QueenOrderTest(PlacementCase):
    """The Queen order names the integration place first: the release worktree may only be recreated by the Queen who holds the place."""

    def test_acquire_comes_before_the_release_worktree_the_compile_check_and_ready(self):
        """Order in the Queen prompt: acquire, then the release worktree, then the compile check, then bzz ready, then her own integration and release."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(terminal=self.term, poll=0.01, tries=1)
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        marks = ["bzz integration acquire", "release worktree", "compile check", "bzz ready", "bzz integration release"]
        at = [text.find(m) for m in marks]
        self.assertNotIn(-1, at, dict(zip(marks, at)))
        self.assertEqual(at, sorted(at), dict(zip(marks, at)))
        self.assertNotIn("Register your own branch with bzz ready, then", text)


class OneBuildJobTest(unittest.TestCase):
    """The owner's rule is one build job (--parallel 1): the value that bzz integration acquire hands out is always 1."""

    def test_the_planned_build_always_gets_one_job(self):
        """Whatever the cores, the free RAM and the measured use per job say, plan_build returns one job."""
        from haivequeen import buildjobs

        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "db")

            class Env:
                """Just enough of a project environment for plan_build."""
                name = "ODP"
                settings = {"buildJobs": {"envVar": "BUILD_MP_JOBS", "perJobMB": 300}}

            Env.conn = conn
            for cores, free in ((12, 32000), (6, 4096), (2, 100000)):
                plan = buildjobs.plan_build(Env, free_mb=free, cores=cores)
                self.assertEqual(plan["Jobs"], 1, (cores, free))
                self.assertEqual(plan["EnvVar"], "BUILD_MP_JOBS")
            conn.close()


class QueenEndRuleTest(unittest.TestCase):
    """A Queen ends only when the plan is integrated, only owner questions remain, or the Beekeeper asked for a handoff."""

    def test_the_queen_order_names_the_three_ways_to_end(self):
        """The Queen instructions say that she works through all open packages in one run and list the only three reasons to end."""
        from haivequeen import bees
        text = bees.queen_instructions("medium", 3, 3, 300000)
        self.assertIn("You end only when", text)
        for reason in ("integrated", "WAITING_FOR_OWNER", "HANDOFF"):
            self.assertIn(reason, text[text.index("You end only when"):])
        self.assertIn("never end after an intermediate step", text)
        self.assertIn("wins over any rule", text[text.index("never end after an intermediate step"):])


class EndedWithoutWorkTest(unittest.TestCase):
    """K2 looks for new commits in the repository of the Queen, in every worktree and branch, not only in her start folder."""

    def git(self, cwd, *args, date=None):
        """Run git in `cwd` with a fixed identity and an optional commit date."""
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    def test_a_commit_in_another_worktree_counts_as_work(self):
        """The Queen started in the plan worktree; her commit in a second worktree of the same repository means she did work."""
        with tempfile.TemporaryDirectory() as tmp:
            main, other = Path(tmp) / "main", Path(tmp) / "other"
            main.mkdir()
            self.git(main, "init", "-q")
            (main / "a.txt").write_text("a", encoding="utf-8")
            self.git(main, "add", "a.txt")
            self.git(main, "commit", "-q", "-m", "old", date="2020-01-01T00:00:00")
            self.git(main, "worktree", "add", "-q", "-b", "work3", str(other))
            (other / "b.txt").write_text("b", encoding="utf-8")
            self.git(other, "add", "b.txt")
            self.git(other, "commit", "-q", "-m", "new work")
            run = Path(tmp) / "run"
            run.mkdir()

            class Env:
                """Just enough of an environment: the run folder of an agent."""
                def run_dir(self, name):
                    return run

            agent = {"name": "Ada", "cwd": str(main), "started_at": "2021-01-01T00:00:00"}
            self.assertFalse(hives.ended_without_work(Env(), agent))
            from datetime import datetime, timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
            self.assertTrue(hives.ended_without_work(Env(), dict(agent, started_at=tomorrow)))   # nothing is newer than tomorrow


if __name__ == "__main__":
    unittest.main()


class WrapAndLayoutTest(FrameCase):
    """Narrow Hive panes wrap the head lines instead of cutting them; the session start puts the first Apiary head right of the Beekeeper before the dashboard goes below it."""

    def test_head_lines_wrap_in_a_narrow_pane(self):
        """A head that does not fit is cut with an ellipsis, never wrapped."""
        self.conn.execute("UPDATE hives SET state='answer' WHERE plan_path=?", (self.plan,))
        rows = [t[2:-2].strip() for t in plain(hivepane.frame_lines(self.conn, 'ODP', self.plan, 20))[1:-1]]
        self.assertTrue(all(len(t) <= 16 for t in rows))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].startswith("Hive: "))
        self.assertTrue(rows[0].endswith("…"))

    def test_session_start_opens_the_dashboard_and_the_head_right_of_it(self):
        """Without panes the window gets the dashboard as its first pane and the first Apiary head split off it to the right; there is no Beekeeper pane."""
        calls = []

        class Term:
            """Records the pane calls of the session start."""

            def list_panes(self, window):
                """No pane is open."""
                return []

            def open_window(self, window, title, command, colour=None, name=None):
                """Open the window with the dashboard pane."""
                calls.append(("open", name))
                return "W.0"

            def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
                """Record a split."""
                calls.append(("split", name, direction, size, target))
                return "W.%d" % len(calls)
        panes = loop.open_session_panes(Term(), "W", SLEEP, head=("H", SLEEP, "ODP__apiary"))
        self.assertEqual(calls[0], ("open", "dashboard"))
        self.assertEqual(calls[1][:3], ("split", "ODP__apiary", "horizontal"))
        self.assertEqual(calls[1][4], "W.0")
        self.assertEqual(len(calls), 2)
        self.assertEqual(panes, ["W.0", "W.2"])

    # A freshly opened first Apiary head cannot use an older pane's size.
    def test_fresh_session_discards_stale_head_measurements(self):
        """A freshly opened first Apiary head cannot use an older pane's size."""
        from unittest import mock
        term = mock.Mock()
        term.list_panes.return_value = []
        term.open_window.return_value = 'W.0'
        runtime.kv_set(self.conn, 'ODP', 'apiary-rows', '4')
        runtime.kv_set(self.conn, 'ODP', 'apiary-columns', '20')
        runtime.kv_set(self.conn, '*', 'dashboard-rows', '50')
        head = (loop.apiary_pane_title('ODP', True), SLEEP, 'ODP__apiary')
        loop.open_session_panes(term, 'W', SLEEP, head=head, conn=self.conn)
        self.assertFalse(runtime.kv_get(self.conn, 'ODP', 'apiary-rows'))
        self.assertFalse(runtime.kv_get(self.conn, 'ODP', 'apiary-columns'))
        self.assertFalse(runtime.kv_get(self.conn, '*', 'dashboard-rows'))

    # Verify that missing pane measurements fail within the existing startup timeout.
    def test_missing_pane_measurement_times_out(self):
        """A missing size cannot leave the window startup waiting indefinitely."""
        from unittest import mock
        from haivequeen.terminal import TerminalError
        term = mock.Mock()
        term.start_timeout = 0
        with self.assertRaises(TerminalError):
            loop.wait_pane_measurement(self.conn, '*', 'dashboard-rows', term)

    # Verify that an older measurement cannot satisfy a newer request.
    def test_measurement_wait_ignores_old_acknowledgement(self):
        """A split waits for the matching head measurement, not an old value."""
        from unittest import mock
        term = mock.Mock()
        term.start_timeout = 1
        with mock.patch.object(runtime, 'kv_get', side_effect=['12', '13']) as get, mock.patch.object(loop.time, 'sleep'):
            self.assertEqual(loop.wait_pane_measurement(self.conn, 'ODP', 'apiary-measure-ack', term, expected='13'), 13)
        self.assertEqual(get.call_count, 2)

    # Verify the dashboard reports its size before the first Apiary split.
    def test_session_waits_for_dashboard_size_before_opening_head(self):
        """Fresh layout uses the new dashboard measurement rather than a fallback."""
        from unittest import mock
        calls = []
        term = mock.Mock()
        term.name = 'wt'
        term.start_timeout = 1
        term.list_panes.return_value = []
        term.open_window.return_value = 'W.0'
        term.split_pane.side_effect = lambda *args: calls.append('split') or 'W.1'
        with mock.patch.object(runtime, 'kv_get', side_effect=lambda *args: calls.append('measure') or '40'):
            loop.open_session_panes(term, 'W', SLEEP, head=(loop.apiary_pane_title('ODP', True), SLEEP, 'ODP__apiary'), conn=self.conn)
        self.assertLess(calls.index('measure'), calls.index('split'))

    def test_dashboard_pane_has_dashboard_title(self):
        """The dashboard pane is identified by its Dashboard title."""
        from unittest import mock
        term = mock.Mock()
        term.list_panes.return_value = []
        term.open_window.return_value = 'W.0'
        loop.open_session_panes(term, 'W', SLEEP)
        self.assertEqual(term.open_window.call_args.args[1], 'Dashboard')
        self.assertEqual(loop.first_split_target([type('P', (), {'id': 'W.0', 'title': 'Dashboard'})()]), 'W.0')

    def test_window_start_runs_the_loop_without_a_pane(self):
        """`bzz start` opens the dashboard (and the head), starts the loop as a detached process with its output in a file, and opens no Beekeeper pane."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        opened, spawned = [], []

        class Term:
            """Records the pane calls and shows no pane."""
            trace = None

            def list_panes(self, window):
                """No pane is open."""
                return []

            def open_window(self, window, title, command, colour=None, name=None):
                """Record the window."""
                opened.append((title, name))
                return "W.0"

            def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
                """Record a split."""
                opened.append((title, name))
                return "W.1"
        args = argparse.Namespace(foreground=False, terminal="headless", rounds=0, round_minutes=0)
        self.addCleanup(lambda: loop.start_pending_file().unlink(missing_ok=True))   # the real loop removes the pending marker; the mocked one cannot
        with mock.patch.object(loop, "get_terminal", return_value=Term()), mock.patch.object(loop, "loop_running", return_value=0), \
                mock.patch.object(loop, "spawn_detached", side_effect=lambda argv, log: spawned.append((argv, log))), contextlib.redirect_stdout(io.StringIO()):
            loop.cmd_start(args, self.conn)
        self.assertEqual(len(spawned), 1)
        self.assertIn("--foreground", spawned[0][0])
        self.assertTrue(all("BEEKEEPER" not in title for title, _ in opened))
        self.assertEqual(opened[0][1], "dashboard")

    # Start with the first registered Apiary even if it has no open Hive.
    def test_window_start_uses_first_registered_apiary(self):
        """An idle first Apiary precedes a later Apiary with an open Hive."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        db.upsert_project(self.conn, 'first', None, 'white', 0)
        db.upsert_project(self.conn, 'second', None, 'white', 0)
        hives.create(self.conn, 'second', 'plans/ACTIVE.md', 'claude', 'white')
        args = argparse.Namespace(foreground=False, terminal='headless', rounds=0, round_minutes=0)
        self.addCleanup(lambda: loop.start_pending_file().unlink(missing_ok=True))
        machine = {'projects': [{'name': 'first'}, {'name': 'second'}]}
        with mock.patch('haivequeen.config.load_config', return_value=machine), mock.patch.object(loop, 'get_terminal'), \
                mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(loop, 'close_idle_hive_panes'), \
                mock.patch.object(loop, 'open_session_panes', return_value=[]) as opened, \
                mock.patch.object(loop, 'spawn_detached'), contextlib.redirect_stdout(io.StringIO()):
            loop.cmd_start(args, self.conn)
        self.assertEqual(opened.call_args.args[3][0], loop.apiary_pane_title('first', loop.is_test_runtime()))

    def test_pane_of_a_hive_without_a_running_queen_replays_no_old_text(self):
        """The text of an ended Queen (hours old) is not drawn again into the pane when the pane is redrawn."""
        import io
        from unittest import mock

        class NoKeys:
            """Key reader without keys."""

            def __enter__(self):
                """Nothing to set up."""
                return self

            def __exit__(self, *exc):
                """Nothing to restore."""

            def get(self, timeout):
                """No key pressed."""
                return None
        self.queen('Old', 'done')
        conn = self.conn

        class Keep:
            """The test connection, which the pane may not close."""

            def __getattr__(self, name):
                """Everything but close goes to the real connection."""
                return getattr(conn, name)

            def close(self):
                """Keep the connection open for the test."""
        with mock.patch.object(hivepane.header, 'read_milestones', return_value=['old text']) as read,                 mock.patch.object(hivepane.db, 'connect', return_value=Keep()):
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0, max_ticks=1, keys=NoKeys())
        read.assert_not_called()

    # Preserve output while a Queen's runner is still alive after reporting.
    def test_live_runner_replays_its_own_milestones(self):
        """A redraw keeps the current runner's text until it exits."""
        import io
        from unittest import mock
        self.queen('Ada', 'done', pid=os.getpid())
        conn = self.conn

        class Keep:
            """Forward connection operations without closing the test database."""
            def __getattr__(self, name):
                """Return the corresponding database operation."""
                return getattr(conn, name)

            def close(self):
                """Leave the test database open."""

        keys = mock.MagicMock()
        keys.__enter__.return_value = keys
        with mock.patch.object(hivepane.header, 'read_milestones', return_value=[]) as read, \
                mock.patch.object(hivepane.db, 'connect', return_value=Keep()):
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0, max_ticks=1, keys=keys)
        read.assert_called_once()

    def test_start_closes_idle_hive_panes_and_keeps_the_one_with_a_running_agent(self):
        """Hive panes without a running agent and the head of an Apiary without a busy Hive pane are closed at a start; a Hive pane with a running Queen and its head stay."""
        runtime.upsert_agent(self.conn, 'ODP', 'Dina', {'plan': self.plan, 'phase': 'queen', 'status': 'running', 'pane_id': 'W.5'})
        titles = {'W.2': loop.apiary_pane_title('ODP', False), 'W.5': loop.hive_pane_title('ODP', self.plan, False),
                  'W.6': loop.hive_pane_title('ODP', 'plans/OTHER.md', False), 'W.7': loop.apiary_pane_title('XYZ', False),
                  'W.8': loop.hive_pane_title('XYZ', 'plans/X.md', False)}
        closed = []

        class Term:
            """Lists the panes and records which are closed."""

            def list_panes(self, window):
                """All panes are alive."""
                from haivequeen.terminal import PaneInfo
                return [PaneInfo(i, t, 1, True) for i, t in titles.items()]

            def close_pane(self, pane_id):
                """Record the closed pane."""
                closed.append(pane_id)
        self.assertEqual(sorted(loop.close_idle_hive_panes(self.conn, Term(), 'W')), ['W.6', 'W.7', 'W.8'])

    # Keep a Hive row together while one of its Bees is running.
    def test_restart_keeps_hive_and_apiary_with_a_running_bee(self):
        """Restart preserves the Hive and Apiary around an occupied Bee pane."""
        from haivequeen.terminal import PaneInfo
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'pane_id': 'W.6'})
        titles = {'W.2': loop.apiary_pane_title('ODP', False), 'W.5': loop.hive_pane_title('ODP', self.plan, False), 'W.6': 'Bee'}
        class Term:
            """Record panes closed during restart."""
            def list_panes(self, window):
                """Return the Hive row."""
                return [PaneInfo(i, t, 1, True) for i, t in titles.items()]
            def close_pane(self, pane_id):
                """A running Bee's row must stay."""
                raise AssertionError('closed ' + pane_id)
        self.assertEqual(loop.close_idle_hive_panes(self.conn, Term(), 'W'), [])

    # Keep the Hive and its Apiary until a Bee has ended.
    def test_finished_hive_keeps_its_row_while_bee_runs(self):
        """A completed plan does not remove the row containing a running Bee."""
        from types import SimpleNamespace
        from haivequeen.terminal import PaneInfo
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'pane_id': 'W.6'})
        self.conn.execute("UPDATE hives SET state='done' WHERE project='ODP' AND plan_path=?", (self.plan,))
        titles = {'W.2': loop.apiary_pane_title('ODP', False), 'W.5': loop.hive_pane_title('ODP', self.plan, False), 'W.6': 'Bee'}
        class Term:
            """Return the occupied row."""
            def list_panes(self, window):
                """List all its panes."""
                return [PaneInfo(i, t, 1, True) for i, t in titles.items()]
            def close_pane(self, pane_id):
                """The row cannot be closed yet."""
                raise AssertionError('closed ' + pane_id)
        loop.close_finished_hive_panes(SimpleNamespace(conn=self.conn, name='ODP'), Term(), 'W')

    # Keep the Hive row during session shutdown while a Bee runs.
    def test_session_close_keeps_hive_with_running_bee(self):
        """Session shutdown leaves a running Bee and its Hive and Apiary together."""
        from haivequeen.terminal import PaneInfo
        runtime.upsert_agent(self.conn, 'ODP', 'Bea', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'pane_id': 'W.6'})
        self.conn.execute("UPDATE hives SET state='done' WHERE project='ODP' AND plan_path=?", (self.plan,))
        titles = {'W.2': loop.apiary_pane_title('ODP', False), 'W.5': loop.hive_pane_title('ODP', self.plan, False), 'W.6': 'Bee'}
        class Term:
            """Return the occupied row."""
            def list_panes(self, window):
                """List all its panes."""
                return [PaneInfo(i, t, 1, True) for i, t in titles.items()]
            def close_pane(self, pane_id):
                """No occupied row may be closed."""
                raise AssertionError('closed ' + pane_id)
        self.assertEqual(loop.close_session_panes(self.conn, Term(), 'W'), [])

    # Old overview panes must also stay while an agent occupies one.
    def test_restart_keeps_an_occupied_old_beekeeper_pane(self):
        """A running agent's old Beekeeper pane is not closed on restart."""
        runtime.upsert_agent(self.conn, 'ODP', 'Dina', {'plan': self.plan, 'phase': 'queen', 'status': 'running', 'pane_id': 'W.1'})
        from haivequeen.terminal import PaneInfo
        class Term:
            """Record attempts to close the old pane."""
            def list_panes(self, window):
                """Return the occupied old Beekeeper pane."""
                return [PaneInfo('W.1', loop.queen_pane_title('m', False), 1, True)]
            def close_pane(self, pane_id):
                """Fail if the occupied pane is closed."""
                raise AssertionError('occupied pane was closed')
        self.assertEqual(loop.close_leftover_panes(Term(), 'W', self.conn), [])

    # After the agent ends, only the obsolete pane is removed during a round.
    def test_round_closes_idle_old_beekeeper_but_keeps_dashboard(self):
        """An obsolete Beekeeper pane is removed without closing the dashboard."""
        from haivequeen.terminal import PaneInfo
        closed = []
        class Term:
            """Two live overview panes."""
            def list_panes(self, window):
                """Return the old and current panes."""
                return [PaneInfo('W.1', loop.queen_pane_title('m', False), 1, True), PaneInfo('W.2', 'Dashboard', 2, True)]
            def close_pane(self, pane_id):
                """Record a closed pane."""
                closed.append(pane_id)
        loop.close_leftover_panes(Term(), 'W', self.conn, keep_dashboard=True)
        self.assertEqual(closed, ['W.1'])

    # A kept old Hive pane is rebuilt when its Queen later finishes.
    def test_restart_marks_running_hive_for_rebuild_after_it_ends(self):
        """A kept old Hive pane is rebuilt when its Queen later finishes."""
        runtime.upsert_agent(self.conn, 'ODP', 'Dina', {'plan': self.plan, 'phase': 'queen', 'status': 'running', 'pane_id': 'W.5'})
        from haivequeen.terminal import PaneInfo
        class Term:
            """A live Hive pane from the old window."""
            def list_panes(self, window):
                """Return the occupied pane."""
                return [PaneInfo('W.5', loop.hive_pane_title('ODP', self_plan, False), 1, True)]
        self_plan = self.plan
        loop.close_idle_hive_panes(self.conn, Term(), 'W')
        self.assertEqual(runtime.kv_get(self.conn, 'ODP', 'hive-big-' + Path(self.plan).stem), 'W.5')

    def test_no_word_project_in_the_texts_the_owner_reads(self):
        """The Beekeeper header, the log lines of the pane and the fault and error texts say Apiary, never project."""
        import inspect
        from haivequeen import faults, runtime, triage
        import datetime
        header = " ".join(t for t, _ in loop.head_lines(80, {"projects": ["a", "b"], "since": datetime.datetime.now(), "next_at": None, "busy": False}))
        self.assertNotIn("Project", header)
        for fn in (faults._check_agents, faults._check_idle):
            self.assertNotRegex(inspect.getsource(fn), r"\"[^\"]*[Pp]roject %s")
        self.assertNotRegex(inspect.getsource(runtime), r"f\"project \{project\} has no workspace")
        self.assertNotRegex(inspect.getsource(triage), r"not active in \{project\}")
        self.assertIn('"[Apiary %s] %s"', inspect.getsource(loop.Pane))


class HiveShareByQueenTest(PlacementCase):
    """F9: a Hive without a running Queen gets a narrow share from the start; when a Queen starts in it, its pane is built again with a larger share as long as no agent runs in it."""

    # A new row waits until the previous row's Bee pane has closed.
    def test_new_hive_waits_for_previous_bee_pane(self):
        env = self.two_hives()
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, pane)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=REL, pane_id=bee)
        self.assertTrue(loop.hive_row_waits_for_bees(env, self.term, 'HaiveQueen', self.OTHER))
        self.conn.execute("UPDATE agents SET status='done', pid=NULL WHERE name='Bea'")
        self.assertTrue(loop.hive_row_waits_for_bees(env, self.term, 'HaiveQueen', self.OTHER))
        with self.assertRaises(loop.TerminalError):
            loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.term.close_pane(bee)
        self.assertFalse(loop.hive_row_waits_for_bees(env, self.term, 'HaiveQueen', self.OTHER))
        self.assertTrue(loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER))

    # Do not insert an earlier Hive below a later occupied Hive.
    def test_earlier_hive_waits_for_later_occupied_row(self):
        """The missing earlier row waits while a later Hive contains an agent."""
        env = self.two_hives()
        later = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id=later)
        self.assertTrue(loop.hive_row_waits_for_bees(env, self.term, 'HaiveQueen', REL))
        with self.assertRaises(loop.TerminalError):
            loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertTrue(any(p.id == later and p.alive for p in self.term.list_panes('HaiveQueen')))

    # Rebuild an idle later row after inserting the missing earlier row.
    def test_earlier_hive_rebuilds_later_idle_row(self):
        """Idle later rows are recreated below the missing earlier Hive."""
        env = self.two_hives()
        later = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = [p for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)]
        panes.sort(key=loop._pane_order)
        self.assertEqual([p.title for p in panes], [loop.hive_pane_title(env.name, plan, True) for plan in (REL, self.OTHER)])
        self.assertNotIn(later, [p.id for p in panes])

    # A finished Bee can still own a visible pane during its close delay.
    def test_earlier_hive_waits_for_later_bee_pane_to_close(self):
        """The later Hive stays in place while its finished Bee pane is visible."""
        env = self.two_hives()
        later = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, later)
        self.agent('Bea', phase='bee', status='done', plan_path=self.OTHER, pane_id=bee)
        with self.assertRaises(loop.TerminalError):
            loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertTrue(any(p.id == later and p.alive for p in self.term.list_panes('HaiveQueen')))

    # A Bee still needs its Hive row after the Queen has ended.
    def test_ended_queen_keeps_hive_pane_while_bee_runs(self):
        """An active Bee prevents its Hive pane from being rebuilt."""
        env = self.two_hives()
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        runtime.kv_set(self.conn, 'testproj', 'hive-big-' + Path(REL).stem, pane)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, pane)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=REL, pane_id=bee)
        loop.rebuild_ended_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertTrue(any(p.id == pane and p.alive for p in self.term.list_panes('HaiveQueen')))

    # A new Queen cannot rebuild the row while its Bee still runs.
    def test_starting_queen_keeps_hive_pane_while_bee_runs(self):
        """A Bee pane and its Hive pane remain together during a Queen start."""
        env = self.two_hives()
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, pane)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=REL, pane_id=bee)
        loop.rebuild_small_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertTrue(any(p.id == pane and p.alive for p in self.term.list_panes('HaiveQueen')))

    # Rebuilding an earlier row must preserve later rows with running Bees.
    def test_rebuild_keeps_following_hive_with_running_bee(self):
        """A later Hive is not closed while its Bee has a separate pane."""
        env = self.two_hives()
        first = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        later = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, later)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id=bee)
        loop.rebuild_small_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertTrue(any(p.id == later and p.alive for p in self.term.list_panes('HaiveQueen')))
        self.assertTrue(any(p.id == first and p.alive for p in self.term.list_panes('HaiveQueen')))
    OTHER = "other/B-PLAN.md"

    # A new Hive can use an adjacent empty pane while repair waits for an agent.
    def test_pending_repair_splits_adjacent_idle_hive(self):
        """An occupied pane stays open while the next idle pane provides space."""
        env = self.envs()[0]
        third = 'plans/C-PLAN.md'
        for plan in (REL, self.OTHER, third):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        idle = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=occupied, pid=os.getpid())
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', third)
        self.assertEqual(self.calls[-1][0], idle)
        self.assertTrue(any(p.id == occupied and p.alive for p in self.term.list_panes('HaiveQueen')))

    # A pending repair can place a Hive when only an occupied row exists.
    def test_pending_repair_takes_only_head_height_from_occupied_hive(self):
        """The new idle row takes its head height without closing the Queen pane."""
        env = self.envs()[0]
        for plan in (REL, self.OTHER):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=occupied, pid=os.getpid())
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        runtime.kv_set(self.conn, 'testproj', 'hive-rows-' + Path(REL).stem, '20')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.assertEqual(self.calls[-1][0], occupied)
        self.assertAlmostEqual(self.calls[-1][2], 0.2)
        self.assertTrue(any(p.id == occupied and p.alive for p in self.term.list_panes('HaiveQueen')))

    # A starting Queen does not take more than the new row's minimum from an occupied row.
    def test_starting_hive_takes_only_head_height_from_occupied_hive(self):
        """A new starting Hive preserves the occupied row apart from its minimum share."""
        env = self.envs()[0]
        for plan in (REL, self.OTHER):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=occupied, pid=os.getpid())
        self.agent('Bea', phase='queen', status='starting', plan_path=self.OTHER)
        runtime.kv_set(self.conn, 'testproj', 'hive-rows-' + Path(REL).stem, '20')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.assertEqual(self.calls[-1][0], occupied)
        self.assertAlmostEqual(self.calls[-1][2], 0.2)
        self.assertTrue(any(p.id == occupied and p.alive for p in self.term.list_panes('HaiveQueen')))

    # Reserve the minimum for each missing row when splitting an occupied Hive.
    def test_starting_hives_share_minimum_space_taken_from_occupied_hive(self):
        """Two missing rows take only their combined minimum from the Queen."""
        env = self.envs()[0]
        third = 'plans/C-PLAN.md'
        for plan in (REL, self.OTHER, third):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=occupied, pid=os.getpid())
        self.agent('Bea', phase='queen', status='starting', plan_path=self.OTHER)
        self.agent('Cora', phase='queen', status='starting', plan_path=third)
        runtime.kv_set(self.conn, 'testproj', 'hive-rows-' + Path(REL).stem, '20')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.assertEqual(self.calls[-1][0], occupied)
        self.assertAlmostEqual(self.calls[-1][2], 0.4)

    # Start the Queen through its Hive while repair is waiting on another Queen.
    def test_pending_repair_starts_queen_without_closing_occupied_hive(self):
        """The replacement pane is ready for its Queen and keeps the running pane."""
        from unittest import mock
        env = self.envs()[0]
        for plan in (REL, self.OTHER):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=occupied, pid=os.getpid())
        self.agent('Bea', phase='queen', status='starting', plan_path=self.OTHER)
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        runtime.kv_set(self.conn, 'testproj', 'hive-rows-' + Path(REL).stem, '20')
        with mock.patch.object(loop, 'runner_argv', return_value=SLEEP):
            pane = loop.start_queen(env, self.term, 'HaiveQueen', {'name': 'Bea', 'plan': self.OTHER})
        self.assertEqual(self.calls[-1][0], occupied)
        self.assertAlmostEqual(self.calls[-1][2], 0.2)
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'Bea')['pane_id'], pane)
        self.assertTrue(any(p.id == occupied and p.alive for p in self.term.list_panes('HaiveQueen')))
        self.assertEqual(runtime.kv_get(self.conn, '*', 'pane-repair-pending'), '1')

    # New idle Hives share the available Apiary height equally.
    def test_new_idle_hive_rebuilds_existing_idle_hives(self):
        """Adding an idle Hive rebuilds idle panes to divide the height equally."""
        env = self.envs()[0]
        plans = [REL, self.OTHER, 'other/C-PLAN.md']
        for plan in plans:
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, [env], self.term)
        old = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)}
        hives.create(self.conn, 'testproj', 'other/D-PLAN.md', 'claude', 'white')
        self.calls.clear()
        hives.ensure_hive_panes(self.conn, [env], self.term)
        current = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)}
        self.assertEqual(len(current), 4)
        self.assertFalse(old & current)
        shares = [size for _, direction, size in self.calls if direction == 'vertical'][1:]
        self.assertEqual(len(shares), 3)
        for actual, expected in zip(shares, (0.75, 2 / 3, 0.5)):
            self.assertAlmostEqual(actual, expected)

    # When a Hive finishes, the remaining idle Hives share its space.
    def test_finished_idle_hive_rebuilds_remaining_idle_hives(self):
        """Removing an idle Hive redistributes the height among the others."""
        env = self.envs()[0]
        plans = [REL, self.OTHER, 'other/C-PLAN.md', 'other/D-PLAN.md']
        for plan in plans:
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, [env], self.term)
        old = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)}
        self.conn.execute("UPDATE hives SET state='done' WHERE project=? AND plan_path=?", ('testproj', plans[1]))
        self.calls.clear()
        hives.ensure_hive_panes(self.conn, [env], self.term)
        current = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)}
        self.assertEqual(len(current), 3)
        self.assertFalse(old & current)
        shares = [size for _, direction, size in self.calls if direction == 'vertical'][1:]
        for actual, expected in zip(shares, (2 / 3, 0.5), strict=True):
            self.assertAlmostEqual(actual, expected)

    # An Apiary with no working Queen shares width equally again.
    def test_last_queen_ending_rebuilds_single_apiary_hives(self):
        """All idle Hives regain equal shares after the last Queen ends."""
        env = self.envs()[0]
        plans = [REL, self.OTHER, 'other/C-PLAN.md']
        for plan in plans:
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = [p for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)]
        old = {p.id for p in panes}
        last = next(p for p in panes if p.title == loop.hive_pane_title('testproj', plans[-1], True))
        runtime.kv_set(self.conn, 'testproj', 'hive-big-' + Path(plans[-1]).stem, last.id)
        hives.ensure_hive_panes(self.conn, [env], self.term)
        current = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)}
        self.assertEqual(len(current), 3)
        self.assertFalse(old & current)

    # A round removes an obsolete overview after its agent ends.
    def test_round_removes_idle_old_beekeeper_after_dashboard_exists(self):
        """The obsolete pane closes in a round while the dashboard stays."""
        dashboard = self.term.split_pane('HaiveQueen', 'Dashboard', 'white', SLEEP, 'horizontal', 0.5, self.chief, 'dashboard')
        hives.ensure_hive_panes(self.conn, [self.envs()[0]], self.term)
        live = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive}
        self.assertNotIn(self.chief, live)
        self.assertIn(dashboard, live)

    # Insert a missing Hive before a later occupied pane.
    def test_missing_middle_hive_splits_its_predecessor(self):
        """A missing middle Hive is inserted before the occupied later Hive."""
        env = self.envs()[0]
        middle, last = 'other/M-PLAN.md', 'other/Z-PLAN.md'
        for plan in (REL, middle, last):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        first = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', last)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=last, pane_id=occupied)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', middle)
        self.assertEqual(self.calls[-1][0], first)

    # An old occupied Hive on the left is not an Apiary block on the right.
    def test_old_left_hive_does_not_anchor_new_apiary_head(self):
        """The right-hand Apiary head splits the dashboard, not the old Hive."""
        env = self.envs()[0]
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        old = self.term.split_pane('HaiveQueen', loop.hive_pane_title('testproj', REL, True), 'white', SLEEP, 'vertical', 0.5, self.chief, 'old-hive')
        dashboard = self.term.split_pane('HaiveQueen', 'Dashboard', 'white', SLEEP, 'horizontal', 0.5, self.chief, 'dashboard')
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=old, pid=os.getpid())
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(env, self.term, 'HaiveQueen', live)
        self.assertEqual(self.calls[-1][:2], (dashboard, 'horizontal'))

    # New Hives must not split an old occupied Hive on the left.
    def test_new_hive_splits_right_head_while_old_hive_is_on_left(self):
        """A new Hive starts under the right head while an older Hive remains on the left."""
        env = self.envs()[0]
        new, later = 'other/NEW-PLAN.md', 'other/LATER-PLAN.md'
        for plan in (REL, new, later):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        old = self.term.split_pane('HaiveQueen', loop.hive_pane_title('testproj', REL, True), 'white', SLEEP, 'vertical', 0.5, self.chief, 'old-hive')
        self.term.split_pane('HaiveQueen', 'Dashboard', 'white', SLEEP, 'horizontal', 0.5, self.chief, 'dashboard')
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=old, pid=os.getpid())
        created = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', new)
        head = next(p for p in self.term.list_panes('HaiveQueen') if p.alive and p.title == loop.apiary_pane_title('testproj', True))
        self.assertEqual(self.calls[-1][:2], (head.id, 'vertical'))
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', later)
        self.assertEqual(self.calls[-1][:2], (created, 'vertical'))

    # Migrate an occupied old Hive only after its Queen ends.
    def test_old_left_hive_moves_after_queen_ends(self):
        """A running old Hive has no duplicate and is rebuilt under the right-hand head after it ends."""
        env = self.envs()[0]
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        old = self.term.split_pane('HaiveQueen', loop.hive_pane_title('testproj', REL, True), 'white', SLEEP, 'vertical', 0.5, self.chief, 'old-hive')
        self.term.split_pane('HaiveQueen', 'Dashboard', 'white', SLEEP, 'horizontal', 0.5, self.chief, 'dashboard')
        self.agent('Ada', phase='queen', status='running', plan_path=REL, pane_id=old, pid=os.getpid())
        loop.close_idle_hive_panes(self.conn, self.term, 'HaiveQueen')
        hives.ensure_hive_panes(self.conn, [env], self.term)
        living = [p for p in self.term.list_panes('HaiveQueen') if p.alive and p.title == loop.hive_pane_title('testproj', REL, True)]
        self.assertEqual([p.id for p in living], [old])
        self.conn.execute("UPDATE agents SET status='done',pid=NULL WHERE name='Ada'")
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        living = [p for p in panes if p.title == loop.hive_pane_title('testproj', REL, True)]
        head = next(p for p in panes if p.title == loop.apiary_pane_title('testproj', True))
        self.assertEqual(len(living), 1)
        self.assertNotEqual(living[0].id, old)
        self.assertEqual(self.calls[-1][:2], (head.id, 'vertical'))

    # Rebuild idle blocks before the first Queen so the working block gets the remaining height.
    def test_first_queen_rebuilds_unoccupied_apiary_blocks(self):
        """A starting Queen grows her block while an idle block keeps its Hive head."""
        self.add_project('other')
        envs = self.envs()
        for env in envs:
            hives.create(self.conn, env.name, REL, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, envs, self.term)
        runtime.kv_set(self.conn, '*', 'dashboard-rows', '40')
        self.agent('Ada', phase='queen', status='starting', plan_path=REL)
        before = len(self.calls)
        hives.prepare_blocks_for_queen(self.conn, envs, self.term)
        self.assertGreater(len(self.calls), before)
        self.assertAlmostEqual(self.calls[-1][2], 0.875)

    # An already occupied block must not be rebuilt before an owner decision.
    def test_first_queen_does_not_rebuild_occupied_apiary_blocks(self):
        """A running agent keeps its pane when another Queen is starting."""
        self.add_project('other')
        envs = self.envs()
        for env in envs:
            hives.create(self.conn, env.name, REL, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, envs, self.term)
        pane = next(p for p in self.term.list_panes('HaiveQueen') if p.alive and p.title == loop.hive_pane_title('other', REL, True))
        self.agent('Bea', project='other', phase='queen', status='running', plan_path=REL, pane_id=pane.id, pid=os.getpid())
        self.agent('Ada', phase='queen', status='starting', plan_path=REL)
        before = len(self.calls)
        hives.prepare_blocks_for_queen(self.conn, envs, self.term)
        self.assertEqual(len(self.calls), before)
        self.assertTrue(any(p.id == pane.id and p.alive for p in self.term.list_panes('HaiveQueen')))

    # Keep blocks that this maintenance pass cannot rebuild.
    def test_first_queen_keeps_unmanaged_apiary_blocks(self):
        """An Apiary missing from the current environments is not closed."""
        self.add_project('other')
        envs = self.envs()
        for env in envs:
            hives.create(self.conn, env.name, REL, 'claude', 'white')
        hives.ensure_hive_panes(self.conn, envs, self.term)
        pane = next(p for p in self.term.list_panes('HaiveQueen') if p.alive and p.title == loop.hive_pane_title('other', REL, True))
        self.agent('Ada', phase='queen', status='starting', plan_path=REL)
        hives.prepare_blocks_for_queen(self.conn, [e for e in envs if e.name == 'testproj'], self.term)
        self.assertTrue(any(p.id == pane.id and p.alive for p in self.term.list_panes('HaiveQueen')))

    def test_apiary_heads_are_created_before_any_hives(self):
        """Each new Apiary block is created before its Hive panes."""
        from unittest import mock
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hives.create(self.conn, 'other', self.OTHER, 'claude', 'white')
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
        panes = sorted((p for p in self.term.list_panes('HaiveQueen') if p.alive), key=loop._pane_order)
        titles = [p.title for p in panes]
        self.assertTrue(loop.is_apiary_title(titles[1]))
        self.assertTrue(loop.is_apiary_title(titles[2]))
        self.assertEqual(self.calls[1][:2], (panes[1].id, 'vertical'))

    # Keep configured Apiaries visible even when they have no open Hive.
    def test_apiary_without_hives_keeps_its_head(self):
        """The pane maintenance creates and retains an empty Apiary head."""
        env = self.envs()[0]
        hives.ensure_hive_panes(self.conn, [env], self.term)
        title = loop.apiary_pane_title(env.name, True)
        self.assertTrue(any(p.alive and p.title == title for p in self.term.list_panes('HaiveQueen')))
        hives.ensure_hive_panes(self.conn, [env], self.term)
        self.assertTrue(any(p.alive and p.title == title for p in self.term.list_panes('HaiveQueen')))

    def test_new_apiary_rebuilds_unoccupied_blocks(self):
        """A new Apiary is stacked as a full block when existing panes are empty."""
        from unittest import mock
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        old = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        hives.create(self.conn, 'other', self.OTHER, 'claude', 'white')
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
        panes = sorted((p for p in self.term.list_panes('HaiveQueen') if p.alive), key=loop._pane_order)
        self.assertNotIn(old, [p.id for p in panes])
        self.assertTrue(loop.is_apiary_title(panes[1].title))
        self.assertTrue(loop.is_apiary_title(panes[2].title))

    # A new block waits while a multi-Hive block has an occupied pane.
    def test_new_apiary_waits_for_hive_with_bee_pane(self):
        """A new block waits until the preceding Bee pane closes."""
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hive = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, hive)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=REL, pane_id=bee)
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        self.assertTrue(loop.apiary_waits_for_bees(other, self.term, 'HaiveQueen'))
        with self.assertRaises(loop.TerminalError):
            loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live)
        self.term.close_pane(bee)
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        self.assertFalse(loop.apiary_waits_for_bees(other, self.term, 'HaiveQueen'))
        self.assertTrue(loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live))

    # A Bee in an earlier block does not block splitting a later full-width block.
    def test_new_apiary_does_not_wait_for_bee_in_earlier_block(self):
        """Only the block used as the split target needs to be whole."""
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        third = self.envs()[0]
        third.name = 'third'
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hive = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live)
        bee = self.term.split_pane('HaiveQueen', 'Bee', 'white', SLEEP, 'horizontal', 0.5, hive)
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=REL, pane_id=bee)
        self.assertFalse(loop.apiary_waits_for_bees(third, self.term, 'HaiveQueen'))
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        self.assertTrue(loop.ensure_apiary_pane(third, self.term, 'HaiveQueen', live))

    # A new block waits while a multi-Hive block has an occupied pane.
    def test_new_apiary_waits_for_occupied_multi_hive_block(self):
        """The next full-width block opens after the occupied Hive ends."""
        from unittest import mock
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        for plan in (REL, self.OTHER):
            hives.create(self.conn, 'testproj', plan, 'claude', 'white')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id=occupied)
        hives.create(self.conn, 'other', 'other/Z-PLAN.md', 'claude', 'white')
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
        titles = [p.title for p in self.term.list_panes('HaiveQueen') if p.alive]
        self.assertNotIn(loop.apiary_pane_title('other', True), titles)
        self.assertIn(loop.hive_pane_title('testproj', self.OTHER, True), titles)
        self.conn.execute("UPDATE agents SET status='done', pid=NULL WHERE name='Bea'")
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
        titles = [p.title for p in self.term.list_panes('HaiveQueen') if p.alive]
        self.assertIn(loop.apiary_pane_title('other', True), titles)

    # Rebuilding an earlier Hive does not move it after a later idle Hive.
    def test_starting_earlier_hive_preserves_creation_order(self):
        """Rebuilding an earlier Hive does not move it after a later idle Hive."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.queen_of(REL)
        self.start(env, REL)
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = sorted((p for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)), key=loop._pane_order)
        self.assertEqual([p.title for p in panes], [loop.hive_pane_title('testproj', p, True) for p in (REL, self.OTHER)])

    # Rebuilding must not close a later Hive that still has an agent.
    def test_starting_earlier_hive_keeps_later_occupied_pane(self):
        """Rebuilding must not close a later Hive that still has an agent."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        occupied = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id=occupied)
        self.queen_of(REL)
        self.start(env, REL)
        self.assertIn(occupied, [p.id for p in self.term.list_panes('HaiveQueen') if p.alive])

    # An occupied block only gives up the space needed by a new idle block.
    # Verify that idle Apiary blocks regain equal height after the last Queen ends.
    def test_last_queen_ending_rebuilds_idle_apiary_blocks(self):
        """When no pane is occupied, idle blocks are rebuilt together."""
        from unittest import mock
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hives.create(self.conn, 'other', self.OTHER, 'claude', 'white')
        self.queen_of(REL)
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
            heads = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_apiary_title(p.title)}
            self.conn.execute("UPDATE agents SET status='done' WHERE name='Ada'")
            hives.ensure_hive_panes(self.conn, [env, other], self.term)
        new_heads = {p.id for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_apiary_title(p.title)}
        self.assertEqual(len(heads), 2)
        self.assertEqual(len(new_heads), 2)
        self.assertFalse(heads & new_heads)

    # Verify that the head prepass leaves a just-ended Queen's pane large.
    def test_head_prepass_does_not_shrink_ended_queen_pane(self):
        """A possible next Queen gets the existing large pane until she starts."""
        env = self.two_hives()
        self.queen_of(REL)
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.conn.execute("UPDATE agents SET status='done' WHERE name='Ada'")
        hives.ensure_hive_panes(self.conn, [env], self.term, heads_only=True)
        self.assertIn(pane, [p.id for p in self.term.list_panes('HaiveQueen') if p.alive])

    # Verify that existing Apiary heads precede the first Queen launch.
    def test_round_places_all_existing_apiaries_before_queen_launch(self):
        """Starting a Queen does not prevent another existing block from being placed."""
        from unittest import mock
        self.add_project('other')
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.plan('READY', [{'id': 'P1', 'title': 'First'}], project='other')
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hives.create(self.conn, 'other', REL, 'claude', 'white')
        seen = []
        def runner(env, spec):
            """Record the existing Apiary heads before starting the runner."""
            seen.append({p.title for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_apiary_title(p.title)})
            return SLEEP
        with mock.patch.object(loop, 'runner_argv', side_effect=runner):
            self.round(terminal=self.term, poll=0.01, tries=1)
        self.assertTrue(seen)
        self.assertTrue({loop.apiary_pane_title(n, True) for n in ('testproj', 'other')} <= seen[0])

    # A rebuilt earlier Hive must not become the bottom Apiary split target.
    def test_new_apiary_uses_terminal_tree_order_after_hive_rebuild(self):
        """The new block follows the bottom block, not the newest pane ID."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        env = self.envs()[0]
        test = loop.is_test_runtime()
        panes = [PaneInfo('W.1', loop.apiary_pane_title('first', test), 1, True),
                 PaneInfo('W.5', loop.hive_pane_title('first', REL, test), 1, True),
                 PaneInfo('W.2', loop.apiary_pane_title('second', test), 1, True),
                 PaneInfo('W.4', loop.hive_pane_title('second', REL, test), 1, True)]
        term = mock.Mock()
        term.name = 'wt'
        term.list_panes.return_value = panes
        term.wt_order.return_value = [1, 5, 2, 4]
        term.split_pane.return_value = 'W.6'
        loop.ensure_apiary_pane(env, term, 'W', panes)
        self.assertEqual(term.split_pane.call_args.args[6], 'W.4')

    # A Bee in the bottom block must still defer the next Apiary.
    def test_new_apiary_waits_for_bee_in_bottom_tree_block(self):
        """A newer pane in an earlier block does not hide the bottom Bee."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo
        self.add_project('second')
        env = next(e for e in self.envs() if e.name == 'testproj')
        test = loop.is_test_runtime()
        panes = [PaneInfo('W.1', loop.apiary_pane_title('first', test), 1, True),
                 PaneInfo('W.5', loop.hive_pane_title('first', REL, test), 1, True),
                 PaneInfo('W.2', loop.apiary_pane_title('second', test), 1, True),
                 PaneInfo('W.4', loop.hive_pane_title('second', REL, test), 1, True),
                 PaneInfo('W.6', 'Bee', 1, True)]
        self.agent('Bea', project='second', phase='bee', status='running', plan_path=REL, pane_id='W.6', pid=os.getpid())
        term = mock.Mock()
        term.name = 'wt'
        term.wt_order.return_value = [1, 5, 2, 4, 6]
        self.assertTrue(loop.apiary_waits_for_bees(env, term, 'W', panes))

    # Verify that an Apiary cannot split an unrelated focused pane.
    def test_apiary_needs_a_known_split_target(self):
        """An unknown live pane is not silently chosen as the Apiary target."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo, TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.name = 'wt'
        with self.assertRaises(TerminalError):
            loop.ensure_apiary_pane(env, term, 'HaiveQueen', [PaneInfo('W.0', 'unrelated', 1, True)])
        term.split_pane.assert_not_called()

    # Verify that an agent cannot split an unrelated focused pane.
    def test_agent_needs_a_known_split_target(self):
        """An unknown live pane is not silently chosen as the agent target."""
        from unittest import mock
        from haivequeen.terminal import PaneInfo, TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.name = 'wt'
        term.list_panes.return_value = [PaneInfo('W.0', 'unrelated', 1, True)]
        spec = {'name': 'Ada', 'person': 'Ada', 'model_long': 'model'}
        with mock.patch.object(loop, 'runner_argv', return_value=SLEEP):
            with self.assertRaises(TerminalError):
                loop.launch_agent(env, term, 'HaiveQueen', spec, settle=0, poll=0, tries=1)
        term.split_pane.assert_not_called()

    # Verify that a legacy agent launch cannot open an extra window.
    def test_agent_launch_does_not_open_a_window_when_all_panes_are_gone(self):
        """A missing terminal is reported without opening a new window."""
        from unittest import mock
        from haivequeen.terminal import TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.name = 'wt'
        term.list_panes.return_value = []
        spec = {'name': 'Ada', 'person': 'Ada', 'model_long': 'model'}
        with mock.patch.object(loop, 'runner_argv', return_value=SLEEP):
            with self.assertRaises(TerminalError):
                loop.launch_agent(env, term, 'HaiveQueen', spec, settle=0, poll=0, tries=1)
        term.open_window.assert_not_called()

    # Verify that the loop cannot open a second terminal window.
    def test_apiary_creation_does_not_open_a_window_when_all_panes_are_gone(self):
        """A missing window is reported instead of opening a new one."""
        from unittest import mock
        from haivequeen.terminal import TerminalError
        env = self.envs()[0]
        term = mock.Mock()
        term.name = 'wt'
        with self.assertRaises(TerminalError):
            loop.ensure_apiary_pane(env, term, 'HaiveQueen', [])
        term.open_window.assert_not_called()

    # Check that initial Apiary splits reserve space for later blocks.
    def test_fresh_three_apiaries_reserve_a_hive_head_for_each_idle_block(self):
        """Successive splits leave both idle blocks an Apiary and a Hive head."""
        from unittest import mock
        names = ('testproj', 'other', 'third')
        envs = []
        for name in names:
            env = self.envs()[0]
            env.name = name
            envs.append(env)
            if name != 'testproj':
                db.upsert_project(self.conn, name, None, 'white', 0)
            hives.create(self.conn, name, REL, 'claude', 'white')
        self.queen_of(REL)
        runtime.kv_set(self.conn, '*', 'dashboard-rows', '40')
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': n} for n in names]}):
            for env in envs:
                live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
                loop.ensure_apiary_pane(env, self.term, 'HaiveQueen', live)
        self.assertAlmostEqual(self.calls[-2][2], 0.25)
        self.assertAlmostEqual(self.calls[-1][2], 0.5)

    # Verify that the first Hive uses its new head's reported height.
    def test_first_hive_waits_for_new_head_measurement(self):
        """A new head reports its height before the Hive is split below it."""
        from unittest import mock
        env = self.two_hives()
        self.term.name = 'wt'
        self.term.start_timeout = 1
        original = runtime.kv_get
        measured = []
        def get(conn, project, key):
            """Return the first measurement for the new head."""
            if key == 'apiary-rows':
                measured.append(key)
                return '4'
            if key == 'apiary-measure-ack':
                measured.append(key)
                return original(conn, project, 'apiary-measure-request')
            return original(conn, project, key)
        with mock.patch.object(runtime, 'kv_get', side_effect=get):
            loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertIn('apiary-measure-ack', measured)
        self.assertIn('apiary-rows', measured)
        self.assertEqual(self.calls[-1][2], 0.75)

    # Verify that a fresh idle Apiary block starts at its minimum height.
    def test_fresh_idle_apiary_reserves_hive_head_beside_working_block(self):
        """A working block keeps the remaining height during initial layout."""
        from unittest import mock
        env = self.envs()[0]
        other = self.envs()[0]
        other.name = 'other'
        db.upsert_project(self.conn, 'other', None, 'white', 0)
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        hives.create(self.conn, 'other', self.OTHER, 'claude', 'white')
        self.queen_of(REL)
        runtime.kv_set(self.conn, '*', 'dashboard-rows', '40')
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'testproj'}, {'name': 'other'}]}):
            live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
            loop.ensure_apiary_pane(env, self.term, 'HaiveQueen', live)
            live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
            loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live)
        self.assertAlmostEqual(self.calls[-1][2], 0.125)

    def test_new_apiary_takes_only_minimum_height_from_occupied_pane(self):
        """An occupied block only gives up the space needed by a new idle block."""
        env = self.envs()[0]
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=REL, pane_id=pane, paneRows=40)
        other = self.envs()[0]
        other.name = 'other'
        db.upsert_project(self.conn, 'other', None, 'white', 0)
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live)
        self.assertEqual(self.calls[-1][0], pane)
        self.assertAlmostEqual(self.calls[-1][2], 1 / 40)

    # A new Apiary needs a head and room for each idle Hive head.
    def test_new_apiary_reserves_height_for_all_hive_heads(self):
        """A new block reserves one Apiary row and four per Hive."""
        env = self.envs()[0]
        hives.create(self.conn, 'testproj', REL, 'claude', 'white')
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=REL, pane_id=pane, paneRows=40)
        other = self.envs()[0]
        other.name = 'other'
        db.upsert_project(self.conn, 'other', None, 'white', 0)
        hives.create(self.conn, 'other', REL, 'claude', 'white')
        hives.create(self.conn, 'other', self.OTHER, 'claude', 'white')
        live = [p for p in self.term.list_panes('HaiveQueen') if p.alive]
        loop.ensure_apiary_pane(other, self.term, 'HaiveQueen', live)
        self.assertAlmostEqual(self.calls[-1][2], 9 / 40)

    def test_hives_open_in_creation_order(self):
        """Hive panes follow creation order rather than plan name."""
        env = self.envs()[0]
        hives.create(self.conn, "testproj", "z/Z-PLAN.md", "claude", "4FA8FF")
        hives.create(self.conn, "testproj", "a/A-PLAN.md", "claude", "4FA8FF")
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = sorted((p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_hive_title(p.title)), key=loop._pane_order)
        names = [p.title for p in panes]
        self.assertIn("Z-PLAN", names[0])
        self.assertIn("A-PLAN", names[1])

    def two_hives(self):
        """Two living Hives of the project and its environment."""
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        hives.create(self.conn, "testproj", self.OTHER, "claude", "4FA8FF")
        return self.envs()[0]

    # Allocate the remaining height to Hive rows with running Queens.
    def test_hive_rows_give_idle_rows_only_their_head_height(self):
        from haivequeen import hivelayout
        self.two_hives()
        self.queen_of(self.OTHER)
        self.assertEqual(hivelayout.hive_row_heights(self.conn, 'testproj', [self.OTHER, REL], 30),
                         {self.OTHER: 26, REL: 4})

    # Divide the remaining height equally between working Hive rows.
    def test_hive_rows_share_height_between_working_queens(self):
        from haivequeen import hivelayout
        env = self.two_hives()
        third = 'other/C-PLAN.md'
        hives.create(self.conn, 'testproj', third, 'claude', 'white')
        self.agent('Ada', phase='queen', status='running', plan_path=self.OTHER)
        self.agent('Bea', phase='queen', status='running', plan_path=REL)
        self.assertEqual(hivelayout.hive_row_heights(self.conn, env.name, [self.OTHER, REL, third], 30),
                         {self.OTHER: 13, REL: 13, third: 4})

    # Open the next Hive below the previous Hive.
    def test_second_hive_is_split_below_first(self):
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.assertEqual(self.calls[-1][1], 'vertical')

    # Give a new idle Hive only the height of its head.
    def test_idle_hive_split_uses_measured_row_height(self):
        env = self.two_hives()
        self.queen_of(REL)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        runtime.kv_set(self.conn, 'testproj', 'hive-rows-' + Path(REL).stem, '30')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.assertAlmostEqual(self.calls[-1][2], 4 / 30)

    def queen_of(self, plan, status="starting"):
        """The Queen row of a Hive."""
        return self.agent("Ada", phase="queen", status=status, plan_path=plan)

    def start(self, env, plan):
        """Start the Queen Ada of the Hive `plan` through start_queen and return its pane id."""
        from unittest import mock
        with mock.patch.object(loop, "runner_argv", return_value=SLEEP):
            return loop.start_queen(env, self.term, "HaiveQueen", {"name": "Ada", "plan": plan})

    def test_a_hive_without_queen_gets_the_narrow_share(self):
        """Hive B (no Queen) beside Hive A that has a Queen takes a quarter of the width; beside a Hive without Queen it takes half."""
        hives.create(self.conn, 'testproj', self.OTHER, 'claude', '4FA8FF')
        hives.create(self.conn, 'testproj', REL, 'claude', '4FA8FF')
        env = self.envs()[0]
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertEqual(self.calls[-1][2], 0.5)
        self.term_reset()
        self.queen_of(self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertEqual(self.calls[-1][2], 0.25)

    def test_idle_hive_uses_readable_width_when_queen_is_running(self):
        """The idle Hive gets its actual head width, not a fixed fraction."""
        hives.create(self.conn, 'testproj', self.OTHER, 'claude', '4FA8FF')
        hives.create(self.conn, 'testproj', REL, 'claude', '4FA8FF')
        env = self.envs()[0]
        self.queen_of(self.OTHER)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        runtime.kv_set(self.conn, 'testproj', 'apiary-columns', '100')
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertAlmostEqual(self.calls[-1][2], len('Queen: wartet auf Platz') / 100 + 0.04)

    def test_no_queen_keeps_an_occupied_pane_width(self):
        """Idle Hives share remaining width without resizing an occupied pane."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='queen', status='done', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=70)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 100)
        self.assertEqual(widths, {self.OTHER: 70, REL: 30})

    # A new Hive gets its narrow width even when the occupied pane fills the block.
    def test_no_queen_with_full_occupied_width_keeps_new_pane_readable(self):
        """A new Hive gets its narrow width even when the occupied pane fills the block."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='queen', status='done', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=100)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 100)
        self.assertEqual(widths[REL], len('Queen: wartet auf Platz') + 4)
        self.assertEqual(sum(widths.values()), 100)

    # Idle Hives share the width equally when none has a running Queen.
    def test_idle_hives_share_available_width_equally(self):
        """Idle Hives share the width equally when none has a running Queen."""
        from haivequeen import hivelayout
        self.two_hives()
        self.assertEqual(len(set(hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 20).values())), 1)

    # Idle Hives divide the remaining width equally, even when their heads differ.
    def test_idle_hives_share_equally_beside_occupied_pane(self):
        """Idle Hives divide the remaining width equally, even when their heads differ."""
        from haivequeen import hivelayout
        self.two_hives()
        third = 'plans/AN-EXCEPTIONALLY-LONG-HIVE-NAME-FOR-TESTING.md'
        hives.create(self.conn, 'testproj', third, 'claude', 'white')
        self.agent('Bea', phase='queen', status='done', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=70)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL, third], 100)
        self.assertEqual(widths[REL], widths[third])

    # A running Queen gives up only the width needed for the new Hive.
    def test_new_idle_hive_has_narrow_width_beside_running_queen(self):
        """A running Queen gives up only the width needed for the new Hive."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=100)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 100)
        self.assertEqual(widths[REL], len('Queen: wartet auf Platz') + 4)
        self.assertEqual(sum(widths.values()), 100)

    # Any agent actually running in a Hive pane retains its width.
    def test_non_queen_agent_keeps_occupied_hive_width(self):
        """Any agent actually running in a Hive pane retains its width."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='work', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=70)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 100)
        self.assertEqual(widths[self.OTHER], 70)

    # A Bee process does not occupy its Hive pane.
    def test_separate_bee_process_does_not_fix_hive_width(self):
        """A Bee process does not occupy its Hive pane."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='bee', status='running', pid=os.getpid(), plan_path=self.OTHER, paneCols=80)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 100)
        self.assertEqual(widths[self.OTHER], widths[REL])

    # A split of the occupied Hive leaves the Queen its remaining width.
    def test_new_hive_takes_only_narrow_width_from_occupied_pane(self):
        """A split of the occupied Hive leaves the Queen its remaining width."""
        hives.create(self.conn, 'testproj', self.OTHER, 'claude', '4FA8FF')
        hives.create(self.conn, 'testproj', REL, 'claude', '4FA8FF')
        env = self.envs()[0]
        pane = loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        runtime.kv_set(self.conn, 'testproj', 'apiary-columns', '100')
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id=pane, paneCols=100)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        self.assertEqual(self.calls[-1][0], pane)
        self.assertAlmostEqual(self.calls[-1][2], (len('Queen: wartet auf Platz') + 4) / 100)

    # A crowded block still produces a valid split ratio.
    def test_overfull_block_keeps_positive_width_for_occupied_pane(self):
        """A crowded block still produces a valid split ratio."""
        from haivequeen import hivelayout
        self.two_hives()
        self.agent('Bea', phase='queen', status='running', pid=os.getpid(), plan_path=self.OTHER, pane_id='W.1', paneCols=10)
        widths = hivelayout.hive_widths(self.conn, 'testproj', [self.OTHER, REL], 10)
        self.assertGreater(widths[self.OTHER], 0)

    def term_reset(self):
        """Close the Hive and Apiary panes of the project so the next placement starts clean."""
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive and (loop.is_hive_title(p.title) or loop.is_apiary_title(p.title)):
                self.term.close_pane(p.id)
        self.calls.clear()

    # The pane of Hive A was built without a Queen; when Ada starts there and no agent runs in it, the pane is closed and built again with 3/4 of the space.
    def test_a_starting_queen_gets_her_pane_built_again_larger(self):
        """The pane of Hive A was built without a Queen; when Ada starts there and no agent runs in it, the pane is closed and built again with 3/4 of the space."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.queen_of(REL)
        new = self.start(env, REL)
        self.assertNotEqual(new, old)
        self.assertNotIn(old, [p.id for p in self.term.list_panes("HaiveQueen") if p.alive])
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        self.assertEqual(self.calls[-1][2], 0.25)
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "Ada")["pane_id"], new)

    def test_a_pane_with_a_running_agent_keeps_its_size(self):
        """While an agent runs in the pane it is not closed, whatever the Queen start wants."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.agent("Bea", phase="bee", status="running", plan_path=REL, pane_id=old)
        self.queen_of(REL)
        before = len(self.calls)
        self.assertEqual(self.start(env, REL), old)
        self.assertEqual(len(self.calls), before)

    def test_a_pane_built_with_its_queen_is_not_built_again(self):
        """A second Queen start in a pane that was already built large leaves it alone."""
        env = self.two_hives()
        self.queen_of(REL)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        first = self.start(env, REL)
        self.assertEqual(self.start(env, REL), first)

    def test_ended_queen_pane_is_rebuilt_narrow(self):
        """After the Queen ends, her empty large pane is replaced."""
        env = self.two_hives()
        self.queen_of(REL)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.conn.execute("UPDATE agents SET status='done' WHERE name='Ada'")
        hives.ensure_hive_panes(self.conn, [env], self.term)
        new = next(p.id for p in self.term.list_panes("HaiveQueen") if p.alive and p.title == loop.hive_pane_title("testproj", REL, loop.is_test_runtime()))
        self.assertNotEqual(old, new)

    # Shrinking an ended Hive keeps it before a later idle Hive.
    def test_ended_earlier_hive_preserves_creation_order(self):
        """Shrinking an ended Hive keeps it before a later idle Hive."""
        env = self.two_hives()
        self.queen_of(REL)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', REL)
        loop.ensure_hive_pane(env, self.term, 'HaiveQueen', self.OTHER)
        self.conn.execute("UPDATE agents SET status='done' WHERE name='Ada'")
        hives.ensure_hive_panes(self.conn, [env], self.term)
        panes = sorted((p for p in self.term.list_panes('HaiveQueen') if p.alive and loop.is_hive_title(p.title)), key=loop._pane_order)
        self.assertEqual([p.title for p in panes], [loop.hive_pane_title('testproj', p, True) for p in (REL, self.OTHER)])

    def test_ended_queen_with_live_process_keeps_pane(self):
        """A Queen process that has not exited keeps its pane after reporting done."""
        env = self.two_hives()
        self.queen_of(REL)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.conn.execute("UPDATE agents SET status='done',pid=123456,pane_id=? WHERE name='Ada'", (old,))
        from unittest import mock
        with mock.patch('haivequeen.platform.process_alive', return_value=True):
            hives.ensure_hive_panes(self.conn, [env], self.term)
        self.assertIn(old, [p.id for p in self.term.list_panes("HaiveQueen") if p.alive])

    def test_running_queen_keeps_large_pane(self):
        """A running Queen keeps her existing large pane during a round."""
        env = self.two_hives()
        self.queen_of(REL, "running")
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        hives.ensure_hive_panes(self.conn, [env], self.term)
        self.assertIn(old, [p.id for p in self.term.list_panes("HaiveQueen") if p.alive])

    def test_next_queen_gets_new_pane_in_same_round(self):
        """A replacement Queen gets a new pane even if the old pane is large."""
        env = self.two_hives()
        self.queen_of(REL)
        old = self.start(env, REL)
        self.conn.execute("UPDATE agents SET status='done' WHERE name='Ada'")
        self.agent("Bea", phase="queen", status="starting", plan_path=REL)
        from unittest import mock
        with mock.patch.object(loop, "runner_argv", return_value=SLEEP):
            new = loop.start_queen(env, self.term, "HaiveQueen", {"name": "Bea", "plan": REL})
        self.assertNotEqual(old, new)
