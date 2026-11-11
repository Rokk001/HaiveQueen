import unittest
from types import SimpleNamespace
from unittest.mock import patch
from haivequeen import hives

class OwnerCheckSettingsTests(unittest.TestCase):
    def test_default_text_is_question(self):
        env = SimpleNamespace(name='p', settings={'ownerCheckText': 'ignored', 'language': 'English'})
        with patch.object(hives.runtime, 'list_agents', return_value=[]), patch.object(hives.db, 'add_question', return_value=7) as add, patch.object(hives, 'wait_for_owner_check'):
            hives.file_owner_check(None, env, 'plan.md')
        self.assertEqual(add.call_args.args[4], 'Check the integrated result')

class IntegrationCommandDefaultsTests(unittest.TestCase):
    def test_no_switches_and_no_commands_by_default(self):
        from haivequeen import scheduler
        settings = scheduler.build_settings({}, {}, legacy_deploy=True)
        for key in ('compileCheckEnabled', 'buildEnabled', 'deployEnabled', 'buildFlowDoc', 'integratorInstructions'):
            self.assertNotIn(key, settings)
        self.assertEqual((settings['compileCheck'], settings['buildCommand'], settings['deployCommand']), ('', '', ''))
        self.assertTrue(settings['deleteFinishedWorktrees'])
        self.assertFalse(settings['deleteIntegratedBranches'])

    def test_new_apiary_has_no_commands(self):
        import tempfile
        from haivequeen import init, config, scheduler
        with tempfile.TemporaryDirectory() as folder:
            init.init_project(folder)
            settings = scheduler.build_settings({}, config.load_project(folder))
        self.assertNotIn('buildEnabled', settings)
        self.assertEqual((settings['buildCommand'], settings['deployCommand']), ('', ''))

class CompileCommandTests(unittest.TestCase):
    def test_configured_command_is_recognized_in_command_position(self):
        from haivequeen.agent_runner import classify_cmd
        self.assertEqual(classify_cmd('cd src && npm test', compile_check='npm test')['label'], 'Compile test')
        self.assertIsNone(classify_cmd('echo npm test', compile_check='npm test'))

    def test_old_filename_alone_is_not_compile_test(self):
        from haivequeen.agent_runner import classify_cmd
        result = classify_cmd('powershell -File compile-check-changed.ps1', compile_check='npm test')
        self.assertNotEqual(result['label'], 'Compile test')
