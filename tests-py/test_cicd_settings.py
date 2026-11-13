import tempfile
import unittest
from pathlib import Path

from haivequeen import config, settings, projsettings


class WorkingSettingsTest(unittest.TestCase):
    def test_legacy_migration_and_round_trip(self):
        old = {"name": "Example", "compileCheck": "check", "buildCommand": "",
               "deployCommand": "deploy", "publishReleases": True}
        new, removed = projsettings.migrate_data(old, ".", {})
        self.assertEqual([new[s + "Mode"] for s in projsettings.STEPS], ["local"] * 5)
        self.assertEqual([new[s + "Enabled"] for s in projsettings.STEP_FIELDS], [True, False, True, True])
        self.assertEqual(new["releasePostfix"], "dev")
        self.assertNotIn("publishReleases", new)
        self.assertIn("publishReleases", [entry[0] for entry in removed])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.toml"
            settings.save_file(path, new)
            loaded = config._load_toml(path)
            self.assertEqual(loaded, new)
            self.assertEqual(projsettings.migrate_data(loaded, ".", {}), (loaded, []))

    def test_presets_and_validation(self):
        settings = projsettings.select_working_way({}, "git")
        settings.update(checkEnabled=True, releaseEnabled=True, releasePostfix="preview")
        self.assertEqual(projsettings.validate_working_settings(settings), [])
        settings = projsettings.select_step_mode(settings, "check", "local")
        self.assertEqual(settings["wayOfWorking"], "custom")
        self.assertIn("compileCheck is required", projsettings.validate_working_settings(settings))
        settings["compileCheck"] = "check"
        self.assertEqual(projsettings.validate_working_settings(settings), [])
        settings["releasePostfix"] = "bad value"
        self.assertIn("Invalid release postfix", projsettings.validate_working_settings(settings))
        settings = projsettings.select_working_way(settings, "local")
        self.assertEqual([settings[s + "Mode"] for s in projsettings.STEPS], ["local"] * 5)
        self.assertIn("releaseFolder is required", projsettings.validate_working_settings(settings))

    def test_old_disabled_command_is_preserved(self):
        new, _ = projsettings.migrate_data({"name": "Example", "compileCheck": "check", "compileCheckEnabled": False}, ".", {})
        self.assertEqual(new["compileCheck"], "check")
        self.assertFalse(new["checkEnabled"])


if __name__ == "__main__":
    unittest.main()
