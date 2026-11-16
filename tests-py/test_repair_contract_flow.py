"""Replay failed package -> feasible repair -> verified progress -> next repair."""
import contextlib
import copy
import io
import json
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, cli, hives, package_diagnosis as diagnosis, repair_contract, runtime


class RepairContractFlow(RoundCase):
    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}, {'id': 'P2', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.env().settings['escalateAfter'] = 1
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed',
                            'old-attempt', ['R1'], 'Admission remains unproven', force_failure=True)

    def report(self, head='head-1', objective='Validate candidate', blocked=False):
        scope = diagnosis.scope(self.conn, 'testproj', REL, 'P1', head)
        data = dict(kind='technical', commit=head, failure_log=scope['failure_log'], reviewed_decisions=[],
            evidence=['isolated candidate verifier is callable', 'legacy admission is unproven'],
            analysis='The worker path assumes missing admission proof.',
            next_action='Implement ' + objective + '; on rejection release the owned reservation.',
            why_new='Test the independent primitive without assuming admission.',
            contract=dict(objective=objective, why_executable='The primitive is callable without the worker.',
                prerequisites=[dict(id='admission', status='unresolved', reason='legacy callers ignore new gates'),
                               dict(id='primitive', status='verified', reason='isolated probe passed', evidence=[0])],
                requires=['admission'] if blocked else ['primitive'], resolves=[],
                repair=dict(goal='Safe activation', behavior='Reject changed candidate', criterion='reject', implementation_paths=['activation.py'], regression_paths=['test_activation.py']),
                criteria=[dict(id='reject', expectation='Reject changed candidate', check='python -m unittest changed'),
                          dict(id='cleanup', expectation='Release owned reservation on rejection', check='python -m unittest cleanup')]))
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        if state.get('report'):
            latest = bees.attempt_history(self.conn, 'testproj', REL, 'P1', limit=1)['attempts'][0]['attempt']
            data['continuation'] = dict(previous=diagnosis._digest(state['report']), attempt=latest,
                outcome='step_complete' if latest in state.get('progress', {}) else 'partial',
                reason='Assess the assigned step separately from the package.', target='activation.py',
                change=objective, verification='Run the prescribed criterion checks.')
        return scope, data

    def accept(self, scope, data):
        path = self.base / 'diagnosis.json'
        path.write_text(json.dumps(data))
        return diagnosis.accept(self.conn, 'testproj', REL, 'P1', path, scope, 'medium', ['medium', 'heavy'])

    def start(self, name, head='head-1'):
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value=head):
            return bees.start_process(self.env(), REL, 'P1', name, 'medium',
                                      'Complete the whole package', ['Repeat broad investigation'], [], [])

    def receipt(self, attempt, head='head-2'):
        assigned = diagnosis._load(self.conn, 'testproj', REL, 'P1')['assigned']
        checks = []
        for criterion in assigned['contract']['criteria']:
            log = criterion['id'] + '.log'
            (self.base / log).write_text('Ran focused check: OK')
            checks.append(dict(id=criterion['id'], command=criterion['check'], exit=0, log=log))
        data = dict(attempt=attempt, contract=assigned['id'], commit=head, checks=checks)
        path = self.base / 'repair-result.json'
        path.write_text(json.dumps(data))
        return path, data

    def result(self, attempt, outcome, path='', head='head-2', real_evidence=False):
        args = cli.build_parser().parse_args(['agent', 'result', '--project', 'testproj', '--plan', REL,
            '--package', 'P1', '--class', 'medium', '--attempt', attempt, '--outcome', outcome,
            '--open-requirements', '["R1"]', '--error', 'Package remains open',
            '--ended-evidence', str(path)] + (['--repair-result', str(path)] if outcome == 'progress' else []))
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), \
             mock.patch.object(cli.config, 'load_config', return_value=self.machine), \
             mock.patch('haivequeen.package_diagnosis.current_commit', return_value=head), \
             (contextlib.nullcontext() if real_evidence else mock.patch('haivequeen.repair_contract.implementation_evidence', side_effect=lambda receipt, *args: receipt.update(implementation={'goal': 'Safe activation'}))), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cli.cmd_bee(args, self.conn)
        return status, output.getvalue()

    def test_failed_package_progress_and_next_repair_survive_reload(self):
        scope, blocked = self.report(blocked=True)
        with self.assertRaisesRegex(ValueError, 'unresolved prerequisite'):
            self.accept(scope, blocked)
        scope, data = self.report()
        self.accept(scope, data)
        launched = self.start('repair-one')
        self.assertTrue(launched)
        self.assertIn('release the owned reservation', launched['prompt'])
        self.assertIn('Release owned reservation on rejection', launched['prompt'])
        self.assertNotIn('Repeat broad investigation', launched['prompt'])
        path, receipt = self.receipt('repair-one')
        status, output = self.result('repair-one', 'progress', path)
        self.assertEqual(status, 0, output)
        answer = json.loads(output)
        self.assertEqual(answer['nextAction'], 'wait_for_diagnosis')
        self.assertNotIn('nextClass', answer)
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        self.assertFalse(bees.package_done(self.conn, 'testproj', REL, 'P1'))
        self.assertIsNone(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1') or None)
        # Re-open DB as on scheduler restart: progress is not in-memory state.
        import sqlite3
        db_path = self.conn.execute('PRAGMA database_list').fetchone()[2]
        with contextlib.closing(sqlite3.connect(db_path)) as reopened:
            saved = json.loads(reopened.execute('SELECT value FROM kv WHERE project=? AND key=?',
                ('testproj', diagnosis._key(REL, 'P1'))).fetchone()[0])
        self.assertEqual(saved['progress']['repair-one']['contract'], receipt['contract'])
        prompt = diagnosis.prompt(self.conn, 'testproj', REL, 'P1', 'head-2', self.base/'next.json', 'medium', ['medium'])
        self.assertIn('latest assigned repair step passed', prompt)
        self.assertNotIn('UNCHANGED CODE AFTER', prompt)
        self.assertFalse(diagnosis.waiting(self.env(), REL))  # independent P2 remains runnable
        scope, next_report = self.report('head-2', 'Establish legacy admission protocol')
        next_report['contract']['resolves'] = ['admission']
        self.accept(scope, next_report)
        self.assertTrue(self.start('repair-two', 'head-2'))
        # Replayed successful receipt must not close the new attempt.
        status, _ = self.result('repair-one', 'progress', path)
        self.assertNotEqual(status, 0)
        self.assertTrue(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1'))
        # A genuine new failure remains a failure, with the earlier progress retained.
        second_path, second_receipt = self.receipt('repair-two')
        second_receipt['checks'][0]['exit'] = 1
        second_path.write_text(json.dumps(second_receipt))
        status, output = self.result('repair-two', 'failed', second_path)
        self.assertEqual(status, 0, output)
        saved = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        self.assertIn('repair-one', saved['progress'])
        self.assertNotIn('repair-two', saved.get('progress', {}))
        self.assertEqual(saved['phase'], 'pending')
        self.assertFalse(bees.package_done(self.conn, 'testproj', REL, 'P1'))

    def test_incomplete_or_wrong_receipt_cannot_report_progress(self):
        self.accept(*self.report())
        self.start('repair-one')
        path, data = self.receipt('repair-one')
        for change in ('missing', 'red', 'wrong-contract', 'wrong-commit', 'empty-log', 'unknown-head'):
            bad = copy.deepcopy(data)
            if change == 'missing': bad['checks'].pop()
            if change == 'red': bad['checks'][0]['exit'] = 1
            if change == 'wrong-contract': bad['contract'] = 'other'
            if change == 'wrong-commit': bad['commit'] = 'old'
            if change == 'empty-log': bad['checks'][0]['log'] = 'missing.log'
            if change == 'unknown-head': bad['commit'] = None
            path.write_text(json.dumps(bad))
            with self.subTest(change=change):
                status, _ = self.result('repair-one', 'progress', path, None if change == 'unknown-head' else 'head-2')
                self.assertNotEqual(status, 0)
                self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
                self.assertTrue(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1'))

    def test_unresolved_precondition_cannot_be_assumed_or_dropped(self):
        scope, data = self.report()
        self.accept(scope, data)
        for variant in ('drop', 'assume', 'invent-evidence', 'circular'):
            scope, follow = self.report('head-2', 'New independently executable step')
            c = follow['contract']
            if variant == 'drop': c['prerequisites'].pop(0)
            if variant == 'assume': c['requires'] = ['admission']
            if variant == 'invent-evidence': c['prerequisites'][0].update(status='verified', evidence=[99])
            if variant == 'circular': c['resolves'] = ['primitive']
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.accept(scope, follow)

    def test_legacy_grant_cannot_bypass_contract_after_update(self):
        scope = self.report()[0]
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(dict(scope=scope,
            phase='authorized', authorization=dict(instruction='repeat the broad task', **{'class': 'medium'}))))
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertFalse(self.start('repair-one'))
        self.assertEqual(diagnosis._load(self.conn, 'testproj', REL, 'P1')['phase'], 'pending')

    def test_real_failure_requests_diagnosis_not_higher_class(self):
        for n in range(4):
            status, output = self.result('failure-%d' % n, 'failed')
            self.assertEqual(status, 0, output)
        self.assertNotIn('nextClass', json.loads(output))
        self.assertEqual(json.loads(output)['nextAction'], 'wait_for_diagnosis')

    def test_followup_does_not_drop_cleanup_from_full_diagnosis(self):
        scope, data = self.report()
        self.accept(scope, data)
        diagnosis.consume(self.conn, 'testproj', REL, 'P1', scope)
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed', 'first', ['R1'], 'still blocked')
        scope, data = self.report('head-2', 'Isolate final verification')
        self.accept(scope, data)
        started = self.start('followup', 'head-2')
        self.assertIn('release the owned reservation', started['prompt'])
        self.assertIn('Release owned reservation on rejection', started['prompt'])
        self.assertIn('python -m unittest cleanup', started['prompt'])

    def test_legacy_receipt_is_observation_not_repair_and_replay_is_idempotent(self):
        self.accept(*self.report())
        self.start('legacy-step')
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        state['assigned']['contract'].pop('repair')
        state['assigned']['id'] = repair_contract.identity(state['assigned']['contract'])
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(state))
        path, _ = self.receipt('legacy-step', 'head-1')
        for _ in range(2):
            status, output = self.result('legacy-step', 'progress', path, 'head-1')
            self.assertEqual(status, 0, output)
            self.assertEqual(json.loads(output)['outcome'], 'observed')
        saved = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        self.assertIn('legacy-step', saved['observations'])
        self.assertNotIn('legacy-step', saved.get('progress', {}))
        self.assertEqual(saved['phase'], 'pending')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        self.assertFalse(bees.package_done(self.conn, 'testproj', REL, 'P1'))

    def test_new_commits_and_renamed_investigations_do_not_authorize_repairs(self):
        self.accept(*self.report())
        self.start('first')
        path, _ = self.receipt('first')
        self.assertEqual(self.result('first', 'progress', path)[0], 0)
        for i in range(5):
            scope, data = self.report('head-' + str(i + 2), 'New observation ' + str(i))
            data['contract'].pop('repair')
            with self.assertRaisesRegex(ValueError, 'perform investigations in diagnosis'):
                self.accept(scope, data)
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        scope, data = self.report('head-2', 'Implement the established fix')
        self.accept(scope, data)
        self.assertTrue(self.start('actual-repair', 'head-2'))

    def test_goal_cannot_be_replaced_by_another_question(self):
        self.accept(*self.report())
        scope, data = self.report('head-2', 'Investigate something else')
        data['contract']['repair']['goal'] = 'Discover another observation'
        with self.assertRaisesRegex(ValueError, 'retain the repair goal'):
            self.accept(scope, data)

    def test_protocol_two_authorization_is_not_reused(self):
        scope, data = self.report()
        self.accept(scope, data)
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        state['authorization']['protocol'] = 2
        runtime.kv_set(self.conn, 'testproj', diagnosis._key(REL, 'P1'), json.dumps(state))
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertFalse(diagnosis.consume(self.conn, 'testproj', REL, 'P1', scope, 'legacy'))
        self.assertFalse(self.start('legacy'))
        self.assertEqual(diagnosis._load(self.conn, 'testproj', REL, 'P1')['phase'], 'pending')

    def test_real_behavior_fix_runs_through_authorization_receipt_and_next_step(self):
        import subprocess, sys
        folder = self.base / 'assigned-agent-worktree'
        def git(*args):
            return subprocess.run(['git', '-C', str(folder), *args], capture_output=True,
                                  text=True, check=True, timeout=10).stdout.strip()
        git('init', '-q'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
        (folder / 'activation.py').write_text('value = 0\n')
        test = folder / 'test_activation.py'
        test.write_text('from activation import value\nassert value == 1\n')
        git('add', 'activation.py', 'test_activation.py'); git('commit', '-qm', 'Initial defect')
        before = git('rev-parse', 'HEAD')
        scope, report = self.report(before)
        command = 'python -B test_activation.py'
        report['contract']['criteria'] = [dict(id='reject', expectation='Correct required value', check=command)]
        self.accept(scope, report)
        self.assertTrue(self.start('real-repair', before))
        baseline = subprocess.run([sys.executable, '-B', str(test)], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(baseline.returncode, 0)
        (self.base / 'baseline.log').write_text(baseline.stderr)
        (self.base / 'test.snapshot').write_bytes(test.read_bytes())
        (folder / 'activation.py').write_text('value = 1\n')
        git('add', 'activation.py'); git('commit', '-qm', 'Repair the value')
        after = git('rev-parse', 'HEAD')
        checked = subprocess.run([sys.executable, '-B', str(test)], capture_output=True, text=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        path, data = self.receipt('real-repair', after)
        data['baseline'] = dict(commit=before, command=command, exit=baseline.returncode,
            log='baseline.log', regression_files={'test_activation.py':'test.snapshot'})
        path.write_text(json.dumps(data))
        with mock.patch('haivequeen.package_diagnosis.checkout', return_value=folder):
            status, output = self.result('real-repair', 'progress', path, after, real_evidence=True)
        self.assertEqual(status, 0, output)
        self.assertEqual(json.loads(output)['nextAction'], 'wait_for_diagnosis')
        state = diagnosis._load(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(state['progress']['real-repair']['implementation']['changed'], ['activation.py'])
        self.assertFalse(bees.package_done(self.conn, 'testproj', REL, 'P1'))
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        scope, follow = self.report(after, 'Repair next behavior')
        self.accept(scope, follow)
        self.assertTrue(self.start('next-repair', after))

    def test_characterization_cannot_be_delegated_as_initial_repair(self):
        scope, data = self.report()
        data['contract'].pop('repair')
        with self.assertRaisesRegex(ValueError, 'perform investigations in diagnosis'):
            self.accept(scope, data)
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
