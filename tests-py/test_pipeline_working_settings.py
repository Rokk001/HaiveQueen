from types import SimpleNamespace
import unittest

from haivequeen import orchestrator


class LocalWorkingSettingsTest(unittest.TestCase):
    def test_disabled_local_command_is_not_run(self):
        env = SimpleNamespace(settings={"wayOfWorking": "local", "checkMode": "local",
                                        "checkEnabled": False, "compileCheck": "old command"})
        self.assertNotIn("Check", orchestrator.chain(env))
        self.assertEqual(orchestrator.commands(env)["Check"], "")

    def test_legacy_command_remains_enabled(self):
        env = SimpleNamespace(settings={"compileCheck": "check"})
        self.assertIn("Check", orchestrator.chain(env))
        self.assertEqual(orchestrator.commands(env)["Check"], "check")
