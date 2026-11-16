"""Workflow trigger and exact-commit reuse regression tests; no live GitHub calls."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class TestWorkflow(unittest.TestCase):
    def test_branch_pushes_exclude_tags_and_pr_jobs_are_fork_only(self):
        source = (ROOT / '.github/workflows/test.yml').read_text(encoding='utf-8')
        self.assertIn("branches: ['**']", source)
        self.assertIn('github.event.pull_request.head.repo.full_name != github.repository', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn('github.sha', source)
        self.assertIn('needs: reuse', source)
        self.assertIn("needs.reuse.outputs.run == 'true'", source)
        for unchanged in ('windows-latest, ubuntu-latest', '"3.11", "3.14"', '-m unittest discover -s tests-py -v'):
            self.assertIn(unchanged, source)

    def module(self):
        spec = importlib.util.spec_from_file_location('reuse_tests', ROOT / '.github/scripts/reuse_tests.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_only_successful_same_commit_push_is_reused(self):
        mod = self.module()
        calls = []
        def request(path):
            calls.append(path)
            return {'workflow_runs': [
                {'id': 1, 'head_sha': 'other', 'event': 'push', 'status': 'completed', 'conclusion': 'success'},
                {'id': 2, 'head_sha': 'abc', 'event': 'pull_request', 'status': 'completed', 'conclusion': 'success'},
                {'id': 3, 'head_sha': 'abc', 'event': 'push', 'status': 'completed', 'conclusion': 'failure'},
                {'id': 4, 'head_sha': 'abc', 'event': 'push', 'status': 'in_progress', 'conclusion': None},
                {'id': 5, 'head_sha': 'abc', 'event': 'push', 'status': 'completed', 'conclusion': 'success'}]}
        self.assertEqual(mod.previous_success(request, 'abc', 6), 5)
        self.assertIn('head_sha=abc', calls[0])
        self.assertIsNone(mod.previous_success(request, 'new', 6))
        self.assertIsNone(mod.previous_success(request, 'abc', 5))

    def test_pagination_and_no_false_success_for_missing_results(self):
        mod = self.module()
        pages = [{'workflow_runs': [{'id': 1, 'head_sha': 'abc', 'event': 'push', 'status': 'completed', 'conclusion': 'cancelled'}] * 100},
                 {'workflow_runs': [{'id': 2, 'head_sha': 'abc', 'event': 'push', 'status': 'completed', 'conclusion': 'success'}]}]
        self.assertEqual(mod.previous_success(lambda path: pages.pop(0), 'abc', 10), 2)
        self.assertIsNone(mod.previous_success(lambda path: {'workflow_runs': []}, 'abc', 10))

    def test_explicit_reruns_and_fork_prs_always_run(self):
        mod = self.module()
        def forbidden(path):
            self.fail('Explicit verification must not be deduplicated')
        base = {'GITHUB_SHA': 'abc', 'GITHUB_RUN_ID': '10', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_EVENT_NAME': 'push'}
        for extra in ({'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_EVENT_NAME': 'workflow_dispatch'}, {'GITHUB_EVENT_NAME': 'pull_request'}):
            self.assertIsNone(mod.reused_run({**base, **extra}, forbidden))

    def test_outputs_reuse_and_api_failure_never_reports_success(self):
        mod = self.module()
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / 'output'
            summary = Path(tmp) / 'summary'
            env = {'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary),
                   'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_REPOSITORY': 'example/project'}
            with patch.dict(mod.os.environ, env, clear=True), patch.object(mod, 'reused_run', return_value=7):
                mod.main()
            self.assertEqual(output.read_text(encoding='utf-8'), 'run=false\n')
            self.assertIn('/actions/runs/7', summary.read_text(encoding='utf-8'))
            output.unlink()
            with patch.dict(mod.os.environ, env, clear=True), patch.object(mod, 'reused_run', side_effect=OSError('API unavailable')):
                with self.assertRaises(OSError):
                    mod.main()
            self.assertFalse(output.exists())
