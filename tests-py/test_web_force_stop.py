import json
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import cli, force_stop, integration, interrupt, loop, orchestrator, runtime


class ForceStopTests(RoundCase):
    def test_start_after_force_stop_retries_queen_without_owner_question(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        first = next(a for a in runtime.list_agents(self.conn, 'testproj', 'active') if a['phase'] == 'queen' and a['status'] == 'running')
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
        orchestrator.save(self.conn, 'testproj', REL, {'steps': {'Merge': 'done', 'Build': 'running'}, 'job': {'pid': 910003}, 'phase': 'pipeline', 'placed': True})
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
