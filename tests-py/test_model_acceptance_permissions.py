import importlib.util
import unittest
from unittest import mock
from pathlib import Path
spec=importlib.util.spec_from_file_location('acceptance',Path(__file__).resolve().parents[1]/'scripts/verify_model_repair.py')
acceptance=importlib.util.module_from_spec(spec); spec.loader.exec_module(acceptance)
class AcceptancePermissionTests(unittest.TestCase):
    def test_windows_enables_sandbox_when_user_config_is_ignored(self):
        command=acceptance.model_command('codex','model','medium',Path.cwd(),windows=True)
        self.assertIn('windows.sandbox="elevated"',command)
        self.assertIn('--approve-for-me',command)
        self.assertNotIn('--sandbox',command, 'CLI rejects explicit sandbox with approve-for-me')
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
    def test_git_trust_is_scoped_to_fixture_repositories(self):
        root=Path.cwd()
        env=acceptance.model_environment({'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'color.ui','GIT_CONFIG_VALUE_0':'false'},[root/'repo',root/'worktree'])
        self.assertEqual(env['GIT_CONFIG_COUNT'],'3')
        self.assertEqual(env['GIT_CONFIG_KEY_0'],'color.ui')
        self.assertEqual(env['GIT_CONFIG_KEY_1'],'safe.directory')
        self.assertEqual(env['GIT_CONFIG_VALUE_1'],str((root/'repo').resolve()))
        self.assertEqual(env['GIT_CONFIG_VALUE_2'],str((root/'worktree').resolve()))
        self.assertNotIn('*',env.values())

    def test_merge_prompt_does_not_disclose_or_request_future_build_artifact(self):
        merge=acceptance.repair_prompt('Merge','verify merge',[])
        build=acceptance.repair_prompt('Build','verify build',[])
        self.assertNotIn('index.txt',merge)
        self.assertIn('index.txt',build)
        self.assertIn('explicit file paths',build)
        self.assertIn('verify merge',merge)
        self.assertIn('preempt later steps',merge)

    def test_fixture_preserves_runner_access_for_sandbox_created_files(self):
        folder=Path('isolated-fixture')
        with mock.patch.object(acceptance.subprocess,'check_output',return_value='"test\\runner","S-1-5-21-123"\n'), mock.patch.object(acceptance.subprocess,'run') as run:
            acceptance.grant_fixture_access(folder)
        self.assertEqual(run.call_args.args[0],['icacls',str(folder),'/grant','*S-1-5-21-123:(OI)(CI)F','/T','/Q'])
        self.assertTrue(run.call_args.kwargs['check'])
