"""A stopped integration keeps its place until cancellation is confirmed."""
from unittest import mock
from bzztest import REL, RoundCase
from haivequeen import dashboard_data, hives, orchestrator, runtime


class StopReservationTests(RoundCase):
    def prepare(self):
        self.plan(status='RUNNING')
        hives.create(self.conn, 'testproj', REL, 'codex', 'white')
        self.conn.execute("UPDATE hives SET state='integrating' WHERE project='testproj'")
        self.assertTrue(hives.take_integration(self.conn, 'testproj', REL))
        self.assertFalse(hives.take_integration(self.conn, 'testproj', 'next-plan'))
        state = {'phase': 'pipeline', 'step': 'Build', 'steps': {'Build': 'running'},
                 'placed': True, 'job': {'token': 'stop-test', 'pid': None}}
        orchestrator.save(self.conn, 'testproj', REL, state)
        return state

    def assert_reserved(self):
        self.assertTrue(orchestrator.load(self.conn, 'testproj', REL)['placed'])
        self.assertFalse(hives.take_integration(self.conn, 'testproj', 'next-plan'))

    def test_stop_hive_retains_place_and_round_releases_after_thread_exit(self):
        self.prepare()
        job = orchestrator.Job('stop-test')
        job.thread = mock.Mock()
        job.thread.is_alive.return_value = True
        orchestrator.JOBS['testproj', REL] = job
        self.addCleanup(orchestrator.JOBS.pop, ('testproj', REL), None)
        hives.stop_hive(self.conn, 'testproj', REL, stop=mock.Mock())
        self.assert_reserved()
        info = dashboard_data.orch_info(self.conn, self.env(), hives.get(self.conn, 'testproj', REL))
        self.assertEqual(info['status'], 'stopping')
        orchestrator.advance(self.conn, self.env(), REL, 16384)
        self.assert_reserved()
        job.thread.is_alive.return_value = False
        orchestrator.advance(self.conn, self.env(), REL, 16384)
        self.assertFalse(orchestrator.load(self.conn, 'testproj', REL)['placed'])
        self.assertTrue(hives.take_integration(self.conn, 'testproj', 'next-plan'))
        self.assertNotIn('job', orchestrator.load(self.conn, 'testproj', REL))

    def test_recovered_supervisor_keeps_place_until_exit_and_result(self):
        state = self.prepare()
        state['job'].update(compile=True, pid=900001, identity='birth')
        orchestrator.save(self.conn, 'testproj', REL, state)
        with mock.patch.object(orchestrator.agent_process, 'identity_matches', return_value=False), \
                mock.patch.object(orchestrator.agent_process, 'recorded_alive', return_value=True) as alive, \
                mock.patch.object(orchestrator.integration, 'poll_compile_check', return_value=None) as poll:
            hives.stop_hive(self.conn, 'testproj', REL, stop=mock.Mock())
            self.assert_reserved()
            alive.return_value = False
            orchestrator.advance(self.conn, self.env(), REL, 16384)
            self.assert_reserved()
            poll.return_value = {'code': 143}
            orchestrator.advance(self.conn, self.env(), REL, 16384)
        self.assertTrue(hives.take_integration(self.conn, 'testproj', 'next-plan'))

    def test_already_ended_job_releases_immediately(self):
        self.prepare()
        hives.stop_hive(self.conn, 'testproj', REL, stop=mock.Mock())
        self.assertTrue(hives.take_integration(self.conn, 'testproj', 'next-plan'))

    def test_fix_agent_keeps_place_until_confirmed_dead(self):
        state = self.prepare()
        state.pop('job')
        state.update(phase='fix', fix={'agent': 'fixer'})
        self.agent('fixer', phase='fix', status='running', pid=900002)
        runtime.upsert_agent(self.conn, 'testproj', 'fixer', {'processIdentity': 'birth'})
        orchestrator.save(self.conn, 'testproj', REL, state)
        with mock.patch.object(orchestrator.agent_process, 'identity_matches', return_value=True), \
                mock.patch.object(orchestrator.agent_process, 'recorded_alive', return_value=True) as alive:
            orchestrator.stop_plan(self.conn, 'testproj', REL, stop=mock.Mock())
            self.assert_reserved()
            alive.return_value = False
            orchestrator.stop_plan(self.conn, 'testproj', REL, stop=mock.Mock())
        self.assertTrue(hives.take_integration(self.conn, 'testproj', 'next-plan'))
