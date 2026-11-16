import json
from unittest import mock
from bzztest import REL, RoundCase
from haivequeen import force_stop, integration, orchestrator, runtime


class StopIdentityTests(RoundCase):
    def test_pipeline_cancellation_requires_recorded_matching_identity(self):
        for recorded, current, expected in [('old', 'new', False), (None, 'new', False), ('old', None, False), ('old', 'old', True)]:
            with self.subTest(recorded=recorded, current=current):
                state = {'steps': {'Build': 'running'}, 'job': {'pid': 910003, 'identity': recorded, 'token': 'test'}}
                stop = mock.Mock()
                with mock.patch.object(orchestrator.osplat, 'process_identity', return_value=current), mock.patch.object(orchestrator.osplat, 'process_alive', return_value=False):
                    orchestrator._cancel_pipeline_job(self.conn, 'testproj', REL, state, stop)
                self.assertEqual(stop.called, expected)
                self.assertNotIn('job', state)

    def test_fix_agent_cancellation_requires_recorded_matching_identity(self):
        for recorded, current, expected in [('old', 'new', False), (None, 'new', False), ('old', None, False), ('old', 'old', True)]:
            with self.subTest(recorded=recorded, current=current):
                self.agent('fixer', phase='fix', status='running', pid=910004)
                runtime.upsert_agent(self.conn, 'testproj', 'fixer', {'processIdentity': recorded})
                orchestrator.save(self.conn, 'testproj', REL, {'phase': 'fix', 'steps': {}, 'fix': {'agent': 'fixer'}})
                stop = mock.Mock()
                with mock.patch.object(orchestrator.osplat, 'process_identity', return_value=current):
                    orchestrator.stop_plan(self.conn, 'testproj', REL, stop)
                self.assertEqual(stop.called, expected)

    def test_force_snapshot_does_not_select_unverified_pipeline_pid(self):
        for recorded, current, expected in [('old', 'new', False), (None, 'new', False), ('old', None, False), ('old', 'old', True)]:
            with self.subTest(recorded=recorded, current=current):
                orchestrator.save(self.conn, 'testproj', REL, {'steps': {}, 'job': {'pid': 910003, 'identity': recorded}})
                with mock.patch.object(force_stop.osplat, 'process_identity', return_value=current):
                    victims = force_stop._snapshot(self.conn)[2]
                self.assertEqual(910003 in victims, expected)

    def test_reused_pipeline_pid_does_not_keep_recovery_waiting(self):
        state = {'steps': {}, 'job': {'pid': 910003, 'identity': 'old', 'token': 'test', 'file': str(self.base / 'absent.json')}}
        with mock.patch.object(orchestrator.osplat, 'process_identity', return_value='new'), mock.patch.object(orchestrator.osplat, 'process_alive', return_value=True):
            self.assertEqual(orchestrator._harvest(self.conn, self.env(), REL, state), 'lost')

    def test_force_stop_rechecks_identity_before_termination(self):
        kill = mock.Mock()
        with mock.patch.object(force_stop.osplat, 'process_identity', return_value='replacement'):
            force_stop._end_work({910003: 'original'}, kill, [])
        kill.assert_not_called()

    def test_unverified_live_pipeline_retains_state(self):
        state = {'steps': {'Build': 'running'}, 'job': {'pid': 910003, 'token': 'test'}}
        stop = mock.Mock()
        with mock.patch.object(orchestrator.osplat, 'process_identity', return_value=None), mock.patch.object(orchestrator.osplat, 'process_alive', return_value=True):
            self.assertFalse(orchestrator._cancel_pipeline_job(self.conn, 'testproj', REL, state, stop))
        stop.assert_not_called()
        self.assertIn('could not be verified', state['job']['cancel_note'])

    def test_global_stop_retains_unverified_live_registration(self):
        state = {'steps': {'Build': 'running'}, 'job': {'pid': 910003, 'token': 'test'}}
        orchestrator.save(self.conn, 'testproj', REL, state)
        stop = mock.Mock()
        with mock.patch.object(force_stop.loop, 'loop_running', return_value=0), mock.patch.object(force_stop.osplat, 'process_identity', return_value=None), mock.patch.object(force_stop.osplat, 'process_alive', return_value=True):
            result = force_stop.stop(self.conn, kill=stop)
        self.assertFalse(result['ok'])
        self.assertIn('Cannot verify process', result['error'])
        stop.assert_not_called()
        self.assertEqual(orchestrator.load(self.conn, 'testproj', REL), state)

    def test_process_start_captures_birth_identity(self):
        job = orchestrator.Job('test')
        with mock.patch.object(orchestrator.osplat, 'process_identity', return_value='birth'):
            job.record_pid(910003)
        self.assertEqual((job.pid, job.identity), (910003, 'birth'))

    def test_detached_check_records_supervisor_identity(self):
        proc = mock.Mock(pid=910003)
        env = self.env()
        with mock.patch.object(integration, 'head_commit', return_value='abc'), mock.patch.object(integration.subprocess, 'Popen', return_value=proc), mock.patch.object(integration.osplat, 'process_identity', return_value='birth'), mock.patch.object(integration.proctree, 'exempt_register'):
            job = integration.start_compile_check(env, {'name': 'test'}, cwd=self.repo, milestones=False)
        self.addCleanup(integration.SUPERVISORS.pop, job['token'], None)
        self.assertEqual((job['pid'], job['identity']), (910003, 'birth'))

    def test_reused_supervisor_pid_is_recovered_without_termination(self):
        request = {'key': 'compile-check:test', 'commit': 'abc', 'settings': {}, 'poll': 0.1}
        runtime.kv_set(self.conn, 'testproj', integration.COMPILE_REQUEST_KEY + 'test', json.dumps(request))
        with mock.patch.object(integration.osplat, 'process_identity', return_value='replacement'), mock.patch.object(integration.osplat, 'process_alive', return_value=True), mock.patch.object(integration.osplat, 'kill_process_tree') as kill:
            result = integration.poll_compile_check(self.conn, 'testproj', {'token': 'test', 'pid': 910003, 'identity': 'birth'}, clean_slot=False)
        self.assertTrue(result['aborted'])
        kill.assert_not_called()
