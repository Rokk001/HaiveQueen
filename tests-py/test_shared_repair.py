"""A repeated diagnostic coordinates repair without serializing all plans."""
import sys
from unittest import mock

from bzztest import REL
from haivequeen import hives, orchestrator
from test_o1_orchestrator import OTHER, OrchCase


class SharedRepair(OrchCase):
    def setUp(self):
        self.check = self.command('first-fix.txt', 'shared fixture assertion failed: required table missing')
        self.build = self.command('second-fix.txt', 'second fixture assertion failed: required index missing')
        super().setUp()
        self.second = self.make_worktree('second-br', 'secondwt')
        self.ready_plan()
        self.ready_plan(OTHER, 'second-br', self.second, 'other.txt')

    @staticmethod
    def command(path, diagnostic):
        return ('"%s" -c "import pathlib,sys; ok=pathlib.Path(\'%s\').exists(); '
                'print(\'Regression fixture: validate shared database schema\\n%s\' if not ok else \'passed\'); '
                'sys.exit(0 if ok else 1)"') % (sys.executable, path, diagnostic)

    def test_shared_failure_waits_for_verified_pipeline_including_second_failure(self):
        self.round()
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        first = self.fixers()[0]
        self.commit(self.pwt, 'first-fix.txt')
        self.end_fixer(first)
        self.round()
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        self.assertEqual(len(self.fixers()), 2)
        prompt = (self.env().run_dir(self.fixers()[-1]['name']) / 'prompt.md').read_text(encoding='utf-8')
        # The prompt references existing evidence instead of embedding all plans.
        groups = self.state()['shared_causes']
        self.assertTrue(groups)
        import json
        from haivequeen import runtime
        self.assertTrue(any(OTHER in json.loads(runtime.kv_get(self.conn, 'testproj', key))['members']
                            for key in groups))
        for key in groups:
            self.assertIn(key, prompt)
        self.assertIn('bzz kv get', prompt)
        self.commit(self.pwt, 'second-fix.txt')
        self.end_fixer(self.fixers()[-1])
        for _ in range(4):
            self.round()
        self.assertEqual(self.plan_status(), 'REVIEW')
        self.assertEqual(self.plan_status(OTHER), 'REVIEW')
        self.assertEqual(len(self.fixers()), 2)
        events = orchestrator.repair_history(self.conn, 'testproj', OTHER)
        self.assertTrue(any(x['event'] == 'shared-repair-verified' for x in events))
        self.assertTrue((self.second / 'first-fix.txt').exists())
        self.assertTrue((self.second / 'second-fix.txt').exists())

    def test_distinct_diagnostics_do_not_share_a_repair(self):
        original = orchestrator._red
        def different(conn, env, plan, state, step, error):
            if plan == OTHER:
                error = 'Independent assertion failed: incompatible local feature, not a shared schema error'
                state.pop('log_file', None)
            return original(conn, env, plan, state, step, error)
        with mock.patch.object(orchestrator, '_red', side_effect=different):
            self.round()
        self.assertEqual(len(self.fixers()), 2)
        self.assertEqual(self.state(OTHER)['phase'], 'fix')

    def test_generic_exit_does_not_establish_a_common_cause(self):
        original = orchestrator._red
        def generic(conn, env, plan, state, step, error):
            return original(conn, env, plan, state, step, 'checks failed (exit 1), see output file')
        with mock.patch.object(orchestrator, '_red', side_effect=generic):
            self.round()
        self.assertEqual(len(self.fixers()), 2)

    def test_cancelled_leader_transfers_diagnosis_not_success(self):
        self.round()
        self.assertEqual(len(self.fixers()), 1)
        self.conn.execute("UPDATE plans SET status='CANCELLED' WHERE project=? AND path=?", ('testproj', REL))
        state = self.state(OTHER)
        orchestrator._advance_shared_repair(self.conn, self.env(), OTHER, state)
        self.assertEqual(self.state(OTHER)['phase'], 'fix')
        self.assertTrue(self.state(OTHER)['shared_causes'])
        self.assertFalse(any(x['event'] == 'shared-repair-verified' for x in
                             orchestrator.repair_history(self.conn, 'testproj', OTHER)))

    def test_wait_survives_observer_restart_and_leaves_unrelated_pipeline_free(self):
        self.round()
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        orchestrator.JOBS.clear()
        orchestrator.advance(self.conn, self.env(), OTHER, 16384, alive=lambda pid: False)
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        # A separate ready plan must not wait for either failed plan's repair.
        third = 'docs/plans/third/THIRD-PLAN.md'
        folder = self.make_worktree('third-br', 'thirdwt')
        self.ready_plan(third, 'third-br', folder, 'third.txt')
        with mock.patch.object(orchestrator, '_advance_pipeline') as pipeline:
            orchestrator.advance(self.conn, self.env(), third, 16384, alive=lambda pid: False)
        pipeline.assert_called_once()
        self.assertEqual(orchestrator._ahead(self.conn, 'testproj', third), [])

    def test_crossed_causes_do_not_create_a_wait_cycle(self):
        env = self.env()
        states = {p: orchestrator._new_state(self.conn, env, p, hives.get(self.conn, env.name, p))
                  for p in (REL, OTHER)}
        def diagnose(plan, diagnostic):
            state = states[plan]
            state['phase'] = 'fix'
            orchestrator._coordinate_repair(self.conn, env, plan, state, 'Check', diagnostic)
            orchestrator.save(self.conn, env.name, plan, state)
        first = 'Regression assertion failed: missing shared database schema table; inspect schema fixture and migration'
        second = 'Regression assertion failed: missing shared database schema index; inspect schema fixture and migration'
        diagnose(REL, first)
        diagnose(OTHER, second)
        diagnose(OTHER, first)
        diagnose(REL, second)
        self.assertEqual(self.state()['phase'], 'fix')
        self.assertEqual(self.state(OTHER)['phase'], 'shared-repair')
        self.assertEqual(len(self.state()['shared_causes']), 2)
