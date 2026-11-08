"""Tests of the settings editor: TOML writing, the allowed tools list, checks, saving with a backup, and the window."""
import sys
import tempfile
import threading
import time
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
workspace = "C:\\\\Users\\\\Example\\\\Projects\\\\Sample"
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

    def test_bzz_install_button_reports_supported_and_skipped_clis(self):
        from haivequeen import bzz_install
        for language in ("English", "German"):
            with self.subTest(language=language), mock.patch("haivequeen.i18n.machine_language", return_value=language):
                win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
                button = next(w for w in descendants(win.form)
                              if w.winfo_class() == "Button" and w.cget("text") == i18n.tr("Install /bzz"))
                result = {"Claude Code": "installed", "Codex CLI": "skipped: literal /bzz is not supported"}
                with mock.patch.object(bzz_install, "install", return_value=result) as install:
                    button.invoke()
                install.assert_called_once_with()
                labels = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
                self.assertIn("Claude Code: %s\nCodex CLI: %s" % (i18n.tr("installed"), i18n.tr(result["Codex CLI"])), labels)
                win.win.destroy()

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

    # The Apiary page of a project file.
    def test_apiary_page_shows_edits_and_saves_its_settings(self):
        """The General page of a project file offers every Apiary setting with its default under a readable label; a changed value is saved."""
        import tomllib
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(win.entries.index(("apiaries", 0)))
        keys = {path[1]: like for path, _getter, like in win.getters if path[0] == "file"}
        for key, default in gui_config.APIARY_DEFAULTS:
            if key in gui_config.RELEASE_KEYS:
                self.assertNotIn(key, keys)   # The release settings are offered only for a remote on GitHub, this Apiary has none
                continue
            self.assertEqual(keys[key], default, key)
        texts = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
        self.assertIn("Run the deploy", texts)
        self.assertNotIn("deployEnabled", texts)
        win.collect()
        win.target()["file"].update({"deployEnabled": False, "remoteName": "upstream", "ownerTopics": ["pricing"]})
        win.build_form()
        win.save()
        saved = tomllib.loads(win.apiaries[0]["path"].read_text(encoding="utf-8"))
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
        """A claude provider shows subagent and a codex provider shows process; a subagent set for codex is shown and saved as process."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)   # provider claude
        self.assertEqual({path: getter() for path, getter, _like in win.getters}[("beeMode",)], "subagent")
        win.select(2)   # provider codex
        getters = {path: getter for path, getter, _like in win.getters}
        self.assertEqual(getters[("beeMode",)](), "process")
        win.target()["beeMode"] = "subagent"   # a stored subagent of codex: Codex has no embedded Bees
        win.build_form()
        win.save()
        self.assertEqual(gui_config.load_raw(self.folder / "config.toml")["providers"][1]["beeMode"], "process")

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

    def test_provider_arguments_survive_save_and_unrelated_edit(self):
        path = self.folder / "config.toml"
        example = gui_config.load_raw(Path(__file__).resolve().parents[1] / "examples" / "config.example.toml")
        data = gui_config.load_raw(path)
        args = example["providers"][0]["args"]
        args[2:2] = ["--allowedTools", "Read, Bash(git status)"]
        data["providers"][0]["args"] = args
        data["providers"][0]["extraList"] = ["first", "", " last "]
        gui_config.save_file(path, data)
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)
        win.save()
        saved = gui_config.load_raw(path)["providers"][0]
        self.assertEqual(saved["args"], args)
        self.assertEqual(saved["extraList"], ["first", "", " last "])
        self.type_into_entry(win, "5", "4")
        win.save()
        saved = gui_config.load_raw(path)["providers"][0]
        self.assertEqual(saved["slots"], 4)
        self.assertEqual(saved["args"], args)
        self.assertEqual(saved["extraList"], ["first", "", " last "])

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

    # The project.toml of an Apiary is edited on its page of the tab Apiaries.
    def test_project_file_is_listed_and_editable(self):
        """The tab Apiaries lists ODP; saving a changed priority on its page writes its project.toml."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        self.assertEqual(win.nodes.get(0, "end"), ("ODP",))
        self.type_into_entry(win, "2", "5")
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(gui_config.load_raw(self.folder / "ws" / ".haivequeen" / "project.toml")["priority"], 5)

    # three tabs, no file selection.
    def test_window_has_three_tabs_and_no_file_selection(self):
        """The window offers the tabs General, Providers and Apiaries (no Servers tab); no File choice and no file name is shown; each tab lists only its own pages."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.assertEqual([b.cget("text") for b in win.tab_buttons.values()], ["General", "Providers", "Apiaries"])
        texts = [w.cget("text") for w in descendants(win.win) if w.winfo_class() in ("Label", "Button", "Menubutton", "Radiobutton")]
        self.assertFalse([text for text in texts if "File:" in text or "config.toml (" in text or "project.toml (" in text], texts)
        self.assertFalse(win.side.winfo_manager())   # the General tab has no list
        win.pick_tab("providers")
        self.assertEqual(win.nodes.get(0, "end"), ("claude", "codex"))
        self.assertEqual(win.add_button.cget("text"), "Add provider")
        win.pick_tab("apiaries")
        self.assertEqual(win.nodes.get(0, "end"), ("ODP",))
        self.assertEqual(win.add_button.cget("text"), "Add Apiary")
        self.assertTrue(win.side.winfo_manager())

    # every setting of an Apiary on one page, each once.
    def test_apiary_page_holds_all_its_settings_once(self):
        """The page of an Apiary shows its name and workspace from config.toml and all settings of its project.toml, including the four switches; no key twice."""
        with mock.patch("haivequeen.publish.github_repository", return_value="owner/name"):
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            win.pick_tab("apiaries")
        paths = [path for path, _getter, _like in win.getters]
        keys = [path[-1] for path in paths]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertIn(("entry", "name"), paths)
        self.assertIn(("place", "repository"), paths)   # decision 741 (1): Repository and Plans folder replace workspace and codeRepo
        self.assertIn(("place", "plansFolder"), paths)
        self.assertNotIn(("entry", "workspace"), paths)
        self.assertTrue({("file", "priority"), ("file", "planGlobs")} <= set(paths))
        labels = [w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"]
        for label in ("Run the compile check", "Run the build", "Run the deploy", "Publish release at integration"):
            self.assertIn(label, labels)
        self.assertNotIn(("file", "name"), paths)   # the name of config.toml is the only one
        self.assertIsNone(win.build_vars)
        self.assertFalse([p for p, _g, _l in win.getters if p[-1] in ("slots", "hives", "capabilities")])   # no machine or provider setting on an Apiary page

    # Save rewrites no unchanged project.toml.
    def test_save_rewrites_no_unchanged_apiary_file(self):
        """Saving after only looking at an Apiary page leaves its project.toml untouched and keeps the provider capabilities as they are."""
        path = self.folder / "ws" / ".haivequeen" / "project.toml"
        before = path.read_text(encoding="utf-8")
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)
        win.select(2)
        win.pick_tab("apiaries")
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertFalse(path.with_name("project.toml.bak").exists())
        codex = gui_config.load_raw(self.folder / "config.toml")["providers"][1]
        self.assertEqual((codex["capabilities"], codex["use"]), (["images"], "capability-only"))

    # the capabilities stay on the provider pages.
    def test_capabilities_are_on_the_provider_pages_only(self):
        """A provider page offers the capability ticks; a changed tick is saved for that provider."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.select(1)   # provider claude
        self.assertIn(("capabilities",), [p for p, _g, _l in win.getters])
        boxes = {w.cget("text"): w for w in descendants(win.form) if w.winfo_class() == "Checkbutton"}
        boxes["Deploy"].invoke()
        win.save()
        claude = gui_config.load_raw(self.folder / "config.toml")["providers"][0]
        self.assertIn("Deploy", claude["capabilities"])

    # Add Apiary creates its project.toml in the background.
    def test_add_apiary_creates_its_project_file(self):
        """Add Apiary adds a page; with a name and an existing workspace Save writes config.toml, registers the Apiary and creates its project.toml with the switches set on the page."""
        new = self.folder / "fresh"
        new.mkdir()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        win.add_entry()
        self.assertEqual(win.nodes.get(0, "end"), ("ODP", "new-apiary"))
        self.type_into_entry(win, "new-apiary", "Fresh")
        self.type_into_entry(win, "", str(new))
        self.assertTrue(win.collect())
        win.target()["file"]["buildEnabled"] = False
        win.build_form()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        listed = gui_config.load_raw(self.folder / "config.toml")["projects"]
        self.assertEqual(listed[-1], {"name": "Fresh", "workspace": str(new)})
        self.assertEqual(gui_config.load_raw(new / ".haivequeen" / "project.toml"), {"buildEnabled": False, "worktreePrefix": "Fresh"})   # decision 741 (2): the prefix follows the name
        self.assertEqual(self.conn.execute("SELECT workspace FROM projects WHERE name='Fresh'").fetchone()[0], str(new.resolve()))

    # A new Apiary in a workspace that already has a project.toml (Vera-7 open point).
    def existing_workspace(self):
        """A folder with a project.toml of its own (a comment, three settings) and the text of that file."""
        folder = self.folder / "known"
        (folder / ".haivequeen").mkdir(parents=True)
        text = '# kept\npriority = 7\nplanGlobs = ["docs/*.md"]\ndeployEnabled = false\n'
        (folder / ".haivequeen" / "project.toml").write_text(text, encoding="utf-8")
        return folder, text

    def new_apiary_in(self, win, folder):
        """Add an Apiary on the tab Apiaries and type `folder` as its workspace (not yet entered)."""
        win.pick_tab("apiaries")
        win.add_entry()
        self.type_into_entry(win, "", str(folder))

    def shown(self, win) -> dict:
        """{key: shown value} of the project.toml settings on the shown Apiary page."""
        return {path[1]: getter() for path, getter, _like in win.getters if path[0] == "file"}

    def test_entered_workspace_shows_its_project_file_at_once(self):
        """Entering the workspace of a new Apiary shows the settings of the project.toml already there, without leaving the page."""
        folder, _text = self.existing_workspace()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.new_apiary_in(win, folder)
        node = win.node
        workspace = next(w for w in descendants(win.form) if w.winfo_class() == "Entry" and w.get() == str(folder))
        self.assertTrue({"<FocusOut>", "<Key-Return>"} <= set(workspace.bind()), workspace.bind())
        win.workspace_entered()   # what both bindings call (hidden test windows get no focus events)
        self.assertEqual(win.node, node)
        shown = self.shown(win)
        self.assertEqual((shown["priority"], shown["planGlobs"], shown["deployEnabled"]), ("7", ["docs/*.md"], False))

    def test_entered_workspace_keeps_the_unsaved_edits(self):
        """A setting changed before the workspace is entered stays as typed; the other values of the existing project.toml stay as they are."""
        folder, _text = self.existing_workspace()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        win.add_entry()
        win.target()["file"]["priority"] = 9
        win.build_form()
        self.type_into_entry(win, "", str(folder))
        win.workspace_entered()
        shown = self.shown(win)
        self.assertEqual((shown["priority"], shown["planGlobs"], shown["deployEnabled"]), ("9", ["docs/*.md"], False))
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        saved = (folder / ".haivequeen" / "project.toml").read_text(encoding="utf-8")
        self.assertEqual(tomllib.loads(saved), {"priority": 9, "planGlobs": ["docs/*.md"], "deployEnabled": False})
        self.assertIn("# kept", saved)

    def test_save_right_after_typing_the_workspace_keeps_the_existing_file(self):
        """Save without entering the workspace first still lays the edits over the existing project.toml instead of replacing it."""
        folder, _text = self.existing_workspace()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        win.add_entry()
        win.target()["file"]["buildEnabled"] = False
        win.build_form()
        self.type_into_entry(win, "", str(folder))
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(gui_config.load_raw(folder / ".haivequeen" / "project.toml"),
                         {"priority": 7, "planGlobs": ["docs/*.md"], "deployEnabled": False, "buildEnabled": False})

    def test_entered_workspace_without_edits_leaves_the_file_untouched(self):
        """Entering the workspace and saving without a change writes no project.toml and no backup; the Apiary is registered."""
        folder, text = self.existing_workspace()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.new_apiary_in(win, folder)
        win.workspace_entered()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        path = folder / ".haivequeen" / "project.toml"
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        self.assertFalse(path.with_name("project.toml.bak").exists())
        self.assertEqual(self.conn.execute("SELECT workspace FROM projects WHERE name='new-apiary'").fetchone()[0], str(folder.resolve()))

    def test_entering_the_same_workspace_again_rebuilds_nothing(self):
        """Entering an unchanged workspace keeps the page and its typed values as they are."""
        folder, _text = self.existing_workspace()
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.new_apiary_in(win, folder)
        win.workspace_entered()
        self.type_into_entry(win, "7", "8")
        rows = list(win.form.winfo_children())
        win.workspace_entered()
        self.assertEqual(list(win.form.winfo_children()), rows)
        self.assertEqual(self.shown(win)["priority"], "8")

    def test_add_apiary_without_workspace_writes_no_project_file(self):
        """A new Apiary whose workspace does not exist cannot keep changed settings: nothing is written and the reason is shown."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        win.add_entry()
        win.target()["file"]["deployEnabled"] = True
        before = (self.folder / "config.toml").read_text(encoding="utf-8")
        win.save()
        self.assertIn("repository or plans folder does not exist", win.status.cget("text"))
        self.assertEqual((self.folder / "config.toml").read_text(encoding="utf-8"), before)

    # Adding and removing a provider.
    def test_add_and_remove_provider(self):
        """A new provider appears in the list and in the saved file; removing it afterwards removes it again."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.add_provider()
        self.assertIn("claude-2", win.nodes.get(0, "end"))   # proposed from CLI and source, "claude" is taken
        self.type_into_entry(win, "claude-2", "extra")
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

    # Looking at a provider is no selection; changing its ticks is.
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
        self.assertFalse(any("as before" in str(w.cget("text")) for w in descendants(win.form) if w.winfo_class() == "Label"))   # No promise of implicit steps
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


    def test_build_notes_wrap_within_the_window(self):
        """The explanations of the Build block sit under their field and wrap to the available width, at the normal and at a small window size."""
        with mock.patch.object(gui_config, "machine_cores", return_value=8):
            win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
            for geo in ("1130x800", "640x700"):
                win.win.geometry(geo)
                win.win.update()
                avail = win.form.winfo_width()
                self.assertGreater(avail, 100)
                self.assertEqual(len(win.build_notes), 3)
                for note in win.build_notes:
                    self.assertLessEqual(int(note.cget("wraplength")), avail)
                    self.assertLessEqual(note.winfo_x() + note.winfo_reqwidth(), avail + 2, geo)
                    self.assertGreaterEqual(note.winfo_reqheight(), 1)

    def test_bottom_note_has_its_own_row_below_the_buttons(self):
        """The save note sits on its own row under the buttons over the full window width and shows every line of its text."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        widths = []
        for geo in ("1130x800", "760x700", "640x700"):
            win.win.geometry(geo)
            win.win.update()
            note = win.bottom_note
            wrap = int(note.cget("wraplength"))
            self.assertGreaterEqual(wrap, 120)
            self.assertLessEqual(wrap, note.winfo_width())
            widths.append(wrap)
            self.assertGreater(note.winfo_width(), win.win.winfo_width() - 40, geo)
            self.assertGreaterEqual(note.winfo_height(), note.winfo_reqheight(), geo)
            self.assertLessEqual(note.winfo_rooty() + note.winfo_height(), win.win.winfo_rooty() + win.win.winfo_height(), geo)
            for child in win.bottom_buttons:
                self.assertLessEqual(child.winfo_x() + child.winfo_width(), win.win.winfo_width(), geo)
                self.assertLessEqual(child.winfo_rooty() + child.winfo_height(), note.winfo_rooty(), geo)
        self.assertLess(widths[2], widths[0])
        win.win.destroy()

    def test_ram_reserve_is_not_offered_but_kept_on_save(self):
        """ramReserveMb is a machine-only setting of config.toml: no field on the General page, the stored value stays when saving."""
        path = self.folder / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("hives = 10", "hives = 10" + chr(10) + "ramReserveMb = 3072"), encoding="utf-8")
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        self.assertNotIn("ramReserveMb", [p[0] for p, _g, _l in win.getters])
        self.assertIn("ramReserveMb", win.data)
        win.save()
        self.assertEqual(gui_config.load_raw(path)["ramReserveMb"], 3072)

    def test_project_file_has_no_build_block(self):
        """The build block belongs to the machine settings only."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.pick_tab("apiaries")
        self.assertIsNone(win.build_vars)


def sch_capabilities(entry):
    """The ticks of a saved provider entry as the program reads them."""
    from haivequeen import scheduler
    return scheduler.provider_capabilities(entry)


class BenchmarkWindowTest(WindowTest):
    """U2: the buttons of the provider page always fit; the benchmark has its own window."""

    def provider_page(self, size):
        """Open the settings at `size` on the first provider page and return the window."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.folder)
        win.win.geometry(size)
        win.select(1)
        self.root.update()
        return win

    def assert_buttons_fit(self, win):
        """Login, Benchmark Start Context and the status text lie completely inside the form."""
        self.root.update()
        right = win.form.winfo_rootx() + win.form.winfo_width()
        for widget in (win.login_status, win.login_button, win.benchmark_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreaterEqual(widget.winfo_rootx(), win.form.winfo_rootx())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right, widget.cget("text"))
            self.assertGreaterEqual(widget.winfo_reqwidth(), 1)

    def test_buttons_fit_at_the_default_and_every_smaller_size(self):
        """Wrapped to a second row instead of cut, at 1130 px and at the smallest size the window allows."""
        for size in ("1130x700", "900x600", "1x1"):
            win = self.provider_page(size)
            self.assert_buttons_fit(win)
            self.assertGreater(win.win.minsize()[0], 1)
            win.win.destroy()

    def test_provider_page_has_only_the_button(self):
        """No benchmark text on the provider page."""
        win = self.provider_page("1130x700")
        self.assertIsNone(win.benchmark_status)
        for widget in descendants(win.form):
            if widget.winfo_class() == "Label":
                self.assertNotIn("no benchmark yet", str(widget.cget("text")))

    def wait_until(self, predicate):
        """Pump Tk events while a background measurement reports its state."""
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate(), "Timed out waiting for the benchmark window")

    def test_window_shows_progress_and_results_table(self):
        """Opening is passive; starting shows live progress and refreshes stored results."""
        from haivequeen import startctx
        startctx.store_benchmark(self.conn, "claude", "ODP", "hq", 32000, when="2026-10-01T10:00:00")
        startctx.store_benchmark(self.conn, "claude", "ODP", "own", 100000, when="2026-10-01T10:00:00")
        entered, release = threading.Event(), threading.Event()

        def fake(conn, cfg, name, progress=None):
            progress("2/4: ODP without it ...")
            entered.set()
            release.wait(5)
            startctx.store_benchmark(conn, name, "ODP", "hq", 40000, when="2026-10-02T10:00:00")
            return {"excluded": False, "errors": [], "results": [], "completed": 4, "total": 4}

        win = self.provider_page("1130x700")
        with mock.patch("haivequeen.gui_dialog.ask", return_value=True) as ask, mock.patch("haivequeen.startctx.benchmark", side_effect=fake) as bench:
            win.benchmark_button.invoke()
            bw = win.benchmark_win
            bench.assert_not_called()
            ask.assert_not_called()
            self.assertEqual(bw.win.title(), "Benchmark")
            heads = [bw.table.heading(c)["text"] for c in bw.table["columns"]]
            self.assertEqual(heads, ["Apiary", "HaiveQueen profile", "Own profile", "Saving", "Date"])
            rows = [bw.table.item(i)["values"] for i in bw.table.get_children()]
            self.assertEqual([str(v) for v in rows[0]], ["ODP", "32k", "100k", "-68 %", "2026-10-01"])
            try:
                bw.start_button.invoke()
                self.wait_until(lambda: entered.is_set() and "2/4: ODP" in bw.progress.cget("text"))
                self.assertEqual(bw.start_button.cget("state"), "disabled")
                bw.start()  # A second start must not launch another measurement.
                bench.assert_called_once()
            finally:
                release.set()
            self.wait_until(lambda: not bw.running)
            self.assertIn("Done: 4 of 4", bw.progress.cget("text"))
            self.assertEqual(bw.start_button.cget("state"), "normal")
            rows = [bw.table.item(i)["values"] for i in bw.table.get_children()]
            self.assertEqual([str(v) for v in rows[0]], ["ODP", "40k", "100k", "-60 %", "2026-10-02"])

    def test_window_shows_error_and_excluded(self):
        """Starting a failed or excluded run reports it in the existing window."""
        win = self.provider_page("1130x700")
        win.benchmark_button.invoke()
        bw = win.benchmark_win
        results = [{"excluded": False, "errors": ["ODP without profile: not logged in"], "results": []},
                   {"excluded": True, "errors": [], "results": []}]
        with mock.patch("haivequeen.gui_dialog.ask", return_value=True), mock.patch("haivequeen.startctx.benchmark", side_effect=results):
            for expected in ("not logged in", "excluded"):
                bw.start_button.invoke()
                self.wait_until(lambda: not bw.running)
                self.assertIn(expected, bw.error.cget("text"))
                self.assertEqual(bw.start_button.cget("state"), "normal")

    def test_declined_question_keeps_window_without_measurement(self):
        """Declining the in-window confirmation leaves the window and results intact."""
        win = self.provider_page("1130x700")
        with mock.patch("haivequeen.gui_dialog.ask", return_value=False) as ask, mock.patch("haivequeen.startctx.benchmark") as bench:
            win.benchmark_button.invoke()
            bw = win.benchmark_win
            ask.assert_not_called()
            bw.start_button.invoke()
            ask.assert_called_once()
            self.assertIs(ask.call_args.args[0], bw.win)
            bench.assert_not_called()
        self.assertTrue(bw.win.winfo_exists())
        self.assertFalse(bw.running)
        self.assertEqual(bw.start_button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
