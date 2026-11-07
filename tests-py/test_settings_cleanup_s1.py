"""Regression tests for settings cleanup S1."""
import tkinter.font
import unittest
import tomllib
from unittest import mock

from haivequeen import agent_runner, scheduler

from test_k5_apiary_page import ApiaryPageCase, descendants


class SettingsCleanupTests(ApiaryPageCase):
    def test_apiary_legacy_round_minutes_is_hidden_and_preserved(self):
        path = self.plain / ".haivequeen" / "project.toml"
        path.write_text(self.plain_text + 'roundMinutes = 17\n', encoding="utf-8")
        win = self.window("Plain")
        self.assertNotIn(("file", "roundMinutes"), self.shown(win))
        self.assertTrue(win.collect())
        win.save()
        self.assertIn('roundMinutes = 17', path.read_text(encoding="utf-8"))

    def test_apiary_offers_empty_overrides_and_distinct_tool_fields(self):
        win = self.window("Plain")
        values = self.shown(win)
        self.assertEqual(values[("file", "escalateAfter")], "")
        self.assertEqual(values[("file", "language")], "")
        self.assertIn(("file", "claudeTools"), values)
        self.assertIn(("file", "allowedTools"), values)
        self.assertIn("General", win.text_of(("file", "escalateAfter")))
        self.assertIn("General", win.text_of(("file", "language")))

    def test_unique_branch_grant_moves_on_save_and_runtime_combines(self):
        config = self.base / "config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace("Read,Edit", "Read,Bash(git push origin integration/one),Bash(git push origin other)"), encoding="utf-8")
        project = self.plain / ".haivequeen" / "project.toml"
        project.write_text(self.plain_text + 'integrationBranch = "integration/one"\n', encoding="utf-8")
        win = self.window("Plain")
        grant = "Bash(git push origin integration/one)"
        self.assertIn(grant, self.shown(win)[("file", "allowedTools")])
        win.save()
        saved = tomllib.loads(project.read_text(encoding="utf-8"))
        provider = tomllib.loads(config.read_text(encoding="utf-8"))["providers"][0]
        self.assertIn(grant, saved["allowedTools"])
        self.assertNotIn(grant, str(provider["args"]))
        self.assertIn("Bash(git push origin other)", str(provider["args"]))
        args = agent_runner.build_args(provider, {"effort": "medium"}, saved["allowedTools"])
        self.assertIn(grant, str(args))
        self.assertIn("Bash(git push origin other)", str(args))

    def test_failed_project_write_keeps_provider_grant(self):
        config = self.base / "config.toml"
        grant = "Bash(git push origin integration/one)"
        config.write_text(config.read_text(encoding="utf-8").replace("Read,Edit", "Read," + grant), encoding="utf-8")
        project = self.plain / ".haivequeen" / "project.toml"
        project.write_text(self.plain_text + 'integrationBranch = "integration/one"\n', encoding="utf-8")
        win = self.window("Plain")
        with mock.patch("haivequeen.gui_config.save_project_file", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                win.save()
        self.assertIn(grant, config.read_text(encoding="utf-8"))

    def test_shared_branch_grant_stays_at_provider(self):
        config = self.base / "config.toml"
        config.write_text(config.read_text(encoding="utf-8").replace("Read,Edit", "Read,Bash(git push origin main)"), encoding="utf-8")
        win = self.window("Plain")
        self.assertNotIn("Bash(git push origin main)", self.shown(win)[("file", "allowedTools")])
        win.save()
        self.assertIn("Bash(git push origin main)", config.read_text(encoding="utf-8"))

    def test_empty_overrides_inherit_general(self):
        settings = scheduler.build_settings({"language": "German", "escalateAfter": 7}, {"language": "", "escalateAfter": ""})
        self.assertEqual(settings["language"], "German")
        self.assertEqual(settings["escalateAfter"], 7)

    def test_clearing_overrides_restores_inheritance(self):
        project = self.plain / ".haivequeen" / "project.toml"
        project.write_text(self.plain_text + 'language = "German"\nescalateAfter = 4\n', encoding="utf-8")
        win = self.window("Plain")
        for key in ("language", "escalateAfter"):
            entry = self.entry_of(win, ("file", key))
            entry.delete(0, "end")
        win.save()
        saved = tomllib.loads(project.read_text(encoding="utf-8"))
        self.assertNotIn("language", saved)
        self.assertNotIn("escalateAfter", saved)

    def test_general_advanced_is_collapsed_and_build_labels_fit(self):
        win = self.window()
        win.win.geometry("1100x700")
        win.win.update()
        self.assertFalse(win.advanced_box.winfo_ismapped())
        labels = [w for w in descendants(win.form) if w.winfo_class() == "Label"
                  and w.cget("text") == "Minimum free RAM before a build"]
        self.assertEqual(len(labels), 1)
        label = labels[0]
        self.assertGreaterEqual(label.winfo_width(), tkinter.font.Font(font=label.cget("font")).measure(label.cget("text")))


if __name__ == "__main__":
    unittest.main()
