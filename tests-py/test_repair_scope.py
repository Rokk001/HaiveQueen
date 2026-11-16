"""Requirement-bound workflow review and tolerant, precise diagnosis decoding."""
import copy
import json
from unittest import mock

from bzztest import REL, RoundCase
from test_repair_contract_flow import RepairContractFlow
from haivequeen import bees, hives, package_diagnosis as diagnosis, repair_contract, repair_scope, runtime


class RepairScope(RoundCase):
    report = RepairContractFlow.report
    accept = RepairContractFlow.accept
    start = RepairContractFlow.start

    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()
        (self.base / 'assigned-agent-worktree' / 'workflow.py').write_text('# isolated workflow fixture\n')
        (self.repo / 'workflow.py').write_text('# isolated workflow fixture\n')
        checkout = mock.patch('haivequeen.package_diagnosis.checkout', return_value=self.repo)
        checkout.start(); self.addCleanup(checkout.stop)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}, {'id': 'P2', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.env().settings['escalateAfter'] = 1
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed',
                            'old-attempt', ['R1'], 'Admission remains unproven', force_failure=True)
        path = self.repo / REL
        text = path.read_text().replace('id = "P1"', 'id = "P1"\nrequirements = ["R1", "R2"]')
        text += '\n## Requirements\n- R1: Activate the candidate through the update worker.\n- R2: Restore the previous version when activation fails.\n'
        path.write_text(text)
        runtime.record_submission(self.conn, 'testproj', REL, text)

    def test_object_checkpoints_authorize_and_keep_expectations(self):
        scope, report = self.report()
        report['contract']['workflow']['checkpoints'] = [
            dict(id='value', expectation='Worker activates candidate'),
            dict(id='cleanup', expectation='Worker restores previous version')]
        accepted = self.accept(scope, report)
        workflow = accepted['contract']['workflow']
        self.assertEqual(workflow['checkpoints'], ['value', 'cleanup'])
        self.assertEqual(workflow['expectations']['cleanup'], 'Worker restores previous version')
        self.assertEqual(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope)['protocol'], 5)

    def test_duplicate_or_malformed_checkpoint_objects_do_not_grant_retry(self):
        for points in ([dict(id='x', expectation='x'), 'x'], [dict(id='x')], [dict(id=[], expectation='x')]):
            scope, report = self.report()
            report['contract']['workflow']['checkpoints'] = points
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.accept(scope, report)
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))

    def test_missing_source_and_malformed_workflow_fail_without_a_retry(self):
        for workflow in (None, [], 'scanner'):
            scope, report = self.report()
            report['contract']['workflow'] = workflow
            with self.subTest(workflow=workflow), self.assertRaises(ValueError):
                self.accept(scope, report)
        runtime.kv_delete(self.conn, 'testproj', runtime.SUBMISSION_KEY + REL)
        scope, report = self.report()
        with self.assertRaisesRegex(ValueError, 'scope source is incomplete'):
            self.accept(scope, report)

    def test_first_diagnosis_cannot_substitute_a_component_for_package_scope(self):
        for kind in ('missing-requirement', 'self-declared-scope', 'test-entrypoint'):
            scope, report = self.report()
            workflow = report['contract']['workflow']
            if kind == 'missing-requirement':
                del workflow['coverage']['R2']
            elif kind == 'self-declared-scope':
                workflow['scope'] = 'linux-scanner-only'
            else:
                workflow['entrypoint'] = 'test_activation.py'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.accept(scope, report)
            self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)

    def test_decision_invalidates_grant_and_source_text_is_authoritative(self):
        scope, report = self.report()
        self.accept(scope, report)
        runtime.add_decision(self.conn, 'testproj', 'owner', 'R2 must preserve the running game.', plan=REL)
        current = diagnosis.scope(self.conn, 'testproj', REL, 'P1', 'head-1')
        self.assertNotEqual(current['requirements'], scope['requirements'])
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', current))
        # An edited working copy cannot replace the submitted requirement text.
        path = self.repo / REL
        path.write_text(path.read_text().replace('Activate the candidate through the update worker.', 'Test one helper.'))
        path.write_text(path.read_text().replace('["R1", "R2"]', '["R1"]'))
        ref = repair_scope.reference(self.conn, 'testproj', REL, 'P1')
        self.assertIn('update worker', ref['requirements']['R1'])
        self.assertIn('R2', ref['requirements'])

    def test_legacy_workflow_is_preserved_but_cannot_lock_new_scope(self):
        scope, report = self.report()
        self.accept(scope, report)
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        state['report']['contract']['workflow'].pop('scope')
        state['workflow_contract'].pop('scope')
        state['authorization']['protocol'] = 4
        state['workflow_progress'] = dict(checks={'value':'passed','cleanup':'passed'})
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(state))
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        scope, report = self.report('head-2', 'Wire worker activation')
        report['contract']['workflow']['entrypoint'] = 'update_worker.run'
        self.accept(scope, report)
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(len(state['legacy_workflows']), 1)
        self.assertNotIn('workflow_progress', state)
        self.assertEqual(state['workflow_contract']['entrypoint'], 'update_worker.run')

    def review(self, report):
        repair_contract.validate(report)
        return repair_scope.review(self.env(), REL, 'P1', report, self.repo, 8192, lambda pid: False)

    def test_semantic_review_rejects_helper_despite_complete_coverage(self):
        _, report = self.report()
        report['contract']['workflow']['entrypoint'] = 'scanner.snapshot'
        with mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            self.assertEqual(self.review(report)[0], 'pending')
            prompt = launch.call_args.args[2]['prompt']
            self.assertIn('Reject a helper', prompt)
            self.assertIn('Restore the previous version', prompt)
            self.agent('scope-check', phase='verify', status='done')
            folder = self.env().run_dir('scope-check'); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'final.txt').write_text('READY-CHECK: FAILED\nR1/R2: scanner never invokes activation or rollback.')
            verdict, reason = self.review(report)
            self.assertEqual(verdict, 'failed')
            self.assertIn('activation or rollback', reason)
            self.assertEqual(self.review(report)[0], 'failed')
            self.assertEqual(launch.call_count, 1)

    def test_passed_review_survives_reload_and_is_reused_until_workflow_changes(self):
        _, report = self.report()
        with mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            self.review(report)
            self.agent('scope-check', phase='verify', status='done')
            folder = self.env().run_dir('scope-check'); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'final.txt').write_text('READY-CHECK: PASSED')
            self.assertEqual(self.review(report)[0], 'passed')
            self.conn.commit()
            self.assertEqual(self.review(copy.deepcopy(report))[0], 'passed')
            self.assertEqual(launch.call_count, 1)
            report['contract']['workflow']['entrypoint'] = 'another.path'
            self.assertEqual(self.review(report)[0], 'pending')
            self.assertEqual(launch.call_count, 2)

    def test_scheduler_waits_for_scope_review_then_dispatches_bound_repair(self):
        scope, report = self.report()
        folder = self.env().run_dir('diagnoser'); folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'package-diagnosis.json'; path.write_text(json.dumps(report))
        self.agent('diagnoser', phase='fix', status='done')
        state = dict(phase='running', agent='diagnoser', scope=scope, current_class='medium',
                     report_path=str(path.resolve()))
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(state))
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), \
                mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
            self.agent('scope-check', phase='verify', status='done')
            folder = self.env().run_dir('scope-check'); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'final.txt').write_text('READY-CHECK: PASSED')
            diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
            grant = diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope)
            self.assertIsNotNone(grant)
            self.assertEqual(set(grant['contract']['workflow']['coverage']), {'R1', 'R2'})
            self.assertEqual(launch.call_count, 1)
        self.assertTrue(self.start('workflow-repair'))
        assigned = diagnosis._load(self.conn, 'testproj', REL, 'P1')['assigned']
        self.assertEqual(assigned['contract']['workflow']['scope'], scope['requirements'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)

    def test_each_reviewed_input_invalidates_cached_success(self):
        _, report = self.report()
        with mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            self.review(report)
            self.agent('scope-check', phase='verify', status='done')
            folder = self.env().run_dir('scope-check'); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'final.txt').write_text('READY-CHECK: PASSED')
            self.assertEqual(self.review(report)[0], 'passed')
            key = 'repair-workflow-review:' + json.dumps([REL, 'P1'], separators=(',', ':'))
            cached = runtime.kv_get(self.conn, 'testproj', key)
            for field in ('behavior', 'criteria', 'prerequisites', 'action'):
                with self.subTest(field=field):
                    runtime.kv_set(self.conn, 'testproj', key, cached)
                    changed = copy.deepcopy(report)
                    if field == 'behavior': changed['contract']['repair']['behavior'] += ' with adoption'
                    elif field == 'criteria': changed['contract']['criteria'][0]['expectation'] += ' through worker'
                    elif field == 'prerequisites': changed['contract']['prerequisites'][0]['reason'] += ' newly observed'
                    else: changed['next_action'] += ' through public entrypoint'
                    self.assertEqual(self.review(changed)[0], 'pending')
            legacy = json.loads(cached)
            legacy['fingerprint'] = repair_contract.identity([report['contract']['workflow']['scope'], report['contract']['workflow']])
            runtime.kv_set(self.conn, 'testproj', key, json.dumps(legacy))
            self.assertEqual(self.review(report)[0], 'pending')

    def test_rejected_repair_correction_is_reviewed_before_dispatch(self):
        scope, report = self.report()
        with mock.patch('haivequeen.readygate._launch_check_agent', side_effect=[('old-review', ''), ('new-review', '')]) as launch:
            self.assertEqual(self.review(report)[0], 'pending')
            self.agent('old-review', phase='verify', status='done')
            folder = self.env().run_dir('old-review'); folder.mkdir(parents=True, exist_ok=True)
            (folder / 'final.txt').write_text('READY-CHECK: FAILED Status-only repair does not activate.')
            self.assertEqual(self.review(report)[0], 'failed')
            corrected = copy.deepcopy(report)
            corrected['next_action'] = 'Connect compatibility to activation and adoption.'
            corrected['contract']['repair']['behavior'] = 'Activate a compatible release and adopt existing work'
            corrected['contract']['criteria'][0]['expectation'] = 'Compatible release activates through the worker'
            self.assertEqual(corrected['contract']['workflow'], report['contract']['workflow'])
            folder = self.env().run_dir('corrector'); folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'package-diagnosis.json'; path.write_text(json.dumps(corrected))
            self.agent('corrector', phase='fix', status='done')
            runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(dict(
                phase='running', agent='corrector', scope=scope, current_class='medium', report_path=str(path.resolve()))))
            with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'):
                diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
                self.assertEqual(launch.call_count, 2)
                self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
                prompt = launch.call_args.args[2]['prompt']
                self.assertIn(corrected['next_action'], prompt)
                self.assertIn(corrected['contract']['repair']['behavior'], prompt)
                self.agent('new-review', phase='verify', status='done')
                folder = self.env().run_dir('new-review'); folder.mkdir(parents=True, exist_ok=True)
                (folder / 'final.txt').write_text('READY-CHECK: PASSED')
                diagnosis.advance(self.env(), 8192, alive=lambda pid: False)
            self.assertIsNotNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
            self.assertTrue(self.start('corrected-repair'))
            assigned = diagnosis._load(self.conn, 'testproj', REL, 'P1')['assigned']
            self.assertEqual(assigned['contract'], corrected['contract'])
            self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)

    def test_active_decision_can_exclude_requirement_but_invented_decision_cannot(self):
        decision = runtime.add_decision(self.conn, 'testproj', 'owner', 'R2 is set aside.', plan=REL)
        scope, report = self.report()
        report['reviewed_decisions'] = [decision]
        report['contract']['workflow']['coverage']['R2'] = dict(decision=decision, reason='Owner set aside R2')
        self.accept(scope, report)
        scope, report = self.report('head-2', 'Another repair')
        report['reviewed_decisions'] = [decision]
        report['contract']['workflow']['coverage']['R2'] = dict(decision=-1, reason='Invented exclusion')
        with self.assertRaises(ValueError):
            self.accept(scope, report)
