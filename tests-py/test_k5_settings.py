import unittest
from types import SimpleNamespace
from unittest.mock import patch
from haivequeen import hives

class OwnerCheckSettingsTests(unittest.TestCase):
    def test_configured_text_is_question(self):
        env = SimpleNamespace(name='p', settings={'ownerCheckText': 'Review the result', 'language': 'English'})
        with patch.object(hives.runtime, 'list_agents', return_value=[]), patch.object(hives.db, 'add_question', return_value=7) as add, patch.object(hives, 'wait_for_owner_check'):
            hives.file_owner_check(None, env, 'plan.md')
        self.assertEqual(add.call_args.args[4], 'Review the result')

class IntegrationSwitchDefaultsTests(unittest.TestCase):
    def test_existing_apiary_keeps_integration_steps(self):
        from haivequeen import scheduler
        settings = scheduler.build_settings({}, {})
        self.assertTrue(settings['compileCheckEnabled'])
        self.assertTrue(settings['buildEnabled'])
        self.assertTrue(settings['deployEnabled'])
        self.assertTrue(settings['deleteFinishedWorktrees'])
        self.assertFalse(settings['deleteIntegratedBranches'])

    def test_new_apiary_disables_deploy(self):
        import tempfile
        from haivequeen import init, config, scheduler
        with tempfile.TemporaryDirectory() as folder:
            init.init_project(folder)
            settings = scheduler.build_settings({}, config.load_project(folder))
        self.assertTrue(settings['compileCheckEnabled'])
        self.assertTrue(settings['buildEnabled'])
        self.assertFalse(settings['deployEnabled'])

class CompileCommandTests(unittest.TestCase):
    def test_configured_command_is_recognized_in_command_position(self):
        from haivequeen.agent_runner import classify_cmd
        self.assertEqual(classify_cmd('cd src && npm test', compile_check='npm test')['label'], 'Compile test')
        self.assertIsNone(classify_cmd('echo npm test', compile_check='npm test'))
