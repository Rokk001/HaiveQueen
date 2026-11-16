import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('live_acceptance', Path(__file__).resolve().parents[1] / 'scripts/verify_live_repair.py')
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


class LiveAcceptanceGuards(unittest.TestCase):
    def test_fault_boundary_reads_the_actual_runner_prompt_with_spaces(self):
        spec = importlib.util.spec_from_file_location('provider_fault', Path(acceptance.__file__).with_name('hardening_provider_fault.py'))
        fault = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fault)
        from haivequeen import agent_runner
        path = Path('test fixture/runs/repair/prompt.md')
        for phase in ('queen', 'fix', 'bee'):
            self.assertEqual(fault.prompt_file([agent_runner.report_prompt(path, 'fixture', 1, phase)]), path)
        self.assertIsNone(fault.prompt_file(['exec', 'Health check']))

    def test_requires_existing_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                acceptance.validate_root(Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_rejects_profile_or_data_outside_test_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('config', 'data', 'venv'):
                (root / name).mkdir()
            (root / 'config/config.toml').write_text('')
            (root / 'data/haivequeen.db').touch()
            self.assertEqual(acceptance.validate_root(root), root.resolve())
            with self.assertRaises(ValueError):
                acceptance.require_inside(root, root.parent)

    def test_completion_requires_real_runner_identity_and_terminal_result(self):
        row = {'status': 'done', 'pid': 123, 'processIdentity': 'birth', 'exit_code': 0}
        self.assertTrue(acceptance.finished_runner(row))
        for field in ('pid', 'processIdentity', 'exit_code'):
            changed = dict(row); changed.pop(field)
            self.assertFalse(acceptance.finished_runner(changed))
        self.assertFalse(acceptance.finished_runner(dict(row, status='running')))

    def test_fault_runs_keep_real_failure_exit_without_accepting_live_or_missing_results(self):
        failed = {'status': 'failed', 'pid': 123, 'processIdentity': 'birth', 'exit_code': 1}
        self.assertTrue(acceptance.terminal_runner(failed))
        self.assertFalse(acceptance.finished_runner(failed))
        self.assertFalse(acceptance.terminal_runner(dict(failed, exit_code=None)))
        self.assertFalse(acceptance.terminal_runner(dict(failed, status='running')))

    def test_unknown_quota_never_becomes_a_test_start_permit(self):
        from haivequeen import usage, runtime
        with mock.patch.object(usage, 'read_codex_rate_limits', return_value={}), \
             mock.patch.object(runtime, 'kv_set') as save:
            with self.assertRaisesRegex(RuntimeError, 'unknown'):
                acceptance.refresh_test_quota(None)
        save.assert_not_called()
