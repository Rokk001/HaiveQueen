from types import SimpleNamespace
import unittest
from unittest import mock

from haivequeen import orchestrator


class LocalWorkingSettingsTest(unittest.TestCase):
    def test_provider_work_waits_before_local_integration(self):
        env = SimpleNamespace(name="test", settings={"wayOfWorking": "git", "mergeMode": "git"})
        state = {"placed": False, "step": "Merge", "steps": {}, "ran": []}
        with mock.patch.object(orchestrator, "_worktree", return_value="worktree"), \
             mock.patch.object(orchestrator, "_blocker") as blocker, \
             mock.patch.object(orchestrator, "_set_status") as status, \
             mock.patch.object(orchestrator.hives, "take_integration") as take:
            orchestrator._advance_pipeline(None, env, "plan", state, 4096)
        take.assert_not_called()
        status.assert_called_with(None, env, "plan", "WAITING")
        self.assertIn("provider", blocker.call_args.args[3].lower())

    def test_disabled_local_command_is_not_run(self):
        env = SimpleNamespace(settings={"wayOfWorking": "local", "checkMode": "local",
                                        "checkEnabled": False, "compileCheck": "old command"})
        self.assertNotIn("Check", orchestrator.chain(env))
        self.assertEqual(orchestrator.commands(env)["Check"], "")

    def test_legacy_command_remains_enabled(self):
        env = SimpleNamespace(settings={"compileCheck": "check"})
        self.assertIn("Check", orchestrator.chain(env))
        self.assertEqual(orchestrator.commands(env)["Check"], "check")
