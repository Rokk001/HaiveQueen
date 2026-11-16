"""Recover a committed merge after losing the original attempt completion."""
import json
import subprocess
import time
from unittest import mock, TestCase

from bzztest import REL
from haivequeen import integration, orchestrator
from test_o1_orchestrator import OrchCase


class RepairScopeInstructions(TestCase):
    def test_project_fix_and_diagnosis_keep_maintenance_outside_assignment(self):
        from haivequeen import roles
        fix = roles.role_text('fix', {'plan': 'sample', 'worktree': 'project-worktree', 'task': 'Resolve failed check'})
        diagnosis = roles.role_text('diagnosis', {})
        self.assertIn('A diagnostic proposal is evidence, not permission', fix)
        self.assertIn('assigned project and worktree', fix)
        self.assertIn('Report it as external', diagnosis)
        self.assertIn("not a technical instruction to that project's Fix Agent", diagnosis)


class RecoveredRepair(OrchCase):
    """Use real conflicting Git ancestry and the durable verification supervisor."""

    def setUp(self):
        """Lose completion, then start a replacement at the completed repair."""
        super().setUp()
        self.commit(self.repo, 'comment.txt', 'base\n')
        self.git('push', '-q', 'origin', 'main')
        self.git('merge', '--ff-only', 'main', cwd=self.pwt)
        self.ready_plan()
        self.before = self.commit(self.pwt, 'comment.txt', 'local\n')
        self.remote_commit('comment.txt', 'upstream\n')
        self.round()
        state = self.state()
        original = self.fixers()[0]
        self.failure_log = state['fix']['log_file']
        self.original = dict(state['fix']['original_attempt'])
        self.assertEqual(self.original['commit'], self.before)
        self.commit(self.pwt, 'comment.txt', 'local and upstream\n')
        self.head = self.git('rev-parse', 'HEAD', cwd=self.pwt)
        self.assertEqual(self.git('rev-parse', 'HEAD^1', cwd=self.pwt), self.before)
        self.assertEqual(self.git('rev-parse', 'HEAD^2', cwd=self.pwt), self.remote_head())
        self.end_fixer(original, verification=False)
        # Simulate restart of a legacy state with no pinned baseline. History
        # still has attempt-started, but never got original attempt-ended.
        state['fix'].pop('original_attempt')
        state['fix']['agent'] = None
        orchestrator._start_fix(self.conn, self.env(), REL, state, 100000, lambda pid: False)
        self.agent = self.fixers()[-1]
        self.assertNotEqual(self.agent['name'], original['name'])
        self.assertEqual(state['fix']['head'], self.head)
        self.assertEqual(state['fix']['original_attempt'], self.original)
        self.end_fixer(self.agent, verification_command='git diff --check HEAD^ HEAD')
        self.state_value = self.state()

    def advance(self):
        """Run the repair gate without running the integration pipeline."""
        return orchestrator._advance_fix(self.conn, self.env(), REL,
                                        self.state_value, 100000, lambda pid: False)

    def rejected(self):
        """Assert no targeted job or integration transition is allowed."""
        with mock.patch.object(orchestrator, '_verify_repair') as verify:
            self.advance()
        verify.assert_not_called()
        self.assertEqual(self.state_value['phase'], 'fix')

    def evidence(self, **changes):
        """Change only the submitted manifest fields for a rejection case."""
        path = self.env().run_dir(self.agent['name']) / 'repair-verification.json'
        evidence = json.loads(path.read_text(encoding='utf-8'))
        evidence.update(changes)
        path.write_text(json.dumps(evidence), encoding='utf-8')
        return evidence

    def test_recovered_merge_runs_exactly_one_durable_check(self):
        """Missing completion and same-HEAD replacement still require fresh verification."""
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        self.assertFalse(any(e['event'] in ('attempt-ended', 'targeted-verification') for e in history))
        orchestrator.ASYNC = True
        with mock.patch.object(integration, 'start_compile_check', wraps=integration.start_compile_check) as start:
            with mock.patch.object(orchestrator, '_harvest', return_value=None):
                self.advance()
                self.advance()
            self.assertEqual(start.call_count, 1)
        self.assertEqual(self.state_value['job']['step'], 'RepairCheck')
        orchestrator.ASYNC = False
        job = self.state_value['job']
        # The supervisor itself owns the bounded timeout; harvest its durable
        # result through the existing recovery path, with no pipeline run.
        with mock.patch.object(orchestrator, '_async', return_value=False):
            deadline = time.monotonic() + 30
            while integration.poll_compile_check(self.conn, 'testproj', job) is None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            self.advance()
        self.assertEqual(self.state_value['phase'], 'pipeline')
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        results = [e for e in history if e['event'] == 'targeted-verification']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['exit_code'], 0)
        self.assertEqual(results[0]['commit'], self.head)
        self.assertEqual(results[0]['failure_log'], self.failure_log)

    def test_wrong_commit_is_rejected(self):
        """A manifest must name the exact current commit."""
        self.evidence(commit=self.before)
        self.rejected()

    def test_missing_replacement_manifest_reuses_bound_prior_record_but_runs_fresh_check(self):
        current = self.env().run_dir(self.agent['name']) / 'repair-verification.json'
        original = self.env().run_dir(self.original['agent']) / 'repair-verification.json'
        original.write_bytes(current.read_bytes())
        current.unlink()
        with mock.patch.object(orchestrator, '_verify_repair') as verify:
            self.advance()
        verify.assert_called_once()
        evidence = self.state_value['fix']['verification']
        self.assertEqual(evidence['source_agent'], self.original['agent'])
        self.assertEqual(evidence['commit'], self.head)
        self.assertEqual(self.state_value['phase'], 'fix')

    def test_resume_without_assigned_agent_consumes_retained_result_without_new_agent(self):
        self.state_value['fix']['agent'] = None
        with mock.patch.object(orchestrator, '_verify_repair') as verify, \
             mock.patch.object(orchestrator, '_start_fix') as start:
            self.advance()
        start.assert_not_called()
        verify.assert_called_once()
        self.assertEqual(self.state_value['fix']['agent'], self.agent['name'])
        self.assertEqual(self.state_value['fix']['verification']['commit'], self.head)

    def test_resume_never_launches_or_verifies_alongside_a_live_repair_agent(self):
        self.state_value['fix']['agent'] = None
        with mock.patch.object(orchestrator.agent_process, 'counts', return_value=True), \
             mock.patch.object(orchestrator, '_verify_repair') as verify, \
             mock.patch.object(orchestrator, '_start_fix') as start:
            self.advance()
        start.assert_not_called()
        verify.assert_not_called()

    def test_unrelated_failure_is_rejected(self):
        """An unrelated attempt history cannot establish earlier progress."""
        self.state_value['fix']['log_file'] = 'unrelated-failure'
        self.evidence(failure_log='unrelated-failure')
        self.rejected()

    def test_unchanged_unrepaired_head_is_rejected(self):
        """An unchanged baseline has no recoverable progress."""
        self.git('checkout', '--detach', self.before, cwd=self.pwt)
        self.state_value['fix']['head'] = self.before
        self.evidence(commit=self.before)
        self.rejected()

    def test_empty_commit_is_rejected(self):
        """An ordinary empty commit cannot stand in for a completed repair."""
        self.git('checkout', '--detach', self.before, cwd=self.pwt)
        self.git('commit', '--allow-empty', '-qm', 'Empty attempt', cwd=self.pwt)
        head = self.git('rev-parse', 'HEAD', cwd=self.pwt)
        self.state_value['fix']['head'] = head
        self.evidence(commit=head)
        self.rejected()

    def test_dirty_tree_is_rejected(self):
        """Uncommitted changes cannot be verified as the committed repair."""
        (self.pwt / 'comment.txt').write_text('dirty\n', encoding='utf-8')
        self.rejected()

    def test_unmerged_tree_is_rejected(self):
        """A new unresolved conflict cannot inherit earlier merge progress."""
        self.git('checkout', '-b', 'new-conflict', self.before, cwd=self.pwt)
        other = self.commit(self.pwt, 'comment.txt', 'conflicting\n')
        self.git('checkout', 'plan-br', cwd=self.pwt)
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('merge', other, cwd=self.pwt)
        self.assertTrue(self.git('ls-files', '-u', cwd=self.pwt))
        self.rejected()

    def test_failed_identical_command_is_rejected(self):
        """Recovery preserves the same-command failed-verification guard."""
        evidence = self.evidence()
        orchestrator._record_repair(self.conn, 'testproj', REL, 'targeted-verification',
            commit=self.head, failure_log=self.failure_log, command=evidence['command'], exit_code=9)
        self.rejected()

    def test_failed_verification_cannot_advance_integration(self):
        """Recovered progress requires a successful durable result."""
        self.evidence(command='echo regression-failed && exit 9')
        self.advance()
        self.assertEqual(self.state_value['phase'], 'fix')
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        results = [e for e in history if e['event'] == 'targeted-verification']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['exit_code'], 9)
        self.assertFalse(any(e['event'] == 'pipeline-retry' for e in history))
