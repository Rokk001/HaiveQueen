import copy
import json
from unittest import mock
from bzztest import REL, RoundCase
import test_repair_scope as fixtures
from haivequeen import bees, package_diagnosis as diagnosis, repair_contract, runtime, hives


class WorkflowPreflight(RoundCase):
    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id':'P1','class':'medium'}, {'id':'P2','class':'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed',
                            'old-attempt', ['R1'], 'Admission remains unproven', force_failure=True)
        (self.repo / 'workflow.py').write_text('# existing workflow fixture\n')

    report = fixtures.RepairScope.report
    review = fixtures.RepairScope.review
    accept = fixtures.RepairScope.accept
    start = fixtures.RepairScope.start

    def test_missing_runner_correction_review_and_dispatch_then_removed_runner(self):
        scope, report = self.report()
        command = 'python "tests-py/flow with spaces.py" --check compatible-switch-and-adoption'
        report['contract']['workflow']['command'] = command
        with mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            with self.assertRaisesRegex(ValueError, 'Workflow prerequisite missing'):
                self.review(report)
            launch.assert_not_called()
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
            runner = self.repo / 'tests-py/flow with spaces.py'
            runner.parent.mkdir(exist_ok=True)
            runner.write_text('raise AssertionError("product behavior not yet implemented")\n')
            self.assertEqual(self.review(report)[0], 'pending')
            self.agent('scope-check', phase='verify', status='done')
            output = self.env().run_dir('scope-check'); output.mkdir(parents=True, exist_ok=True)
            (output / 'final.txt').write_text('READY-CHECK: PASSED')
            self.assertEqual(self.review(report)[0], 'passed')
            path = self.base / 'checked-diagnosis.json'; path.write_text(json.dumps(report))
            diagnosis.accept(self.conn, 'testproj', REL, 'P1', path, scope, 'medium', ['medium'],
                             workflow_inputs=repair_contract.workflow_inputs(self.repo, report['contract']['workflow']))
            # The actual assigned checkout lost the runner after review.
            self.assertFalse(self.start('must-not-launch'))
            self.assertIn('Workflow prerequisite missing', bees.LAST_REFUSAL)
            self.assertIsNotNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
            self.assertFalse(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1'))
            assigned = self.base / 'assigned-agent-worktree/tests-py/flow with spaces.py'
            assigned.parent.mkdir(exist_ok=True); assigned.write_text('# changed input\n')
            self.assertFalse(self.start('changed-runner'))
            self.assertIn('changed after authorization', bees.LAST_REFUSAL)
            assigned.write_bytes(runner.read_bytes())
            self.assertTrue(self.start('repair'))
            self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))

    def test_cached_pass_cannot_hide_missing_or_changed_runner_after_restart(self):
        _, report = self.report()
        with mock.patch('haivequeen.readygate._launch_check_agent', return_value=('scope-check', '')) as launch:
            self.review(report)
            self.agent('scope-check', phase='verify', status='done')
            output = self.env().run_dir('scope-check'); output.mkdir(parents=True, exist_ok=True)
            (output / 'final.txt').write_text('READY-CHECK: PASSED')
            self.assertEqual(self.review(report)[0], 'passed')
            self.conn.commit()
            runner = self.repo / 'workflow.py'; original = runner.read_bytes(); runner.unlink()
            with self.assertRaisesRegex(ValueError, 'Workflow prerequisite missing'):
                self.review(copy.deepcopy(report))
            self.assertEqual(launch.call_count, 1)
            runner.write_bytes(original + b'# changed runner\n')
            self.assertEqual(self.review(report)[0], 'pending')
            self.assertEqual(launch.call_count, 2)

    def test_local_script_forms_and_wrong_directory_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checkout is unavailable'):
            repair_contract.workflow_inputs(None, {'command':'python workflow.py'})
        for command in ('python absent.py', 'pwsh -File tests/absent.ps1', 'node tests/absent.mjs',
                        'bash tests/absent.sh', 'python ../outside.py', 'cd other && python workflow.py'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                repair_contract.workflow_inputs(self.repo, {'command': command})
        self.assertIn('workflow.py', repair_contract.workflow_inputs(self.repo, {'command':'python workflow.py'}))
