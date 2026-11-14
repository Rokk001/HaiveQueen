"""Package diagnosis authorization is scoped, economical and single-use."""
import json
from unittest import mock
from bzztest import REL, RoundCase
from haivequeen import bees, runtime


class PackageDiagnosis(RoundCase):
    def setUp(self):
        from haivequeen import package_diagnosis
        super().setUp()
        self.review = package_diagnosis
        self.record('failed-1')

    def record(self, attempt, package='P1'):
        bees.record_attempt(self.conn, 'testproj', REL, package, 'medium', 'failed',
                            attempt, ['R1'], 'specific failure', force_failure=True)

    def scope(self, commit='head-1', package='P1'):
        return self.review.scope(self.conn, 'testproj', REL, package, commit)

    def report(self, scope, **overrides):
        report = dict(kind='technical', commit=scope['commit'], failure_log=scope['failure_log'],
                      reviewed_decisions=[], evidence=['source.py:10 incorrect argument'],
                      analysis='The previous attempt changed the wrong caller.',
                      next_action='Correct the caller and run its regression.',
                      why_new='The actual failing caller has now been identified.')
        report.update(overrides)
        path = self.base / 'diagnosis.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path

    def test_authorization_is_single_use_and_keeps_failure_history(self):
        scope = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope,
                           'medium', ['medium', 'heavy'])
        self.assertEqual(self.review.authorization(self.conn, 'testproj', REL, 'P1', scope)['class'], 'medium')
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P2', self.scope(package='P2')))
        self.assertTrue(self.review.consume(self.conn, 'testproj', REL, 'P1', scope))
        self.assertFalse(self.review.consume(self.conn, 'testproj', REL, 'P1', scope))
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope,
                               'medium', ['medium', 'heavy'])

    def test_changed_commit_attempt_or_decision_invalidates_authorization(self):
        scope = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope,
                           'medium', ['medium'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', self.scope('head-2')))
        self.record('failed-2')
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', self.scope()))
        with mock.patch('haivequeen.repair_review.decisions', return_value=[{'id': 1, 'text': 'new decision'}]):
            self.assertNotEqual(scope, self.scope())
        with self.assertRaisesRegex(ValueError, 'current'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), self.scope(),
                               'medium', ['medium'])

    def test_costlier_class_requires_reason_and_configured_class(self):
        scope = self.scope()
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope, repair_class='heavy'),
                               scope, 'medium', ['medium', 'heavy'])
        with self.assertRaisesRegex(ValueError, 'configured'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                               self.report(scope, repair_class='heavy', class_reason='Cross-module diagnosis'),
                               scope, 'medium', ['medium'])

    def test_product_diagnosis_does_not_authorize_technical_retry(self):
        scope = self.scope()
        result = self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(scope, kind='product', question='Which behavior should the product expose?',
                        why_owner='The requirements permit two different user-visible behaviors.',
                        recommendation='Keep the documented behavior.'), scope, 'medium', ['medium'])
        self.assertEqual(result['kind'], 'product')
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', scope))

    def test_prompt_references_existing_package_history_without_full_error(self):
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed', 'long', [],
                            'large detail ' * 10000, force_failure=True)
        prompt = self.review.prompt(self.conn, 'testproj', REL, 'P1', 'head-1',
                                    self.base / 'diagnosis.json', 'medium', ['medium', 'heavy'])
        self.assertIn('bzz agent history', prompt)
        self.assertIn('--package "P1"', prompt)
        self.assertNotIn('orch-repair-history:', prompt)
        self.assertLess(len(prompt), 7000)

    def test_new_attempt_does_not_license_the_same_unchanged_approach(self):
        scope = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope,
                           'medium', ['medium'])
        self.review.consume(self.conn, 'testproj', REL, 'P1', scope)
        self.record('failed-again')
        current = self.scope()
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(current), current,
                               'medium', ['medium'])

    def test_cached_scope_cannot_consume_after_another_failure(self):
        previous = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(previous), previous,
                           'medium', ['medium'])
        self.record('new-failure')
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', previous))
        self.assertFalse(self.review.consume(self.conn, 'testproj', REL, 'P1', previous))

    def test_class_reason_must_be_text(self):
        current = self.scope()
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                               self.report(current, repair_class='heavy', class_reason=42),
                               current, 'medium', ['medium', 'heavy'])

class PackageDiagnosisLifecycle(PackageDiagnosis):
    def test_cancelled_plan_never_starts_queued_diagnosis(self):
        self.plan('CANCELLED', [{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        self.review.request(env, REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0), \
             mock.patch('haivequeen.loop._do_start', return_value=False) as launch:
            self.review.advance(env, 16384, alive=lambda pid: False)
        launch.assert_not_called()

    def test_real_start_consumes_diagnosed_approach_once(self):
        from haivequeen import hives
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        env.settings['escalateAfter'] = 1
        scope = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope, 'medium', ['medium'])
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'):
            result = bees.start_process(env, REL, 'P1', 'Worker', 'medium', 'task', ['one'], [], [])
            self.assertTrue(result)
            self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', scope))
            bees.close_embedded(self.conn, 'testproj', REL, 'P1')
            self.assertFalse(bees.start_process(env, REL, 'P1', 'Next', 'medium', 'task', ['one'], [], []))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)

    def test_only_dependent_work_waits_for_review(self):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'},
                                 {'id': 'P2', 'class': 'medium', 'depends': ['P1']}])
        env = self.env()
        self.review.request(env, REL, 'P1', 'medium')
        self.assertTrue(self.review.waiting(env, REL))
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}, {'id': 'P2', 'class': 'medium'}])
        self.assertFalse(self.review.waiting(env, REL))

    def test_authorized_package_wakes_without_an_owner_question(self):
        from haivequeen import hives
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='done', provider='codex', pid=None)
        self.review.request(self.env(), REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.advance'), \
             mock.patch('haivequeen.hives.ended_without_work', return_value=True):
            self.round()
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open'), [])
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        state['phase'] = 'authorized'
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'), json.dumps(state))
        self.assertFalse(self.review.waiting(self.env(), REL))

    def test_unanswered_product_question_does_not_restart_diagnosis(self):
        from haivequeen import db
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        qid = db.add_question(self.conn, 'testproj', REL, 'P1', 'Choose supported behavior?', 'Keep it.')
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'),
                       json.dumps({'phase': 'owner', 'question': qid, 'scope': self.scope()}))
        self.review.request(env, REL, 'P1', 'medium')
        self.assertEqual(self.review._load(self.conn, 'testproj', REL, 'P1')['phase'], 'owner')
        db.answer_question(self.conn, qid, 'Keep it.', 'owner')
        self.review.request(env, REL, 'P1', 'medium')
        self.assertEqual(self.review._load(self.conn, 'testproj', REL, 'P1')['phase'], 'pending')

    def test_verified_package_cancels_pending_diagnosis(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        self.review.request(env, REL, 'P1', 'medium')
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'verified')
        with mock.patch('haivequeen.loop._do_start', return_value=False) as launch:
            self.review.advance(env, 16384, alive=lambda pid: False)
        launch.assert_not_called()
        self.assertEqual(self.review._load(self.conn, 'testproj', REL, 'P1')['phase'], 'completed')

    def test_failure_requests_diagnosis_before_escalation_or_owner_question(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        env.settings['escalateAfter'] = 1
        bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'exhausted', error='caller regression', force_failure=True)
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(state.get('phase'), 'pending')
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1'), [])

    def test_diagnosis_runner_uses_configured_class_and_validates_report(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        env.settings['diagnosisClass'] = 'light'
        self.review.request(env, REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0):
            self.review.advance(env, 16384, alive=lambda pid: False)
            state = self.review._load(self.conn, 'testproj', REL, 'P1')
            self.assertEqual(state['phase'], 'running')
            agent = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['name'] == state['agent'])
            self.assertEqual(agent['class'], 'light')
            self.assertEqual(agent['package'], '')
            report = self.report(state['scope'])
            (env.run_dir(state['agent']) / 'package-diagnosis.json').write_text(report.read_text(), encoding='utf-8')
            runtime.upsert_agent(self.conn, 'testproj', state['agent'], {'status': 'done', 'pid': None})
            self.review.advance(env, 16384, alive=lambda pid: False)
            self.assertIsNotNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', self.scope()))

    def test_exhausted_package_waits_for_diagnosis_instead_of_stronger_model(self):
        from haivequeen import hives
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='running', provider='codex', pid=42)
        env = self.env()
        env.settings['escalateAfter'] = 1
        with mock.patch('haivequeen.loop._do_start', return_value=True) as launch, \
             mock.patch('haivequeen.loop.ram_gb', return_value=16):
            result = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], no_subagents=True)
        self.assertFalse(result)
        self.assertIn('diagnosis', bees.LAST_REFUSAL)
        launch.assert_not_called()
