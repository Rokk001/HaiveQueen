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
        previous = self.review._load(self.conn, 'testproj', REL, 'P1').get('report')
        if previous:
            latest = bees.attempt_history(self.conn, 'testproj', REL, 'P1', limit=1)['attempts'][0]['attempt']
            report['continuation'] = dict(previous=self.review._digest(previous), attempt=latest,
                outcome='partial', reason='The fixture is repaired; the caller still fails.',
                target='caller.py', change='Correct the caller prerequisite identified by ' + latest,
                verification='Run the caller regression, red before the change and green after it.')
        report.update(overrides)
        path = self.base / 'diagnosis.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path

    def test_memory_recovery_closes_only_resource_wait_without_changing_scope(self):
        from haivequeen import db
        env = self.env()
        scope = self.scope()
        self.review.accept(self.conn, env.name, REL, 'P1', self.report(scope), scope, 'medium', ['medium'])
        text = ('The P1 retry diagnosis has remained pending for several minutes while free physical memory is below the configured 2 GiB start threshold. '
                'Can you free enough memory for the diagnosis and subsequent agent to start?')
        qid = db.add_question(self.conn, env.name, REL, 'P1-memory', text, '')
        product = db.add_question(self.conn, env.name, REL, 'P1-memory', 'Should we lower the memory limit?', '')
        other = db.add_question(self.conn, env.name, 'other.md', 'P1-memory', text, '')
        with mock.patch('haivequeen.loop._min_ram', return_value=2):
            self.review.resolve_memory_wait(env, REL, 'P1', 1024)
            self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (qid,)).fetchone()[0], 'open')
            self.review.resolve_memory_wait(env, REL, 'P1', 4096)
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (qid,)).fetchone()[0], 'answered')
        for untouched in (product, other):
            self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (untouched,)).fetchone()[0], 'open')
        self.assertEqual(scope, self.scope())
        self.assertIsNotNone(self.review.authorization(self.conn, env.name, REL, 'P1', scope))
        self.assertEqual(bees.failure_count(self.conn, env.name, REL, 'P1', 'medium'), 1)

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

    def repeat_scope(self):
        previous = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(previous), previous,
                           'medium', ['medium'])
        self.review.consume(self.conn, 'testproj', REL, 'P1', previous)
        self.record('no-progress')
        return self.scope()

    def experiment(self, name='probe.txt', output='caller reproduction: exit 1'):
        (self.base / name).write_text(output, encoding='utf-8')
        return dict(question='Which caller fails?', command='focused caller probe', exit=1,
                    finding='The second caller reproduces the defect.', log=name)

    def test_paraphrase_after_no_progress_cannot_authorize_retry(self):
        current = self.repeat_scope()
        with self.assertRaisesRegex(ValueError, 'experiment'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, next_action='Repair the incorrect caller with a test.'),
                current, 'medium', ['medium'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))
        prompt = self.review.prompt(self.conn, 'testproj', REL, 'P1', 'head-1',
                                   self.base / 'diagnosis.json', 'medium', ['medium'])
        self.assertIn('UNCHANGED CODE', prompt)
        self.assertIn('experiment:', prompt)
        self.assertLess(len(prompt), 7000)

    def test_new_probe_justifies_same_approach_and_reaches_repair_agent(self):
        current = self.repeat_scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, experiment=self.experiment()), current, 'medium', ['medium'])
        instruction = self.review.authorization(self.conn, 'testproj', REL, 'P1', current)['instruction']
        self.assertIn('second caller', instruction)
        self.assertIn('probe.txt', instruction)
        self.assertTrue(self.review.consume(self.conn, 'testproj', REL, 'P1', current))
        self.record('no-progress-again')
        current = self.scope()
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, next_action='Different wording', experiment=self.experiment('renamed.txt')),
                current, 'medium', ['medium'])

    def test_probe_requires_real_local_output_and_exit_code(self):
        current = self.repeat_scope()
        valid = self.experiment()
        for change in ({'log': 'missing.txt'}, {'log': '../outside.txt'}, {'exit': True}, {'finding': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.review.accept(self.conn, 'testproj', REL, 'P1',
                    self.report(current, experiment={**valid, **change}), current, 'medium', ['medium'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

    def test_changed_code_can_receive_new_diagnosis_without_probe(self):
        self.repeat_scope()
        current = self.scope('head-2')
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(current), current, 'medium', ['medium'])
        self.assertIsNotNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

    def test_fresh_probe_without_repair_postmortem_cannot_restart_work(self):
        current = self.repeat_scope()
        with self.assertRaisesRegex(ValueError, 'continuation'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, experiment=self.experiment(), continuation=None), current, 'medium', ['medium'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

    def test_changed_commit_does_not_bypass_last_attempt_review(self):
        self.repeat_scope()
        current = self.scope('fixture-only-commit')
        path = self.report(current)
        report = json.loads(path.read_text())
        report['continuation']['attempt'] = 'obsolete-attempt'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'latest repair attempt'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', path, current, 'medium', ['medium'])

    def test_repeated_action_rejected_despite_fresh_output_and_incidental_commit(self):
        current = self.repeat_scope()
        path = self.report(current, experiment=self.experiment())
        self.review.accept(self.conn, 'testproj', REL, 'P1', path, current, 'medium', ['medium'])
        old = json.loads(path.read_text())['continuation']
        self.review.consume(self.conn, 'testproj', REL, 'P1', current)
        self.record('still-no-implementation')
        current = self.scope('fixture-only-commit')
        path = self.report(current, experiment=self.experiment(output='new timestamp; same defect'))
        report = json.loads(path.read_text())
        report['continuation'].update(outcome='no_progress', target=old['target'].upper(),
                                      change='  ' + old['change'] + '  ')
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.review.accept(self.conn, 'testproj', REL, 'P1', path, current, 'medium', ['medium'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))
        # A genuinely narrower prerequisite can progress without an owner question.
        report['continuation']['change'] = 'Add admission-state validation before the caller spawns.'
        path.write_text(json.dumps(report))
        self.review.accept(self.conn, 'testproj', REL, 'P1', path, current, 'medium', ['medium'])
        grant = self.review.authorization(self.conn, 'testproj', REL, 'P1', current)
        self.assertIn('admission-state validation', grant['instruction'])
        self.assertNotIn(report['next_action'], grant['instruction'])
        self.assertTrue(self.review.consume(self.conn, 'testproj', REL, 'P1', current))
        self.assertFalse(self.review.consume(self.conn, 'testproj', REL, 'P1', current))
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open'), [])

    def test_same_probe_output_after_partial_fix_allows_one_scoped_repair(self):
        current = self.repeat_scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, experiment=self.experiment()), current, 'medium', ['medium'])
        self.review.consume(self.conn, 'testproj', REL, 'P1', current)
        self.record('partial-fixture-fix')
        current = self.scope('head-2')
        # The defect is still reproducible after an incidental code change.
        self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, experiment=self.experiment('new-run.txt')), current, 'medium', ['medium'])
        grant = self.review.authorization(self.conn, 'testproj', REL, 'P1', current)
        self.assertIn('Supporting fixture changes alone do not complete', grant['instruction'])
        self.assertIn('Correct the caller', grant['instruction'])
        self.assertTrue(self.review.consume(self.conn, 'testproj', REL, 'P1', current))
        self.assertFalse(self.review.consume(self.conn, 'testproj', REL, 'P1', current))
        self.record('same-head-failed')
        current = self.scope('head-2')
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, experiment=self.experiment('third-run.txt')), current, 'medium', ['medium'])
        prompt = self.review.prompt(self.conn, 'testproj', REL, 'P1', 'head-2',
                                   self.base / 'diagnosis.json', 'medium', ['medium'])
        self.assertIn('Continue the unresolved repair objective', prompt)
        self.assertLess(len(prompt), 7000)

    def test_legacy_hash_is_not_a_global_ban_on_later_commits(self):
        import hashlib
        current = self.repeat_scope()
        experiment = self.experiment()
        digest = hashlib.sha256((self.base / experiment['log']).read_bytes()).hexdigest()
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        state['experiment_digests'] = [digest]
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'), json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, experiment=experiment), current, 'medium', ['medium'])
        current = self.scope('head-2')
        self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, experiment=experiment), current, 'medium', ['medium'])
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        self.assertEqual({x['commit'] for x in state['experiment_scopes']}, {'head-1', 'head-2'})
        self.assertEqual(state['experiment_digests'], [digest])
        # A return to the old commit does not reset its previous evidence guard.
        current = self.scope('head-1')
        with self.assertRaisesRegex(ValueError, 'already'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, experiment=experiment), current, 'medium', ['medium'])

    def test_real_external_prerequisite_still_reaches_owner_after_no_progress(self):
        current = self.repeat_scope()
        result = self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, kind='external', evidence=['probe: missing authorized test device'],
                        question='Provide the required test device?', why_owner='No authorized device is available.',
                        recommendation='Connect the test device.'), current, 'medium', ['medium'])
        self.assertEqual(result['kind'], 'external')
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

    def test_legacy_accepted_report_does_not_reset_no_progress_protection(self):
        current = self.repeat_scope()
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        state.pop('consumed_scopes')
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'), json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'experiment'):
            self.review.accept(self.conn, 'testproj', REL, 'P1',
                self.report(current, next_action='Same repair paraphrased after upgrade'),
                current, 'medium', ['medium'])

class PackageDiagnosisLifecycle(PackageDiagnosis):
    def test_scheduler_keeps_accepted_baseline_across_new_review_and_restart(self):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        initial = self.scope()
        self.review.accept(self.conn, env.name, REL, 'P1', self.report(initial), initial, 'medium', ['medium'])
        self.review.consume(self.conn, env.name, REL, 'P1', initial)
        self.record('partial-fix')
        self.review.request(env, REL, 'P1', 'medium')
        prompts = []

        def launch(ctx, action, **kwargs):
            prompts.append(action['prompt'])
            ctx['state']['names'].append('review-new')
            runtime.upsert_agent(self.conn, env.name, 'review-new',
                                 dict(plan=REL, phase='fix', status='done', exitCode=0))
            return True

        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-2'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0), \
             mock.patch('haivequeen.loop._do_start', side_effect=launch):
            self.review.advance(env, 16384, alive=lambda pid: False)
            state = self.review._load(self.conn, env.name, REL, 'P1')
            self.assertEqual(state['phase'], 'running')
            self.assertEqual(state['scope']['commit'], 'head-2')
            self.assertNotIn('UNCHANGED CODE', prompts[0])
            output = env.run_dir('review-new') / 'package-diagnosis.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(self.report(state['scope']).read_bytes())
            # advance reloads persisted state, as after an orchestrator restart.
            self.review.advance(env, 16384, alive=lambda pid: False)
        state = self.review._load(self.conn, env.name, REL, 'P1')
        self.assertEqual(state['phase'], 'authorized')
        self.assertEqual(state['accepted_scope']['commit'], 'head-2')
        self.assertIsNotNone(self.review.authorization(self.conn, env.name, REL, 'P1', self.scope('head-2')))
        self.assertEqual(runtime.list_questions(self.conn, env.name, status='open'), [])

    def test_legacy_running_review_uses_report_commit_not_running_scope(self):
        previous = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(previous), previous, 'medium', ['medium'])
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        state.pop('accepted_scope', None)
        state['scope'] = self.scope('head-2')
        state['phase'] = 'running'
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'), json.dumps(state))
        current = self.scope('head-2')
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(current), current, 'medium', ['medium'])
        self.assertIsNotNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

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
            self.assertNotIn('Work only package P1: task', result['prompt'])
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

    def test_followup_start_uses_bounded_steps_instead_of_queen_package_task(self):
        from haivequeen import hives
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        current = self.repeat_scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1',
            self.report(current, experiment=self.experiment()), current, 'medium', ['medium'])
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'):
            result = bees.start_process(self.env(), REL, 'P1', 'Worker', 'medium',
                                        'Complete the entire package again', ['Repeat all investigation'], [], [])
        self.assertTrue(result)
        self.assertNotIn('Complete the entire package again', result['prompt'])
        self.assertNotIn('Repeat all investigation', result['prompt'])
        self.assertIn('Verify: Run the caller regression', result['prompt'])
        self.assertIn('Why it stopped:', result['prompt'])
        self.assertIsNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', current))

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

    def test_historical_success_does_not_cancel_new_review(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'old-success')
        self.review.request(env, REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0):
            self.review.advance(env, 16384, alive=lambda pid: False)
        self.assertEqual(self.review._load(self.conn, env.name, REL, 'P1')['phase'], 'running')
        self.assertTrue(bees.package_done(self.conn, env.name, REL, 'P1'))

    def test_duplicate_success_cannot_cancel_requested_review(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'old-success')
        self.review.request(env, REL, 'P1', 'medium')
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'old-success')
        self.assertEqual(self.review._load(self.conn, env.name, REL, 'P1')['phase'], 'pending')
        self.assertTrue(self.review.waiting(env, REL))
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'new-success')
        self.assertEqual(self.review._load(self.conn, env.name, REL, 'P1')['phase'], 'completed')

    def test_legacy_pending_review_preserves_history_and_runs(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'old-success')
        runtime.kv_set(self.conn, env.name, self.review._key(REL, 'P1'),
                       json.dumps({'phase': 'pending', 'current_class': 'medium', 'accepted': ['old-signature']}))
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0):
            self.review.advance(env, 16384, alive=lambda pid: False)
        state = self.review._load(self.conn, env.name, REL, 'P1')
        self.assertEqual(state['phase'], 'running')
        self.assertEqual(state['accepted'], ['old-signature'])

    def test_new_success_supersedes_legacy_review_before_next_round(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        runtime.kv_set(self.conn, env.name, self.review._key(REL, 'P1'),
                       json.dumps({'phase': 'pending', 'current_class': 'medium'}))
        bees.report_attempt(env, REL, 'P1', 'medium', 'succeeded', 'new-success')
        self.assertEqual(self.review._load(self.conn, env.name, REL, 'P1')['phase'], 'completed')

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

    def test_recovered_memory_wakes_plan_with_existing_authorization(self):
        from haivequeen import db, hives
        self.plan('WAITING', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        question = db.add_question(self.conn, 'testproj', REL, 'P1-memory',
            'The P1 retry diagnosis has remained pending for several minutes while free physical memory is below the configured 2 GiB start threshold. '
            'Can you free enough memory for the diagnosis and subsequent agent to start?', '')
        self.conn.execute("UPDATE hives SET state='answer',questions_json=? WHERE project=? AND plan_path=?",
                          (json.dumps([question]), 'testproj', REL))
        scope = self.scope()
        self.review.accept(self.conn, 'testproj', REL, 'P1', self.report(scope), scope, 'medium', ['medium'])
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        state.update(phase='authorized', current_class='medium')
        runtime.kv_set(self.conn, 'testproj', self.review._key(REL, 'P1'), json.dumps(state))
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.loop._min_ram', return_value=2):
            self.round(alive=lambda pid: False, free_ram=lambda: 1)
            self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (question,)).fetchone()[0], 'open')
            self.round(alive=lambda pid: False, free_ram=lambda: 4)
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (question,)).fetchone()[0], 'answered')
        self.assertNotEqual(self.conn.execute('SELECT state FROM hives WHERE project=? AND plan_path=?', ('testproj', REL)).fetchone()[0], 'answer')
        self.assertIsNotNone(self.review.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)

    def test_pending_review_runs_while_scheduler_question_is_open(self):
        from haivequeen import db, hives
        self.plan('WAITING', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        question = db.add_question(self.conn, 'testproj', REL, 'diagnosis-scheduler',
                                   'The package review has not started.', 'Investigate the scheduler.')
        self.conn.execute("UPDATE hives SET state='answer',questions_json=? WHERE project=? AND plan_path=?",
                          (json.dumps([question]), 'testproj', REL))
        env = self.env()
        self.review.request(env, REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)), \
             mock.patch('haivequeen.loop._min_ram', return_value=0):
            self.round(alive=lambda pid: False, free_ram=lambda: 16)
        state = self.review._load(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(state['phase'], 'running')
        self.assertIsNone(state.get('authorization'))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (question,)).fetchone()[0], 'open')

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

    def test_invalid_reports_allow_one_correction_without_resetting_attempts(self):
        self.plan(packages=[{'id': 'P1', 'class': 'medium'}])
        env = self.env()
        self.review.request(env, REL, 'P1', 'medium')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
             mock.patch('haivequeen.bees.plan_worktree', return_value=('branch', self.wt)):
            for _ in range(2):
                self.review.advance(env, 16384, alive=lambda pid: False)
                state = self.review._load(self.conn, env.name, REL, 'P1')
                self.assertEqual(state['phase'], 'running')
                self.finish(state['agent'])  # Missing structured output, not a project repair.
                self.review.advance(env, 16384, alive=lambda pid: False)
            state = self.review._load(self.conn, env.name, REL, 'P1')
            self.assertEqual(state['phase'], 'owner')
            with mock.patch('haivequeen.loop._do_start') as launch:
                self.review.advance(env, 16384, alive=lambda pid: False)
            launch.assert_not_called()
        self.assertEqual(bees.failure_count(self.conn, env.name, REL, 'P1', 'medium'), 1)
        self.assertIsNone(state.get('authorization'))
