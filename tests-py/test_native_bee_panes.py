"""Native Bee panes show progress without creating provider processes."""
from unittest import mock
from io import StringIO
import sys
import time

from bzztest import REL, RoundCase
from haivequeen import bees, hivepane, hives, loop, native_bee, runtime
from haivequeen.terminal.base import PaneInfo, TerminalError, pane_title
from haivequeen.terminal.headless import Headless


class NativeBeePaneTests(RoundCase):
    """Native Bees have their own status pane and no agent process row."""

    # Keep a completed Hive's pane while its native Bee still runs.
    def test_finished_hive_waits_for_native_bee(self):
        """A native Bee keeps the Hive pane alive until its result arrives."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'task', term, 'window')
        self.conn.execute("UPDATE plans SET status='DONE' WHERE project=? AND path=?", (env.name, REL))
        self.assertFalse(hivepane.finished(self.conn, env.name, REL))
        native_bee.finish(self.conn, env.name, REL, 'P1')
        self.assertTrue(hivepane.finished(self.conn, env.name, REL))

    # Run the actual pane process against an isolated runtime.
    def test_native_pane_process_draws_and_exits(self):
        """The launched pane displays its Bee and exits after the result."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        term = Headless(self.base / 'panes')
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        hive = term.open_window('window', title, [sys.executable, '-c', 'import time;time.sleep(30)'], name='hive')
        pane = None
        try:
            pane = native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
            output = self.base / 'panes' / term.registry.find(pane)['name'] / 'output.log'
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and (not output.exists() or 'Bee: ADA first task' not in output.read_text(encoding='utf-8', errors='replace')):
                time.sleep(0.05)
            self.assertIn('Bee: ADA first task', output.read_text(encoding='utf-8', errors='replace'))
            runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 progress', kind='note', agent='Cleo', mirror='none')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and 'Bee: ADA progress 1/2' not in output.read_text(encoding='utf-8', errors='replace'):
                time.sleep(0.05)
            self.assertIn('Bee: ADA progress 1/2', output.read_text(encoding='utf-8', errors='replace'))
            native_bee.finish(self.conn, env.name, REL, 'P1')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(p.id == pane and p.alive for p in term.list_panes('window')):
                time.sleep(0.05)
            self.assertFalse(any(p.id == pane and p.alive for p in term.list_panes('window')))
        finally:
            for item in term.list_panes('window'):
                if item.alive:
                    term.close_pane(item.id)

    # Start the display in the Hive row and close it by reporting the result.
    def test_native_pane_follows_hive_and_finishes_with_result(self):
        """The status pane remains separate from a native provider process."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        self.assertEqual(term.split_pane.call_args.args[4:7], ('horizontal', 0.5, 'window.1'))
        self.assertEqual(native_bee.get(self.conn, env.name, REL, 'P1')['pane'], 'window.2')
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, env.name, 'all')))
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'Ada-attempt')
        self.assertFalse(native_bee.get(self.conn, env.name, REL, 'P1')['running'])

    # Draw the Bee name and task and stop after its result is recorded.
    def test_native_view_draws_task_and_stops_after_result(self):
        """The Bee status pane ends when the Queen records the result."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        self.agent('Cleo', phase='queen', status='running', plan_path=REL)
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        token = native_bee.get(self.conn, env.name, REL, 'P1')['token']
        runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 progress', kind='note', agent='Cleo', mirror='none')
        out = StringIO()
        with mock.patch('haivequeen.native_bee.time.sleep', side_effect=lambda _: native_bee.finish(self.conn, env.name, REL, 'P1')):
            native_bee.run(self.conn, env.name, REL, 'P1', token, out=out)
        self.assertIn('Bee: ADA progress 1/2 [', out.getvalue())

    # Keep progress visible while the plan is done but the Bee still runs.
    def test_done_plan_keeps_native_bee_progress(self):
        """A retained Bee pane still displays its latest progress."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        self.agent('Cleo', phase='queen', status='running', plan_path=REL)
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'task', term, 'window')
        token = native_bee.get(self.conn, env.name, REL, 'P1')['token']
        runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 progress', kind='note', agent='Cleo', mirror='none')
        self.conn.execute("UPDATE plans SET status='DONE' WHERE project=? AND path=?", (env.name, REL))
        out = StringIO()
        with mock.patch('haivequeen.native_bee.time.sleep', side_effect=lambda _: native_bee.finish(self.conn, env.name, REL, 'P1')):
            native_bee.run(self.conn, env.name, REL, 'P1', token, out=out)
        self.assertIn('progress 1/2 [', out.getvalue())

    # A completed step count does not hide a native Bee before its result.
    def test_native_bee_keeps_final_progress_until_result(self):
        """A native Bee remains visible after its last step until it ends."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        self.agent('Cleo', phase='queen', status='running', plan_path=REL)
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        runtime.append_event(self.conn, env.name, 'Ada P1: step 2/2 finished', kind='note', agent='Cleo', mirror='none')
        hive = hives.hive_data(self.conn, env.name, runtime.list_agents(self.conn, env.name, 'active'), REL)[0]
        self.assertEqual(hive['bees'][0]['step'], '2/2')

    # An interrupted native Bee must not remain visible as running.
    def test_finished_native_bee_does_not_replay_old_progress(self):
        """Reporting an interrupted attempt removes its old Bee line."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        self.agent('Cleo', phase='queen', status='running', plan_path=REL)
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 progress', kind='note', agent='Cleo', mirror='none')
        bees.report_attempt(env, REL, 'P1', 'medium', 'interrupted', 'Ada-attempt')
        hive = hives.hive_data(self.conn, env.name, runtime.list_agents(self.conn, env.name, 'active'), REL)[0]
        self.assertEqual(hive['bees'], [])

    # A restarted native Bee must not inherit the previous attempt's notes.
    def test_new_native_bee_ignores_old_progress(self):
        """Only notes written after the new Bee starts are shown."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        self.agent('Cleo', phase='queen', status='running', plan_path=REL)
        runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 old', kind='note', agent='Cleo', mirror='none')
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'new task', term, 'window')
        hive = hives.hive_data(self.conn, env.name, runtime.list_agents(self.conn, env.name, 'active'), REL)[0]
        self.assertEqual(hive['bees'][0]['task'], 'new task')
        runtime.append_event(self.conn, env.name, 'Ada P1: step 1/2 new', kind='note', agent='Cleo', mirror='none')
        hive = hives.hive_data(self.conn, env.name, runtime.list_agents(self.conn, env.name, 'active'), REL)[0]
        self.assertEqual(hive['bees'][0]['task'], 'new')

    # Pass runtime overrides through the native pane launcher.
    def test_native_view_accepts_forwarded_runtime_environment(self):
        """The launcher applies its runtime override before opening the database."""
        with mock.patch.dict('os.environ', {}, clear=False), mock.patch.object(native_bee.db, 'connect') as connect, mock.patch.object(native_bee, 'run'):
            native_bee.main(['--project', 'testproj', '--plan', REL, '--package', 'P1', '--token', 'x', '--env', 'BZZ_DATA_DIR=somewhere'])
            self.assertEqual(__import__('os').environ['BZZ_DATA_DIR'], 'somewhere')
            connect.assert_called_once()

    # Keep a Hive and its Apiary when its native Bee pane is live.
    def test_rebuild_keeps_hive_with_native_bee(self):
        """A live native Bee protects its Hive and Apiary during a rebuild."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        head = PaneInfo('window.0', loop.apiary_pane_title(env.name, loop.is_test_runtime()), 2, True)
        term = mock.Mock()
        term.list_panes.return_value = [head, hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [head, hive, PaneInfo('window.2', 'Bee', 3, True)]
        loop.close_idle_hive_panes(self.conn, term, 'window')
        term.close_pane.assert_not_called()

    # A new row must wait while a native Bee occupies its predecessor.
    def test_new_hive_waits_for_native_bee_row(self):
        """A native Bee pane prevents an incomplete-width Hive row."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        other = 'docs/plans/dummy/OTHER-PLAN.md'
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hives.create(self.conn, env.name, other, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        head = PaneInfo('window.0', loop.apiary_pane_title(env.name, loop.is_test_runtime()), 2, True)
        term = mock.Mock()
        term.list_panes.return_value = [head, hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [head, hive, PaneInfo('window.2', 'Bee', 3, True)]
        self.assertTrue(loop.hive_row_waits_for_bees(env, term, 'window', other))
        with self.assertRaises(TerminalError):
            loop.ensure_hive_pane(env, term, 'window', other)

    # A native Bee prevents a repair from closing its Hive.
    def test_native_bee_defers_window_repair(self):
        """Window repair waits while a native Bee pane is live."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [hive, PaneInfo('window.2', 'Bee', 3, True)]
        runtime.kv_set(self.conn, '*', 'pane-repair-pending', '1')
        self.assertFalse(loop.repair_pane_window(env, term, 'window'))
        term.close_pane.assert_not_called()

    # A Queen restart must not close a Hive with a native Bee.
    def test_native_bee_keeps_hive_during_queen_restart(self):
        """A running native Bee keeps its Hive pane during a Queen restart."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [hive, PaneInfo('window.2', 'Bee', 3, True)]
        loop.rebuild_small_hive_pane(env, term, 'window', REL)
        term.close_pane.assert_not_called()

    # A new Apiary must wait while the split target has a native Bee.
    def test_new_apiary_waits_for_native_bee(self):
        """An Apiary is not split from only part of a native Bee row."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        other = self.env()
        other.name = 'other'
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        head = PaneInfo('window.0', loop.apiary_pane_title(env.name, loop.is_test_runtime()), 2, True)
        term = mock.Mock()
        term.list_panes.return_value = [head, hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [head, hive, PaneInfo('window.2', 'Bee', 3, True)]
        self.assertTrue(loop.apiary_waits_for_bees(other, term, 'window'))

    # A process Bee starts after an earlier native Bee in the same Hive.
    def test_process_bee_follows_native_bee(self):
        """The next Bee splits the last Bee pane of its Hive."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        term.list_panes.return_value = [hive, PaneInfo('window.2', 'Bee', 3, True)]
        term.split_pane.return_value = 'window.3'
        spec = {'name': 'Bea', 'person': 'Bea', 'plan': REL, 'model_long': 'model'}
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']):
            loop.start_bee_pane(env, term, 'window', spec)
        self.assertEqual(term.split_pane.call_args.args[6], 'window.2')

    # A native Bee leaves room for the Queen's head when it fits.
    def test_native_bee_keeps_queen_head_readable(self):
        """The first native Bee uses the measured space left by the Queen."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        self.agent('Cleo', phase='queen', status='running', plan_path=REL, pane_id='window.1', paneCols=80)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        lines = [[('', '')], [('│ ', ''), ('Q' * 56 + ' ' * 964, ''), (' │', '')], [('│ ', ''), ('Q' + ' ' * 1019, ''), (' │', '')]]
        with mock.patch('haivequeen.hivepane.frame_lines', return_value=lines):
            native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        self.assertEqual(term.split_pane.call_args.args[5], 0.25)

    # A running native Bee cannot be replaced by another attempt of its package.
    def test_native_bee_does_not_replace_running_attempt(self):
        """A duplicate start keeps the original native Bee pane and state."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        previous = native_bee.get(self.conn, env.name, REL, 'P1')
        term.list_panes.return_value = [hive, PaneInfo('window.2', 'Bee', 3, True)]
        with self.assertRaises(TerminalError):
            native_bee.start(env, REL, 'P1', 'Bea', 'second task', term, 'window')
        self.assertEqual(native_bee.get(self.conn, env.name, REL, 'P1'), previous)

    # Restore the status pane when it disappears while the native Bee runs.
    def test_running_native_bee_reopens_its_missing_pane(self):
        """A missing native Bee pane is recreated without restarting the Bee."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        token = native_bee.get(self.conn, env.name, REL, 'P1')['token']
        term.split_pane.return_value = 'window.3'
        native_bee.ensure(env, term, 'window')
        item = native_bee.get(self.conn, env.name, REL, 'P1')
        self.assertEqual(item['pane'], 'window.3')
        self.assertEqual(item['token'], token)

    # A reused pane ID must not be mistaken for the missing Bee.
    def test_recovery_ignores_reused_id_of_another_pane(self):
        """An unrelated pane with the old ID cannot suppress Bee recovery."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first', term, 'window')
        term.list_panes.return_value = [hive, PaneInfo('window.2', 'unrelated', 2, True)]
        term.split_pane.return_value = 'window.3'
        native_bee.ensure(env, term, 'window')
        self.assertEqual(native_bee.get(self.conn, env.name, REL, 'P1')['pane'], 'window.3')
        term.close_pane.assert_not_called()

    # Recover a pane created just before the Queen loses its record update.
    def test_recovery_adopts_pane_after_record_write_fails(self):
        """A started pane is adopted by token instead of duplicated."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        live, records = [hive], []
        term = mock.Mock()
        term.name = 'headless'
        term.list_panes.side_effect = lambda _: live[:]
        term.registry.records.side_effect = lambda: records[:]
        def split(window, title, colour, argv, direction, size, target, name):
            """Simulate a started pane with its registry record."""
            pane = 'window.%d' % (len(records) + 2)
            live.append(PaneInfo(pane, title, 2, True))
            records.append({'id': pane, 'window': window, 'name': name})
            return pane
        term.split_pane.side_effect = split
        original = runtime.kv_set
        writes = []
        def write(conn, project, key, value):
            """Fail the native Bee's second write after the pane starts."""
            if key.startswith('native-bee:'):
                writes.append(key)
                if len(writes) == 2:
                    raise OSError('record write failed')
            return original(conn, project, key, value)
        with mock.patch.object(runtime, 'kv_set', side_effect=write), self.assertRaises(OSError):
            native_bee.start(env, REL, 'P1', 'Ada', 'first', term, 'window')
        native_bee.ensure(env, term, 'window')
        self.assertEqual(len(records), 1)
        self.assertEqual(native_bee.get(self.conn, env.name, REL, 'P1')['pane'], 'window.2')

    # Recover an actually launched status process after the same write failure.
    def test_real_pane_is_adopted_after_record_write_fails(self):
        """The headless backend retains one live status pane after recovery."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        term = Headless(self.base / 'panes')
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term.open_window('window', title, [sys.executable, '-c', 'import time;time.sleep(30)'], name='hive')
        original, writes = runtime.kv_set, []
        def write(conn, project, key, value):
            """Fail after the status process has started."""
            if key.startswith('native-bee:'):
                writes.append(key)
                if len(writes) == 2:
                    raise OSError('record write failed')
            return original(conn, project, key, value)
        try:
            with mock.patch.object(runtime, 'kv_set', side_effect=write), self.assertRaises(OSError):
                native_bee.start(env, REL, 'P1', 'Ada', 'first', term, 'window')
            native_bee.ensure(env, term, 'window')
            pane = native_bee.get(self.conn, env.name, REL, 'P1')['pane']
            self.assertTrue(pane)
            self.assertEqual(len([p for p in term.list_panes('window') if p.alive]), 2)
            native_bee.finish(self.conn, env.name, REL, 'P1')
        finally:
            for item in term.list_panes('window'):
                if item.alive:
                    term.close_pane(item.id)

    # Restore an earlier Bee before a later Bee in the same Hive.
    def test_recovery_keeps_bees_in_start_order(self):
        """Reopening the first Bee splits the Queen, not the later Bee."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}, {'id': 'P2', 'title': 'Second'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first', term, 'window')
        ada = pane_title(env.name, 'Bee ADA', 'Ada', '-', loop.is_test_runtime())
        bea = pane_title(env.name, 'Bee BEA', 'Bea', '-', loop.is_test_runtime())
        first = PaneInfo('window.2', ada, 2, True)
        term.list_panes.return_value = [hive, first]
        term.split_pane.return_value = 'window.3'
        native_bee.start(env, REL, 'P2', 'Bea', 'second', term, 'window')
        term.list_panes.return_value = [hive, PaneInfo('window.3', bea, 3, True)]
        term.split_pane.return_value = 'window.4'
        native_bee.ensure(env, term, 'window')
        self.assertEqual(term.split_pane.call_args.args[6], hive.id)
        term.list_panes.return_value = [hive, PaneInfo('window.4', ada, 4, True), PaneInfo('window.3', bea, 3, True)]
        spec = {'name': 'Dina', 'person': 'Dina', 'plan': REL, 'model_long': 'model'}
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']):
            loop.start_bee_pane(env, term, 'window', spec)
        self.assertEqual(term.split_pane.call_args.args[6], 'window.3')
        term.split_pane.return_value = 'window.5'
        native_bee.start(env, REL, 'P3', 'Cora', 'third', term, 'window')
        self.assertEqual(term.split_pane.call_args.args[6], 'window.3')

    # One unavailable Hive must not prevent recovery of another Bee pane.
    def test_recovery_continues_after_one_hive_is_unavailable(self):
        """A failed pane recovery does not block later Bees."""
        env = self.env()
        items = [dict(plan='missing', package='P1', name='Ada', task='first', running=True, pane='old1'),
                 dict(plan=REL, package='P2', name='Bea', task='second', running=True, pane='old2')]
        term = mock.Mock()
        term.list_panes.return_value = []
        with mock.patch.object(native_bee, 'records', return_value=items), mock.patch.object(native_bee, 'start', side_effect=[TerminalError('no Hive pane'), 'new']) as start:
            native_bee.ensure(env, term, 'window')
        self.assertEqual(start.call_count, 2)

    # An operating-system launch failure must not block other Bee panes.
    def test_recovery_continues_after_launch_os_error(self):
        """A failed terminal process launch leaves other Bee recoveries possible."""
        env = self.env()
        items = [dict(plan='missing', package='P1', name='Ada', task='first', running=True, pane='old1'),
                 dict(plan=REL, package='P2', name='Bea', task='second', running=True, pane='old2')]
        term = mock.Mock()
        term.list_panes.return_value = []
        with mock.patch.object(native_bee, 'records', return_value=items), mock.patch.object(native_bee, 'start', side_effect=[OSError('launch failed'), 'new']) as start:
            native_bee.ensure(env, term, 'window')
        self.assertEqual(start.call_count, 2)

    # Concurrent recovery must keep the first replacement pane.
    def test_concurrent_recovery_does_not_duplicate_a_live_pane(self):
        """A stale recovery request keeps a pane already restored by another call."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        stale = native_bee.get(self.conn, env.name, REL, 'P1')
        term.split_pane.return_value = 'window.3'
        native_bee.ensure(env, term, 'window')
        title = pane_title(env.name, 'Bee ADA', 'Ada', '-', loop.is_test_runtime())
        term.list_panes.return_value = [hive, PaneInfo('window.3', title, 3, True)]
        term.split_pane.reset_mock()
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window', _resume=stale)
        term.split_pane.assert_not_called()
        self.assertEqual(native_bee.get(self.conn, env.name, REL, 'P1')['pane'], 'window.3')

    # A result arriving during recovery must not revive a finished Bee.
    def test_recovery_does_not_revive_finished_bee(self):
        """A stale recovery request cannot restart a completed status pane."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        hives.create(self.conn, env.name, REL, 'claude', 'white')
        hive = PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)
        term = mock.Mock()
        term.list_panes.return_value = [hive]
        term.split_pane.return_value = 'window.2'
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window')
        stale = native_bee.get(self.conn, env.name, REL, 'P1')
        native_bee.finish(self.conn, env.name, REL, 'P1')
        term.split_pane.reset_mock()
        native_bee.start(env, REL, 'P1', 'Ada', 'first task', term, 'window', _resume=stale)
        term.split_pane.assert_not_called()
        self.assertFalse(native_bee.get(self.conn, env.name, REL, 'P1')['running'])
