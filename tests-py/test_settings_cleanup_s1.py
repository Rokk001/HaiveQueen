"""Regression tests for settings cleanup."""
import tkinter.font
import unittest
import tomllib
from unittest import mock

from haivequeen import agent_runner, scheduler

from test_k5_apiary_page import ApiaryPageCase, descendants, esc


class SettingsCleanupTests(ApiaryPageCase):
    def test_apiary_legacy_round_minutes_is_hidden_and_preserved(self):
        path = self.plain / ".haivequeen" / "project.toml"
        path.write_text(self.plain_text + 'roundMinutes = 17\n', encoding="utf-8")
        win = self.window("Plain")
        self.assertNotIn(("file", "roundMinutes"), self.shown(win))
        self.assertTrue(win.collect())
        win.save()
        self.assertIn('roundMinutes = 17', path.read_text(encoding="utf-8"))

    def test_apiary_page_has_no_overrides_or_tool_fields(self):
        win = self.window("Plain")
        values = self.shown(win)
        for key in ("escalateAfter", "language", "claudeTools", "allowedTools"):
            self.assertNotIn(("file", key), values)   # General and the providers decide these now

    def test_failed_project_write_keeps_provider_grant(self):
        config = self.base / "config.toml"
        grant = "Bash(git push origin integration/one)"
        config.write_text(config.read_text(encoding="utf-8").replace("Read,Edit", "Read," + grant), encoding="utf-8")
        project = self.plain / ".haivequeen" / "project.toml"
        project.write_text(self.plain_text + 'integrationBranch = "integration/one"\n', encoding="utf-8")
        win = self.window("Plain")
        next(x for x in win.apiaries if x["name"] == "Plain")["file"]["colour"] = "red"   # a changed project file is written
        with mock.patch("haivequeen.gui_config.save_project_file", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                win.save()
        self.assertIn(grant, config.read_text(encoding="utf-8"))

    def test_branch_grants_stay_at_the_provider_on_save(self):
        config = self.base / "config.toml"
        grants = "Bash(git push origin integration/one),Bash(git push origin main),Bash(git push origin integration/one main)"
        config.write_text(config.read_text(encoding="utf-8").replace("Read,Edit", "Read," + grants), encoding="utf-8")
        project = self.plain / ".haivequeen" / "project.toml"
        project.write_text(self.plain_text + 'integrationBranch = "integration/one"' + chr(10), encoding="utf-8")
        win = self.window("Plain")
        win.save()
        self.assertIn(grants, config.read_text(encoding="utf-8"))
        self.assertNotIn("allowedTools", tomllib.loads(project.read_text(encoding="utf-8")))

    def test_empty_overrides_inherit_general(self):
        settings = scheduler.build_settings({"language": "German", "escalateAfter": 7}, {"language": "", "escalateAfter": ""})
        self.assertEqual(settings["language"], "German")
        self.assertEqual(settings["escalateAfter"], 7)

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


class CompletenessTests(ApiaryPageCase):
    def test_general_labels_fit_at_all_gui_test_sizes_and_languages(self):
        from haivequeen import gui_config, i18n
        for german in (False, True):
            with mock.patch.object(i18n, 'is_german', return_value=german):
                win = self.window()
                for geometry in ('1100x700', '1900x1000'):
                    win.win.geometry(geometry)
                    win.win.update()
                    expected = [i18n.tr(label) for _, label, _ in gui_config.BUILD_FIELDS]
                    expected += [i18n.tr(label) for label in gui_config.GENERAL_LABELS.values()]
                    labels = [w for w in descendants(win.form) if w.winfo_class() == 'Label']
                    for text in expected:
                        matches = [w for w in labels if w.cget('text') == text]
                        self.assertEqual(len(matches), 1, (geometry, text))
                        label = matches[0]
                        self.assertGreaterEqual(label.winfo_width(), max(tkinter.font.Font(font=label.cget('font')).measure(word) for word in text.split()), (geometry, text))
                        self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight(), (geometry, text))
                win.win.destroy()

    def test_classification_and_localized_explanations(self):
        from haivequeen import gui_config, i18n
        project = self.plain / '.haivequeen' / 'project.toml'
        project.write_text(self.plain_text + 'owner = "Owner"\nplanSuffixRegex = "PLAN"\n[views]\nqueue = "queue.md"\n', encoding='utf-8')
        config = self.base / 'config.toml'
        config.write_text(config.read_text(encoding='utf-8').replace('slots = 2', 'slots = 2\nagentStallSeconds = 60'), encoding='utf-8')
        for german in (False, True):
            with mock.patch.object(i18n, 'is_german', return_value=german):
                for apiary in (None, 'Plain'):
                    win = self.window(apiary)
                    win.win.update()
                    self.assertFalse(win.advanced_box.winfo_ismapped())
                    for path, _, _ in win.getters:
                        in_advanced = str(win.rows[path]).startswith(str(win.advanced_box))
                        if apiary and path in (('file', 'rulesOff'), ('file', 'upstreamRules')):
                            continue   # the Knowledge base has its own lines (tests of P2)
                        expected_main = path in (('entry', 'name'), ('place', 'repository'), ('file', 'integrationBranch'), ('file', 'compileCheck')) if apiary else path[-1] in gui_config.GENERAL_LABELS
                        self.assertEqual(not in_advanced, expected_main, path)
                        if expected_main:
                            label = win.text_of(path)
                            help_map = gui_config.SETTING_HELP if apiary else gui_config.GENERAL_HELP
                            help_text = help_map[path[-1]]
                            self.assertTrue(label)
                            rendered = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
                            self.assertIn(label, rendered)
                            self.assertIn(i18n.tr(help_text), rendered)
                            if german:
                                source_label = (gui_config.SETTING_LABELS if apiary else gui_config.GENERAL_LABELS)[path[-1]]
                                self.assertIn(source_label, i18n.GERMAN)
                                self.assertNotEqual(i18n.tr(help_text), help_text)
                    new_rows = [w for w in descendants(win.form) if w.winfo_class() == 'Label' and w.cget('text') == i18n.tr('new setting')]
                    if apiary:
                        self.assertFalse(new_rows)   # P-8: no free setting rows on a Project page
                    else:
                        self.assertTrue(new_rows)
                        self.assertTrue(all(str(w).startswith(str(win.advanced_box)) for w in new_rows))
                    win.win.destroy()

    def test_config_project_and_alias_round_trip(self):
        from haivequeen import gui_config
        config = self.base / 'config.toml'
        config.write_text(config.read_text(encoding='utf-8').replace('slots = 2', 'slots = 2\nroundMinutes = 8\nagentStallSeconds = 45\n[buildJobs]\ncores = 4\nparallel = 2\nminFreeRamGB = 3'), encoding='utf-8')
        for alias in ('repo', 'codeRepo', 'both'):
            with self.subTest(alias=alias):
                project = self.plain / '.haivequeen' / 'project.toml'
                paths = {'repo': esc(self.code), 'codeRepo': esc(self.code), 'both': esc(self.code)}
                extra = ('repo = "%s"\n' % (esc(self.plans) if alias == 'both' else paths[alias]) if alias != 'codeRepo' else '')
                if alias != 'repo':
                    extra += 'codeRepo = "%s"\n' % esc(self.code)
                project.write_text(self.plain_text + extra + 'roundMinutes = 17\nclaudeTools = "Read,Edit"\nallowedTools = ["Bash(test)"]\nplanGlobs = ["plans/*.md"]\n[views]\nqueue = "queue.md"\n', encoding='utf-8')
                before_config = gui_config.load_raw(config)
                before_project = gui_config.load_raw(project)
                win = self.window('Plain')
                self.assertEqual(self.shown(win)[('place', 'repository')], str(self.code))
                win.save()
                self.assertEqual(gui_config.load_raw(config), before_config)
                self.assertEqual(gui_config.load_raw(project), before_project)
                win.win.destroy()



if __name__ == "__main__":
    unittest.main()
