"""Tests of the settings editor: TOML writing, the allowed tools list, checks, saving with a backup, and the window."""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, gui_config, i18n  # noqa: E402

SAMPLE = '''# Machine settings (kept at the top)
slots = 8
exportDir = "%LOCALAPPDATA%\\\\HaiveQueen\\\\exports"
buildJobs = { "envVar" = "BUILD_MP_JOBS", "reserveMB" = 1024, "perJobMB" = "auto" }
hives = 10

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--allowedTools", "Bash(git merge *),Bash(git -C * merge *),Read", "--effort", "{effort}"]
slots = 5
use = "preferred"
limit = { "source" = "claude" }
[providers.classes]
light = { "model" = "claude-haiku-5-5", "effort" = "medium" }
medium = { "model" = "claude-sonnet-5-5", "effort" = "medium" }

[[providers]]
name = "codex"
protocol = "codex"
command = "codex"
args = ["exec", "--json"]
slots = 1
use = "capability-only"
capabilities = ["images"]

[[projects]]
name = "ODP"
workspace = "C:\\\\Users\\\\mario\\\\GitHub\\\\Moreya-Internal"
'''


class DataLayerTest(unittest.TestCase):
    """Reading, splitting, writing and checking settings."""

    def setUp(self):
        """A temp folder with a sample config.toml."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    # The allowed tools are split at commas outside parentheses.
    def test_split_tools_keeps_patterns_with_spaces_and_stars(self):
        """Entries with spaces and stars stay whole; empty entries and blanks go."""
        self.assertEqual(gui_config.split_tools("Bash(git merge *), Bash(git -C * merge *),Read,,"),
                         ["Bash(git merge *)", "Bash(git -C * merge *)", "Read"])
        self.assertEqual(gui_config.split_tools("Bash(a,b),Read"), ["Bash(a,b)", "Read"])
        self.assertEqual(gui_config.split_tools(""), [])

    # The tools come out of the arguments and go back to the same place.
    def test_args_round_trip_keeps_the_position(self):
        """Taking the allowed tools out and putting them back gives the original list; no tools removes the flag."""
        args = gui_config.load_raw(self.path)["providers"][0]["args"]
        rest, tools, pos = gui_config.split_args(args)
        self.assertNotIn("--allowedTools", rest)
        self.assertEqual(len(tools), 3)
        self.assertEqual(gui_config.join_args(rest, tools, pos), args)
        self.assertEqual(gui_config.join_args(rest, [], pos), rest)
        self.assertEqual(gui_config.split_args(["a", "b"]), (["a", "b"], [], None))

    # What is written reads back identically, including backslashes, quotes and unicode.
    def test_dumps_round_trips_the_sample_and_odd_strings(self):
        """The sample and a dict with quotes, backslashes, unicode, nesting and empty values read back unchanged."""
        data = gui_config.load_raw(self.path)
        self.assertEqual(tomllib.loads(gui_config.dumps(data)), data)
        odd = {"a": 'say "hi"\\now', "b": "größe ünï", "c": [1, 2.5, True, "x"], "d": {}, "e": {"x y": {"k": 1}}, "f": [{"n": 1}, {"n": 2}], "g": 0.00001}
        self.assertEqual(tomllib.loads(gui_config.dumps(odd)), odd)

    # Problems are reported before anything is written.
    def test_check_reports_bad_machine_settings(self):
        """A good config passes; an unknown protocol, duplicate names and a zero round time are reported."""
        self.assertEqual(gui_config.check(gui_config.load_raw(self.path)), [])
        bad = gui_config.load_raw(self.path)
        bad["providers"][0]["protocol"] = "nope"
        self.assertTrue(gui_config.check(bad))
        bad = gui_config.load_raw(self.path)
        bad["providers"][1]["name"] = "claude"
        self.assertTrue(any("unique" in p for p in gui_config.check(bad)))
        bad = gui_config.load_raw(self.path)
        bad["roundMinutes"] = 0
        self.assertTrue(any("roundMinutes" in p for p in gui_config.check(bad)))
        self.assertTrue(gui_config.check({"x": float("nan")}, machine=False))

    # Saving keeps the top comment and the old file.
    def test_save_keeps_comment_and_backup(self):
        """The first comment lines stay, the old file is stored as .bak, and the new file reads back as the data."""
        data = gui_config.load_raw(self.path)
        data["slots"] = 3
        gui_config.save_file(self.path, data)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Machine settings (kept at the top)\n"))
        self.assertEqual(gui_config.load_raw(self.path)["slots"], 3)
        self.assertEqual(gui_config.load_raw(self.path.with_name("config.toml.bak"))["slots"], 8)
        self.assertEqual(tomllib.loads(text), data)

    # Typed values keep the type of the old value.
    def test_parse_typed(self):
        """A number stays a number, true/false a bool, a new value is guessed, wrong text raises."""
        self.assertEqual(gui_config.parse_typed("5", 3), 5)
        self.assertEqual(gui_config.parse_typed("2.5", 1.0), 2.5)
        self.assertIs(gui_config.parse_typed("true", False), True)
        self.assertEqual(gui_config.parse_typed("7"), 7)
        self.assertEqual(gui_config.parse_typed("abc"), "abc")
        with self.assertRaises(ValueError):
            gui_config.parse_typed("many", 3)


# All widgets below a widget.
def descendants(widget):
    """Every widget below `widget`, depth first."""
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WindowTest(unittest.TestCase):
    """The editor window reads, edits and saves the files (fully transparent windows)."""

    def setUp(self):
        """A temp config folder, a database with one project that has a project.toml, a transparent Tk root."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            self.skipTest("no display")

        class Quiet(tk.Toplevel):
            """A Toplevel that is transparent from the start."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        quiet = mock.patch("tkinter.Toplevel", Quiet)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "config.toml").write_text(SAMPLE, encoding="utf-8")
        workspace = self.folder / "ws"
        (workspace / ".haivequeen").mkdir(parents=True)
        (workspace / ".haivequeen" / "project.toml").write_text('name = "ODP"\npriority = 2\nplanGlobs = ["plans/*.md"]\n', encoding="utf-8")
        self.conn = db.connect(self.folder / "o.db")
        db.upsert_project(self.conn, "ODP", str(workspace), "cyan", 1)

    def tearDown(self):
        """Close the windows and remove the temp folder."""
        self.root.destroy()
        self.conn.close()
        self.tmp.cleanup()

    # Set the text of the entry that shows `old`.
    def type_into_entry(self, win, old, new):
        """Replace the text of the single-line editor whose current text is `old` by `new`."""
        for widget in descendants(win.form):
            if widget.winfo_class() == "Entry" and widget.get() == old:
                widget.delete(0, "end")
                widget.insert(0, new)
                return
        self.fail("no entry showing %r" % old)

    # The provider page shows the login status and a Login button.
    def test_provider_page_shows_login_status_and_button(self):
        """The page of a provider says "Login missing" or "Login present" from the profile, and the Login button asks first, then opens the terminal."""
        import os
        from haivequeen import profile
        data = self.folder / "data"
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(data)}):
            os.environ.pop(profile.NO_CHECK_ENV, None)
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.select(1)   # provider claude
            self.assertEqual(win.login_status.cget("text"), "Login missing")
            with mock.patch("haivequeen.gui_dialog.ask", return_value=False) as ask, mock.patch("haivequeen.profile.login_run") as run:
                win.login_button.invoke()
                self.assertTrue(ask.called)
                run.assert_not_called()
            proc = mock.Mock()
            proc.poll.return_value = 0
            with mock.patch("haivequeen.gui_dialog.ask", return_value=True), mock.patch("haivequeen.profile.login_run", return_value=proc) as run:
                win.login_button.invoke()
                self.assertEqual(run.call_args[0][0]["name"], "claude")
            (data / "profile" / "claude").mkdir(parents=True)
            (data / "profile" / "claude" / ".credentials.json").write_text("{}", encoding="utf-8")
            win.refresh_login()
            self.assertEqual(win.login_status.cget("text"), "Login present")
            win.select(2)
            self.assertEqual(win.login_status.cget("text"), "Login missing")   # codex has no login yet

    # Own CLI: the three values are offered on its page and used for the status.
    def test_unknown_cli_offers_its_login_values(self):
        """A provider whose CLI is not built in shows profileEnv, loginCommand and loginFiles, and its status follows the login file."""
        import os
        from haivequeen import profile
        data = self.folder / "data"
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(data)}):
            os.environ.pop(profile.NO_CHECK_ENV, None)
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.data["providers"].append({"name": "mycli", "protocol": "mycli"})
            win.fill_nodes()
            win.select(3)
            keys = {path[0] for path, _g, _l in win.getters}
            self.assertTrue({"loginCommand", "loginFiles"} <= keys)
            win.target().update({"profileEnv": {"MYCLI_HOME": "{home}"}, "loginCommand": "mycli signin", "loginFiles": ["t.json"]})
            win.refresh_login()
            self.assertEqual(win.login_status.cget("text"), "Login missing")
            (data / "profile" / "mycli").mkdir(parents=True)
            (data / "profile" / "mycli" / "t.json").write_text("{}", encoding="utf-8")
            win.refresh_login()
            self.assertEqual(win.login_status.cget("text"), "Login present")

    # H-15 to H-19: the Apiary page of a project file.
    def test_apiary_page_shows_edits_and_saves_its_settings(self):
        """The General page of a project file offers every Apiary setting with its default under a readable label; a changed value is saved."""
        import tomllib
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        index, label = next((i, f[0]) for i, f in enumerate(win.files) if not f[2])
        win.pick_file(label)
        keys = {path[0]: like for path, _getter, like in win.getters}
        for key, default in gui_config.APIARY_DEFAULTS:
            self.assertEqual(keys[key], default, key)
        texts = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
        self.assertIn("Run the deploy", texts)
        self.assertNotIn("deployEnabled", texts)
        win.collect()
        win.target().update({"deployEnabled": False, "remoteName": "upstream", "ownerTopics": ["pricing"]})
        win.build_form()
        win.save()
        saved = tomllib.loads(win.files[index][1].read_text(encoding="utf-8"))
        self.assertIs(saved["deployEnabled"], False)
        self.assertEqual(saved["remoteName"], "upstream")
        self.assertEqual(saved["ownerTopics"], ["pricing"])

    # The provider page offers the CLI choice.
    def test_provider_page_offers_the_cli_choice(self):
        """The page shows the CLI of the provider; choosing another CLI sets all its values, Custom CLI keeps them and offers the output format."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)   # provider claude
        self.assertEqual(win.cli_var.get(), "Claude Code")
        win.choose_cli("OpenCode")
        target = win.target()
        self.assertEqual((target["protocol"], target["command"], target["beeMode"]), ("opencode", "opencode", "process"))
        self.assertEqual(win.cli_var.get(), "OpenCode")
        self.assertEqual(target["name"], "claude")   # the provider name and its slots stay
        self.assertEqual(target["slots"], 5)
        win.save()
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["providers"][0]["protocol"], "opencode")
        win.choose_cli("Custom CLI")
        self.assertEqual(win.target()["protocol"], "opencode")
        self.assertIn("outputFormat", {path[0] for path, _g, _l in win.getters})

    # Every provider offers the Bee mode with the value that applies when the file has no entry.
    def test_provider_offers_the_bee_mode_with_its_default(self):
        """A claude provider shows subagent and a codex provider shows process; choosing a value and saving writes it to the file."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)   # provider claude
        self.assertEqual({path: getter() for path, getter, _like in win.getters}[("beeMode",)], "subagent")
        win.select(2)   # provider codex
        getters = {path: getter for path, getter, _like in win.getters}
        self.assertEqual(getters[("beeMode",)](), "process")
        win.target()["beeMode"] = "subagent"   # the choice the owner makes in the list
        win.build_form()
        win.save()
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["providers"][1]["beeMode"], "subagent")

    # The language of the free text to the owner.
    def test_general_offers_the_language_and_saves_it(self):
        """General offers English and German (English without an entry); the picked language is written to the file."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(0)
        getters = {path: getter for path, getter, _like in win.getters}
        self.assertEqual(getters[("language",)](), "English")
        button = next(w for w in descendants(win.form) if w.winfo_class() == "Menubutton" and "English" in w.cget("text"))
        menu = button.nametowidget(button.cget("menu"))
        menu.invoke(menu.index("German"))
        win.save()
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["language"], "German")

    # Editing the allowed tools list and a number, then saving.
    def test_edit_allowed_tools_and_save(self):
        """Changing the allowed tools and the slots of a provider writes both; everything else in the file stays."""
        saved = []
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder, on_saved=lambda: saved.append(1))
        win.select(1)   # provider claude
        getters = {path: getter for path, getter, _like in win.getters}
        self.assertEqual(len(getters[("allowedTools",)]()), 3)
        for widget in descendants(win.form):
            if widget.winfo_class() == "Text" and "git merge" in widget.get("1.0", "end"):
                widget.delete("1.0", "end")
                widget.insert("1.0", "Read\nBash(git status)")
        self.type_into_entry(win, "5", "4")
        win.save()
        data = gui_config.load_raw(self.folder / "config.toml")
        _rest, tools, _pos = gui_config.split_args(data["providers"][0]["args"])
        self.assertEqual(tools, ["Read", "Bash(git status)"])
        self.assertEqual(data["providers"][0]["slots"], 4)
        self.assertEqual(data["providers"][1]["name"], "codex")
        self.assertEqual(data["hives"], 10)
        self.assertEqual(saved, [1])
        self.assertTrue((self.folder / "config.toml.bak").is_file())

    # A wrong value is shown and nothing is written.
    def test_wrong_number_is_refused(self):
        """Text in a number field is reported in the status line and the file stays as it was."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.type_into_entry(win, "8", "many")
        win.save()
        self.assertIn("slots", win.status.cget("text"))
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["slots"], 8)

    # The project.toml of an Apiary is offered as a second file.
    def test_project_file_is_listed_and_editable(self):
        """The file list holds config.toml and the project.toml of ODP; saving a changed priority writes it."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.assertEqual([f[0] for f in win.files], ["config.toml (machine)", "project.toml (ODP)"])
        win.pick_file("project.toml (ODP)")
        self.type_into_entry(win, "2", "5")
        win.save()
        self.assertEqual(gui_config.load_raw(win.files[1][1])["priority"], 5)

    # Adding and removing a provider.
    def test_add_and_remove_provider(self):
        """A new provider appears in the list and in the saved file; removing it afterwards removes it again."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.add_provider()
        self.assertIn("provider: new-provider", win.nodes.get(0, "end"))
        self.type_into_entry(win, "new-provider", "extra")
        win.save()
        names = [p["name"] for p in gui_config.load_raw(self.folder / "config.toml")["providers"]]
        self.assertEqual(names, ["claude", "codex", "extra"])
        win.select(3)   # the new provider
        with mock.patch("haivequeen.gui_dialog.ask", return_value=True):
            win.remove_node()
        win.save()
        self.assertEqual([p["name"] for p in gui_config.load_raw(self.folder / "config.toml")["providers"]], ["claude", "codex"])


    # The block "Build" of the General page.
    def test_general_shows_one_block_build_with_three_fields(self):
        """The General page of config.toml has one block "Build" with exactly the three fields and one sentence each; nothing is named buildJobs."""
        with mock.patch.object(gui_config, "machine_cores", return_value=8):
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            texts = [w.cget("text") for w in descendants(win.form) if w.winfo_class() in ("Label", "Labelframe")]
            self.assertEqual(texts.count("Build"), 1)
            self.assertEqual([t for t, *_ in [(f[1],) for f in gui_config.BUILD_FIELDS]], ["CPU cores per build", "Parallel builds", "Minimum free RAM before a build"])
            for _key, label, explanation in gui_config.BUILD_FIELDS:
                self.assertIn(label, texts)
                self.assertIn(explanation, texts)
                self.assertEqual(explanation.count(". "), 0)   # one sentence
            blob = " ".join(texts).lower()
            for word in ("buildjobs", "coreshare", "reservemb", "perjobmb", "firstjobs", "envvar", "measurements"):
                self.assertNotIn(word, blob)
            self.assertNotIn("buildJobs", [path[0] for path, _g, _l in win.getters])   # no raw field of the table either
            menu = next(w for w in descendants(win.form) if w.winfo_class() == "Menubutton")
            self.assertTrue(menu.cget("text").startswith("6"))   # the default of 8 cores
            popup = win.root.nametowidget(menu["menu"])
            choices = [popup.entrycget(i, "label") for i in range(popup.index("end") + 1)]
            self.assertEqual(choices, [str(n) for n in range(1, 9)])   # 1 to the 8 logical cores, a selection

    def test_build_block_defaults_and_old_values(self):
        """Without a setting the block shows two thirds of the cores rounded up, parallel 1 and 2 GB; an old coreShare shows the count it gives today."""
        with mock.patch.object(gui_config, "machine_cores", return_value=8):
            (self.folder / "config.toml").write_text("slots = 2\n", encoding="utf-8")
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            self.assertEqual([v.get() for v in win.build_vars], ["6", "1", "2"])
            win.win.destroy()
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.data["buildJobs"] = {"coreShare": "1/2", "envVar": "BUILD_MP_JOBS"}
            win.build_form()
            self.assertEqual([v.get() for v in win.build_vars], ["4", "1", "2"])

    def test_saving_the_build_block_rewrites_the_old_entries(self):
        """Saving writes cores, parallel and minimum RAM into the table, removes coreShare, fixed and a plain number, keeps the other keys."""
        with mock.patch.object(gui_config, "machine_cores", return_value=8):
            for old in ('buildJobs = { envVar = "BUILD_MP_JOBS", coreShare = "2/3", fixed = 3, reserveMB = 1024 }', 'buildJobs = 4'):
                (self.folder / "config.toml").write_text("slots = 2\n" + old + "\n", encoding="utf-8")
                win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
                win.build_vars[0].set("5")
                win.build_vars[1].set("2")
                win.build_vars[2].set("3")
                win.save()
                saved = gui_config.load_raw(self.folder / "config.toml")["buildJobs"]
                self.assertEqual((saved["cores"], saved["parallel"], saved["minFreeRamGB"]), (5, 2, 3))
                for gone in ("coreShare", "fixed"):
                    self.assertNotIn(gone, saved)
                if "envVar" in old:
                    self.assertEqual((saved["envVar"], saved["reserveMB"]), ("BUILD_MP_JOBS", 1024))
                win.win.destroy()

    def test_build_ram_uses_the_decimal_comma_in_german_and_stores_a_toml_number(self):
        """German shows and takes "2,5" (a point is still taken), the file always holds a number, a wrong value is refused with a German message."""
        path = self.folder / "config.toml"
        with mock.patch.object(gui_config, "machine_cores", return_value=8), mock.patch.object(i18n, "machine_language", return_value="German"):
            path.write_text("slots = 2\n[buildJobs]\nminFreeRamGB = 2.5\n", encoding="utf-8")
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            self.assertEqual(win.build_vars[2].get(), "2,5")
            for typed, stored, shown in (("3,25", 3.25, "3,25"), ("4.5", 4.5, "4,5"), ("6,0", 6, "6")):
                win.build_vars[2].set(typed)
                win.save()
                self.assertEqual(gui_config.load_raw(path)["buildJobs"]["minFreeRamGB"], stored)
                win.build_form()
                self.assertEqual(win.build_vars[2].get(), shown)
            before = path.read_text(encoding="utf-8")
            win.build_vars[2].set("1,2,3")
            win.save()
            self.assertEqual(win.status.cget("text"), "Mindestens freier RAM vor einem Build: eine Zahl wird erwartet")
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            win.win.destroy()

    def test_build_ram_keeps_the_decimal_point_in_english(self):
        """English shows the point; a comma is taken there too."""
        path = self.folder / "config.toml"
        with mock.patch.object(gui_config, "machine_cores", return_value=8), mock.patch.object(i18n, "machine_language", return_value="English"):
            path.write_text("slots = 2\n[buildJobs]\nminFreeRamGB = 2.5\n", encoding="utf-8")
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            self.assertEqual(win.build_vars[2].get(), "2.5")
            win.build_vars[2].set("3,5")
            win.save()
            self.assertEqual(gui_config.load_raw(path)["buildJobs"]["minFreeRamGB"], 3.5)
            win.win.destroy()

    def test_a_wrong_build_value_is_refused(self):
        """A parallel value of 0 stays on screen with its message and nothing is written."""
        with mock.patch.object(gui_config, "machine_cores", return_value=8):
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.build_vars[1].set("0")
            before = (self.folder / "config.toml").read_text(encoding="utf-8")
            win.save()
            self.assertIn("Parallel builds", win.status.cget("text"))
            self.assertEqual((self.folder / "config.toml").read_text(encoding="utf-8"), before)

    # H-10, H-17: looking at a provider is no selection; changing its ticks is.
    def legacy_page(self, **entry):
        """The window on a provider entry from before the ticks."""
        path = self.folder / "config.toml"
        data = gui_config.load_raw(path)
        data["providers"] = [{"name": "old", "protocol": "claude", "command": "claude", "classes": {"medium": {"model": "m"}}, **entry}]
        gui_config.save_file(path, data)
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)
        return win, path

    def test_showing_and_saving_an_old_provider_changes_nothing_of_its_selection(self):
        win, path = self.legacy_page(capabilities=["build"], use="capability-only")
        self.assertTrue(any("as before" in str(w.cget("text")) for w in descendants(win.form) if w.winfo_class() == "Label"))
        win.save()
        saved = gui_config.load_raw(path)["providers"][0]
        self.assertEqual(saved["capabilities"], ["build"])
        self.assertEqual(saved["use"], "capability-only")
        self.assertNotIn("capabilitiesVersion", saved)

    def test_changing_the_ticks_of_an_old_provider_makes_them_the_selection(self):
        win, path = self.legacy_page(capabilities=["build"])
        next(w for w in descendants(win.form) if w.winfo_class() == "Checkbutton" and w.cget("text") == "Deploy").invoke()
        win.save()
        saved = gui_config.load_raw(path)["providers"][0]
        self.assertEqual(saved["capabilitiesVersion"], 1)
        self.assertEqual(sch_capabilities(saved), ["Coding", "Queen", "Build", "Deploy"])

    def test_project_file_has_no_build_block(self):
        """The build block belongs to the machine settings only."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_file("project.toml (ODP)")
        self.assertIsNone(win.build_vars)


def sch_capabilities(entry):
    """The ticks of a saved provider entry as the program reads them."""
    from haivequeen import scheduler
    return scheduler.provider_capabilities(entry)


if __name__ == "__main__":
    unittest.main()
