from bzztest import RoundCase, REL, write_plan
from haivequeen import hives, runtime, web_api


class ScopedControls(RoundCase):
    def test_global_resume_ignores_legacy_paused_plans(self):
        self.plan('PAUSED')
        self.assertEqual(web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'}), [])

    def test_stop_resume_restores_only_its_own_plans(self):
        self.plan('RUNNING')
        old = 'docs/plans/old/OLD-PLAN.md'
        write_plan(self.repo / old, 'PAUSED')
        runtime.sync_plans(self.conn, 'testproj')
        data = {'operation': 'stop-gracefully'}
        targets = web_api.execute(self.conn, 'plans/control-preview', data)
        web_api.execute(self.conn, 'plans/control', {**data, 'targets': targets, 'confirmed': True})
        self.assertEqual(hives._plan_status(self.conn, 'testproj', REL), 'PAUSED')
        targets = web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'})
        self.assertEqual([r['plan'] for r in targets], [REL])
        web_api.execute(self.conn, 'plans/control', {'operation': 'resume', 'targets': targets, 'confirmed': True})
        self.assertEqual(hives._plan_status(self.conn, 'testproj', REL), 'RUNNING')
        self.assertEqual(hives._plan_status(self.conn, 'testproj', old), 'PAUSED')
        self.assertEqual(web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'}), [])

    def test_unknown_previous_state_does_not_start(self):
        self.plan('PAUSED')
        with self.assertRaises(ValueError):
            hives.start_again(self.conn, 'testproj', REL)
        self.assertEqual(hives._plan_status(self.conn, 'testproj', REL), 'PAUSED')


    def test_repeated_stop_and_force_preserve_scope(self):
        from unittest import mock
        self.plan('RUNNING')
        for operation in ('stop-gracefully', 'stop-gracefully', 'force-stop'):
            targets = web_api.execute(self.conn, 'plans/control-preview', {'operation': operation})
            with mock.patch('haivequeen.hives.stop_hive', wraps=hives.pause_plan):
                web_api.execute(self.conn, 'plans/control', {'operation': operation, 'targets': targets, 'confirmed': True})
        self.assertEqual(len(web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'})), 1)

    def test_changed_status_rejects_confirmed_selection(self):
        self.plan('RUNNING')
        targets = web_api.execute(self.conn, 'plans/control-preview', {'operation': 'stop-gracefully'})
        runtime.set_plan_status(self.conn, 'testproj', REL, 'DONE')
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            web_api.execute(self.conn, 'plans/control', {'operation': 'stop-gracefully', 'targets': targets, 'confirmed': True})
        self.assertEqual(hives._plan_status(self.conn, 'testproj', REL), 'DONE')

    def test_individual_resume_then_pause_drops_global_membership(self):
        self.plan('RUNNING')
        targets = web_api.execute(self.conn, 'plans/control-preview', {'operation': 'stop-gracefully'})
        web_api.execute(self.conn, 'plans/control', {'operation': 'stop-gracefully', 'targets': targets, 'confirmed': True})
        hives.start_again(self.conn, 'testproj', REL)
        hives.pause_plan(self.conn, 'testproj', REL)
        self.assertEqual(web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'}), [])

    def test_global_force_stop_tracks_only_newly_stopped_plans(self):
        from unittest import mock
        self.plan('RUNNING')
        old = 'docs/plans/old/OLD-PLAN.md'
        write_plan(self.repo / old, 'PAUSED')
        runtime.sync_plans(self.conn, 'testproj')
        targets = web_api.execute(self.conn, 'plans/control-preview', {'operation': 'force-stop'})
        with mock.patch('haivequeen.hives.stop_hive', wraps=hives.pause_plan) as stop:
            web_api.execute(self.conn, 'plans/control', {'operation': 'force-stop', 'targets': targets, 'confirmed': True})
        stop.assert_called_once_with(self.conn, 'testproj', REL)
        self.assertEqual([r['plan'] for r in web_api.execute(self.conn, 'plans/control-preview', {'operation': 'resume'})], [REL])
