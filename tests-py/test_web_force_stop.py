import json
import threading
from types import SimpleNamespace
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import cli, force_stop, integration, interrupt, loop, orchestrator, runtime


class ForceStopTests(RoundCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(force_stop.osplat, 'process_identity', return_value='fixture-process')
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, *args, **kwargs):
        result = super().agent(*args, **kwargs)
        runtime.upsert_agent(self.conn, kwargs.get('project', 'testproj'), args[0], {'processIdentity': 'fixture-process'})
        return result

    def test_held_scheduling_lock_does_not_delay_process_termination(self):
        self.plan('RUNNING')
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        orchestrator.save(self.conn, 'testproj', REL, {
            'steps': {'Merge': 'done', 'Build': 'running'}, 'job': {'pid': 910003, 'identity': 'fixture-process'}})
        held = threading.Event()
        release = threading.Event()
        def hold_lock():
            with loop.hive_schedule_lock():
                held.set()
                release.wait(5)
        holder = threading.Thread(target=hold_lock)
        holder.start()
        events = []
        acquire = loop.hive_schedule_lock
        attempts = []
        def cleanup_lock(*args, **kwargs):
            self.assertTrue(force_stop.requested())
            if not attempts:
                self.assertEqual(events[:1], [('loop', 910004)])
                self.assertEqual(set(events[1:]), {('work', 910002), ('work', 910003)})
                self.agent('late-worker', phase='bee', status='running', pid=910005, started=1)
                orchestrator.save(self.conn, 'testproj', REL + '-late', {
                    'steps': {'Deploy': 'running'}, 'job': {'pid': 910006, 'identity': 'fixture-process'}})
                self.agent('reused-worker', phase='bee', status='running', pid=910007, started=1)
                runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'late', json.dumps({
                    'supervisor_pid': 910008, 'supervisor_identity': 'supervisor',
                    'check_pid': 910009, 'check_identity': 'old-check',
                    'key': 'compile-check:late', 'commit': 'abc1234', 'settings': {}}))
            attempts.append(True)
            return acquire(*args, **kwargs)
        def kill(pid):
            events.append(('work', pid))
            if {('work', 910005), ('work', 910006), ('work', 910008)} <= set(events):
                release.set()
            return True
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(loop, 'loop_running', return_value=910004), mock.patch.object(force_stop.agent_process, 'pid_reused', side_effect=lambda row: row.get('pid') == 910007), mock.patch.object(force_stop.osplat, 'process_identity', side_effect=lambda pid: {910007: 'reused', 910008: 'supervisor', 910009: 'new-check'}.get(pid, 'fixture-process')), mock.patch.object(loop, 'hive_schedule_lock', side_effect=cleanup_lock):
                result = force_stop.stop(self.conn, kill=kill,
                                         end_loop=lambda pid: events.append(('loop', pid)) or True)
            self.assertTrue(result['ok'], result)
            self.assertTrue(release.is_set(), 'Late work must stop before the lock is released')
            self.assertGreater(len(attempts), 1)
            self.assertEqual(len(events), 6)
        finally:
            release.set()
            holder.join(5)
        self.assertEqual(orchestrator.load(self.conn, 'testproj', REL)['steps'], {'Merge': 'done'})
        self.assertNotIn('job', orchestrator.load(self.conn, 'testproj', REL + '-late'))
        self.assertTrue(json.loads(runtime.kv_get(self.conn, 'testproj', integration.COMPILE_RUN_KEY + 'late'))['aborted'])
        self.assertTrue(all(interrupt.is_interrupted(runtime.get_agent(self.conn, 'testproj', name))
                            for name in ('worker', 'late-worker')))
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'reused-worker')['status'], 'running')

    def test_start_recovers_interrupted_force_stop_before_resuming(self):
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        orchestrator.save(self.conn, 'testproj', REL, {
            'steps': {'Merge': 'done', 'Build': 'running'}, 'job': {'pid': 910003, 'identity': 'fixture-process'}})
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text('force stop requested before crash\n', encoding='utf-8')
        args = cli.build_parser().parse_args(['start', '--foreground'])
        killed = []
        def run(*args):
            self.assertFalse(loop.stop_flag().exists())
            self.assertEqual(set(killed), {910002, 910003})
            self.assertTrue(interrupt.is_interrupted(runtime.get_agent(self.conn, 'testproj', 'worker')))
            self.assertEqual(orchestrator.load(self.conn, 'testproj', REL)['steps'], {'Merge': 'done'})
            return 0
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(force_stop.agent_process, 'pid_reused', return_value=False), mock.patch.object(force_stop.osplat, 'kill_process_tree', side_effect=lambda pid: killed.append(pid) or True), mock.patch.object(loop, 'run_loop', side_effect=run):
            self.assertEqual(loop.cmd_start(args, self.conn), 0)

    def test_start_keeps_stop_marker_when_recovery_fails(self):
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text('force stop requested before crash\n', encoding='utf-8')
        args = cli.build_parser().parse_args(['start', '--foreground'])
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(force_stop, 'stop', return_value={'ok': False, 'error': 'termination failed'}), mock.patch.object(loop, 'run_loop') as run:
            self.assertNotEqual(loop.cmd_start(args, self.conn), 0)
        run.assert_not_called()
        self.assertTrue(force_stop.requested())

    def test_work_registered_before_cleanup_is_stopped_once(self):
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        killed = []
        # Keep database writes on this thread rather than in the kill worker.
        acquire = loop.hive_schedule_lock
        def cleanup_lock(*args, **kwargs):
            orchestrator.save(self.conn, 'testproj', REL, {
                'steps': {'Deploy': 'running'}, 'job': {'pid': 910003, 'identity': 'fixture-process'}})
            return acquire(*args, **kwargs)
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(force_stop.agent_process, 'pid_reused', return_value=False), mock.patch.object(loop, 'hive_schedule_lock', side_effect=cleanup_lock):
            self.assertTrue(force_stop.stop(self.conn, kill=lambda pid: killed.append(pid) or True)['ok'])
        self.assertEqual(killed, [910002, 910003])
        self.assertNotIn('job', orchestrator.load(self.conn, 'testproj', REL))

    def test_reused_work_pids_are_refused_in_both_snapshots(self):
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        request = {'supervisor_pid': 910005, 'supervisor_identity': 'old-supervisor',
                   'check_pid': 910006, 'check_identity': 'old-check',
                   'key': 'compile-check:queen', 'commit': 'abc1234', 'settings': {}}
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'run', json.dumps(request))
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(force_stop.agent_process, 'pid_reused', return_value=True), mock.patch.object(force_stop.osplat, 'process_identity', return_value='new-process'), mock.patch.object(force_stop.osplat, 'kill_process_tree') as kill:
            self.assertTrue(force_stop.stop(self.conn)['ok'])
        kill.assert_not_called()

    def test_tray_detects_work_left_after_the_loop_stops(self):
        from haivequeen import tray
        self.assertFalse(tray.work_running())
        self.agent('worker', status='running', pid=910005)
        with mock.patch.object(force_stop.agent_process, 'pid_reused', return_value=False):
            self.assertTrue(tray.work_running())
        runtime.upsert_agent(self.conn, 'testproj', 'worker', {'status': 'done', 'pid': None})
        self.assertFalse(tray.work_running())
        orchestrator.save(self.conn, 'testproj', REL, {'job': {'pid': 910006, 'identity': 'fixture-process'}})
        self.assertTrue(tray.work_running())
        orchestrator.save(self.conn, 'testproj', REL, {})
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'tray-check', json.dumps({'check_pid': 910007}))
        self.assertTrue(tray.work_running())
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_RUN_KEY + 'tray-check', json.dumps({'ok': True}))
        self.assertFalse(tray.work_running())

    def test_foreground_console_entry_point_can_be_force_stopped(self):
        commands = (
            ('/opt/tools/bin/python /opt/tools/bin/bzz start --foreground', False),
            ('/opt/tools/bin/bzz start --foreground --rounds 2', False),
            ('"C:\\Program Files\\Python\\python.exe" "C:\\Program Files\\Python\\Scripts\\bzz.exe" start --foreground', True),
            ('"C:\\Program Files\\Python\\Scripts\\bzz.exe" start --foreground', True),
            ('python3.14 -I -m haivequeen start --foreground', False),
            ('python -I /opt/tools/haivequeen/cli.py start --foreground', False),
            ('"C:\\Program Files\\Python\\pythonw.exe" -I "C:\\Program Files\\App\\haivequeen\\cli.py" start --foreground', True),
            ('python -m haivequeen.cli start --foreground', True),
        )
        for command, windows in commands:
            with (
                self.subTest(command=command),
                mock.patch.object(loop, 'loop_running', return_value=910004),
                mock.patch.object(force_stop.osplat, 'process_command_line', return_value=command),
                mock.patch.object(force_stop.osplat, 'is_windows', return_value=windows),
                mock.patch.object(force_stop.osplat, 'process_alive', return_value=False),
                mock.patch.object(force_stop.signal, 'SIGKILL', 9, create=True),
                mock.patch.object(force_stop.osplat, '_win_terminate') as win_end,
                mock.patch.object(force_stop.os, 'kill') as posix_end,
                mock.patch.object(force_stop.osplat, 'kill_process_tree') as tree,
            ):
                self.assertTrue(force_stop.stop(self.conn)['ok'])
                if windows:
                    win_end.assert_called_once_with(910004)
                    posix_end.assert_not_called()
                else:
                    posix_end.assert_called_once_with(910004, force_stop.signal.SIGKILL)
                    win_end.assert_not_called()
                tree.assert_not_called()

    def test_unrelated_commands_cannot_pass_as_foreground_orchestrator(self):
        commands = (
            'python unrelated.py haivequeen start --foreground',
            'python unrelated.py bzz start --foreground',
            'python -m haivequeen restart --foreground',
            'python -m haivequeen start --foreground-other',
            'python -m haivequeen web start --foreground',
            'bzz-helper start --foreground',
            'echo "bzz start --foreground"',
            'python -c "print(1)" bzz start --foreground',
            'python /opt/haivequeen/unrelated.py start --foreground',
            'bzz start -- --foreground',
            'bzz start "--foreground',
            '',
        )
        for command in commands:
            with self.subTest(command=command), mock.patch.object(force_stop.osplat, 'process_command_line', return_value=command), mock.patch.object(force_stop.osplat, '_win_terminate') as win_end, mock.patch.object(force_stop.os, 'kill') as posix_end:
                with self.assertRaisesRegex(ValueError, 'verify'):
                    force_stop.terminate_loop(910004)
                win_end.assert_not_called()
                posix_end.assert_not_called()

    def test_new_build_and_deploy_are_visible_to_force_stop_before_next_round(self):
        for step in ('Build', 'Deploy'):
            with self.subTest(step=step):
                finished = threading.Event()
                def watch(*args, on_start, **kwargs):
                    on_start(910007)
                    finished.wait(5)
                    return 0, ''
                env = SimpleNamespace(name='testproj', conn=self.conn,
                                      settings={'buildCommand': 'dummy', 'deployCommand': 'dummy'})
                state = {'steps': {}}
                try:
                    with mock.patch.object(orchestrator, 'ASYNC', True), mock.patch.object(orchestrator, '_log'), mock.patch.object(orchestrator, '_harvest', return_value=None), mock.patch.object(integration, 'head_commit', return_value='abc1234'), mock.patch.object(integration, '_watch_compile', side_effect=watch):
                        orchestrator._command(self.conn, env, REL, state, self.wt, step, 8192)
                        stored = orchestrator.load(self.conn, 'testproj', REL)
                        self.assertEqual(stored['job']['pid'], 910007)
                        self.assertEqual(stored['job']['identity'], 'fixture-process')
                        killed = []
                        with mock.patch.object(loop, 'loop_running', return_value=0):
                            self.assertTrue(force_stop.stop(self.conn, kill=lambda pid: killed.append(pid) or True)['ok'])
                        self.assertEqual(killed, [910007])
                finally:
                    finished.set()
                    job = orchestrator.JOBS.pop(('testproj', REL), None)
                    if job and job.thread:
                        job.thread.join(5)

    def test_start_after_force_stop_retries_queen_without_owner_question(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        first = next(a for a in runtime.list_agents(self.conn, 'testproj', 'active') if a['phase'] == 'queen' and a['status'] == 'running')
        runtime.upsert_agent(self.conn, 'testproj', first['name'], {'processIdentity': 'fixture-process'})
        with mock.patch.object(loop, 'loop_running', return_value=0):
            self.assertTrue(force_stop.stop(self.conn, kill=lambda pid: True)['ok'])
        self.round()
        self.assertFalse(any(a['status'] == 'running' for a in runtime.list_agents(self.conn, 'testproj', 'active')))
        args = cli.build_parser().parse_args(['start', '--foreground'])
        with mock.patch.object(loop, 'run_loop', return_value=0):
            self.assertEqual(loop.cmd_start(args, self.conn), 0)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, 'testproj', 'active') if a['phase'] == 'queen' and a['status'] == 'running']
        self.assertEqual(len(active), 1)
        self.assertNotEqual(active[0]['name'], first['name'])
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open', plan=REL), [])

    def test_force_stops_agents_build_and_loop_keeps_plan_for_restart(self):
        self.plan('RUNNING')
        self.agent('queen', phase='queen', status='running', pid=910001, started=1)
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        before = self.conn.execute('SELECT status FROM plans').fetchone()[0]
        orchestrator.save(self.conn, 'testproj', REL, {'steps': {'Merge': 'done', 'Build': 'running'}, 'job': {'pid': 910003, 'identity': 'fixture-process'}, 'phase': 'pipeline', 'placed': True})
        killed = []
        ended = []
        with mock.patch.object(loop, 'loop_running', return_value=910004), mock.patch('haivequeen.agent_process.pid_reused', return_value=False):
            result = force_stop.stop(self.conn, kill=lambda pid: killed.append(pid) or True, end_loop=lambda pid: ended.append(pid))
        self.assertTrue(result['ok'])
        self.assertEqual(set(killed), {910001, 910002, 910003})
        self.assertEqual(ended, [910004])
        self.assertTrue(loop.stop_flag().exists())
        self.assertEqual(self.conn.execute('SELECT status FROM plans').fetchone()[0], before)
        self.assertEqual(orchestrator.load(self.conn, 'testproj', REL)['steps'], {'Merge': 'done'})
        self.assertNotIn('job', orchestrator.load(self.conn, 'testproj', REL))
        self.assertTrue(all(a['status'] == 'done' for a in runtime.list_agents(self.conn, 'testproj')))
        self.assertTrue(all(interrupt.is_interrupted(a) for a in runtime.list_agents(self.conn, 'testproj')))

    def test_detached_build_and_waiting_supervisor_are_stopped(self):
        request = {'supervisor_pid': 910005, 'supervisor_identity': 'supervisor', 'check_pid': 910006, 'check_identity': 'check',
                   'commit': 'abc1234', 'key': 'compile-check:queen', 'settings': {'compileCheck': 'dummy'}}
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'run', json.dumps(request))
        killed = []
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch.object(force_stop.osplat, 'process_identity', side_effect=lambda pid: {910005: 'supervisor', 910006: 'check'}[pid]):
            self.assertTrue(force_stop.stop(self.conn, kill=lambda pid: killed.append(pid) or True)['ok'])
        self.assertEqual(set(killed), {910005, 910006})
        self.assertTrue(json.loads(runtime.kv_get(self.conn, 'testproj', integration.COMPILE_RUN_KEY + 'run'))['aborted'])

    def test_interrupted_fix_attempt_is_repeated_without_escalation(self):
        orchestrator.save(self.conn, 'testproj', REL, {'steps': {}, 'phase': 'fix', 'fix': {'agent': 'fix-worker'}})
        runtime.kv_set(self.conn, 'testproj', orchestrator.ATTEMPTS_KEY + REL, json.dumps({'n': 2, 'cls': 'medium'}))
        with mock.patch.object(loop, 'loop_running', return_value=0):
            self.assertTrue(force_stop.stop(self.conn, kill=lambda pid: True)['ok'])
        self.assertEqual(json.loads(runtime.kv_get(self.conn, 'testproj', orchestrator.ATTEMPTS_KEY + REL))['n'], 1)
        self.assertIsNone(orchestrator.load(self.conn, 'testproj', REL)['fix']['agent'])

    def test_supervisor_started_during_force_stop_cannot_launch_a_build(self):
        request = {'commit': 'abc1234', 'key': 'compile-check:queen', 'settings': {}, 'name': 'queen', 'cwd': str(self.wt), 'poll': 0.01, 'milestones': ''}
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'late', json.dumps(request))
        loop.stop_flag().write_text('force stop requested now\n', encoding='utf-8')
        with mock.patch.object(integration, '_acquire_compile_slot') as acquire, mock.patch.object(integration, '_execute_compile_check') as execute:
            integration.compile_supervisor(force_stop.config_database(self.conn), 'testproj', 'late')
        acquire.assert_not_called()
        execute.assert_not_called()
        self.assertTrue(json.loads(runtime.kv_get(self.conn, 'testproj', integration.COMPILE_RUN_KEY + 'late'))['aborted'])

    def test_cli_force_flag_is_confirmation_and_prints_warning_first(self):
        args = cli.build_parser().parse_args(['stop', '--force'])
        with mock.patch.object(force_stop, 'stop', return_value={'ok': True}) as stop, mock.patch('builtins.print') as output:
            self.assertEqual(loop.cmd_stop(args, self.conn), 0)
        self.assertEqual(output.call_args_list[0].args, (force_stop.WARNING,))
        stop.assert_called_once_with(self.conn)

    def test_failed_kill_reports_failure_and_retains_unfinished_state(self):
        self.agent('worker', phase='bee', status='running', pid=910002, started=1)
        with mock.patch.object(loop, 'loop_running', return_value=0), mock.patch('haivequeen.agent_process.pid_reused', return_value=False):
            result = force_stop.stop(self.conn, kill=lambda pid: False)
        self.assertFalse(result['ok'])
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'worker')['status'], 'running')

    def test_single_loop_termination_does_not_end_its_web_child(self):
        with mock.patch.object(force_stop.osplat, 'process_command_line', return_value='python -m haivequeen start --foreground'), mock.patch.object(force_stop.osplat, 'is_windows', return_value=True), mock.patch.object(force_stop.osplat, '_win_terminate') as end, mock.patch.object(force_stop.osplat, 'kill_process_tree') as tree:
            force_stop.terminate_loop(910004)
        end.assert_called_once_with(910004)
        tree.assert_not_called()

    def test_recycled_loop_pid_is_refused(self):
        with mock.patch.object(force_stop.osplat, 'process_command_line', return_value='unrelated program'), mock.patch.object(force_stop.osplat, '_win_terminate') as end:
            with self.assertRaisesRegex(ValueError, 'verify'):
                force_stop.terminate_loop(910004)
        end.assert_not_called()

    def test_failed_loop_termination_is_reported(self):
        with mock.patch.object(loop, 'loop_running', return_value=910004):
            result = force_stop.stop(self.conn, kill=lambda pid: True, end_loop=lambda pid: False)
        self.assertFalse(result['ok'])
        self.assertIn('Could not end the Orchestrator', result['error'])
