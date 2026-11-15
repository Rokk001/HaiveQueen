from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import hives, plan_view, orchestrator


class MissingPlanTests(RoundCase):
    def test_missing_file_blocks_starts_preserves_history_and_restores(self):
        self.plan('RUNNING', [{'id': 'P1', 'title': 'First'}])
        runtime.set_package(self.conn, 'testproj', REL, 'P1', phase='done')
        runtime.upsert_agent(self.conn, 'testproj', 'worker', {'status': 'running', 'plan': REL})
        path = self.repo / REL
        original = path.read_bytes()
        path.unlink()
        runtime.sync_plans(self.conn, 'testproj', str(self.repo))
        self.assertEqual(hives.start_block_reason(self.conn, 'testproj', REL), 'plan file missing')
        row = runtime.list_plans(self.conn, 'testproj')[0]
        self.assertTrue(row['missing'])
        self.assertEqual(row['status'], 'RUNNING')
        self.assertEqual(runtime.list_agents(self.conn, 'testproj')[0]['status'], 'running')
        self.assertEqual(runtime.list_packages(self.conn, 'testproj', REL)[0]['phase'], 'done')
        path.write_bytes(original)
        runtime.sync_plans(self.conn, 'testproj', str(self.repo))
        self.assertFalse(runtime.list_plans(self.conn, 'testproj')[0]['missing'])
        self.assertEqual(hives.start_block_reason(self.conn, 'testproj', REL), '')

    def test_missing_plan_has_no_start_action(self):
        self.assertEqual(plan_view.actions({'status': 'DRAFT', 'missing': True}), [])

    def test_missing_plan_details_still_show_recorded_package_result(self):
        from haivequeen import bees, web_api
        self.plan('RUNNING', [{'id': 'P1', 'title': 'First'}])
        bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'done', 'worker')
        (self.repo / REL).unlink()
        detail = web_api.execute(self.conn, 'plan/detail', {'project': 'testproj', 'plan': REL})
        self.assertTrue(detail['plan']['missing'])
        self.assertEqual(detail['packages'][0]['status'], 'done')
        self.assertEqual(detail['text'], '')

    def test_missing_plan_only_harvests_existing_job(self):
        self.plan('RUNNING', [{'id': 'P1', 'title': 'First'}])
        (self.repo / REL).unlink()
        state = {'phase': 'pipeline'}
        env = mock.Mock(name='environment')
        env.name = 'testproj'
        with mock.patch.object(hives, 'get', return_value={'state': 'integrating'}), \
                mock.patch.object(orchestrator, 'load', return_value=state), \
                mock.patch.object(orchestrator, '_harvest_only') as harvest, \
                mock.patch.object(orchestrator, '_advance_pipeline') as advance:
            orchestrator.advance(self.conn, env, REL, 10000)
        harvest.assert_called_once()
        advance.assert_not_called()
