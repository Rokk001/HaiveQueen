"""Tests of the settings editor: TOML writing, the allowed tools list, checks, saving with a backup, and the window."""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, gui_config  # noqa: E402

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
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            win.remove_node()
        win.save()
        self.assertEqual([p["name"] for p in gui_config.load_raw(self.folder / "config.toml")["providers"]], ["claude", "codex"])


if __name__ == "__main__":
    unittest.main()
