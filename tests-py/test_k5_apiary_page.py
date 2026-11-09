"""Owner decisions 741 and 742: the simplified Project page (Repository, Plans folder, labels, Advanced) and fields only as wide as their values need."""
import sys
import tempfile
import tkinter
import tkinter.font
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, gui_config, publish  # noqa: E402

CONFIG = '''slots = 2

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--allowedTools", "Read,Edit", "--effort", "{effort}"]
slots = 3
use = "preferred"
[providers.classes]
medium = { "model" = "claude-sonnet-5-5", "effort" = "medium" }

[[projects]]
name = "Plain"
workspace = "%s"

[[projects]]
name = "HaiveQueen"
workspace = "%s"
'''

SPLIT = '''# The plans live apart from the code.
name = "HaiveQueen"
colour = "magenta"
priority = 0
codeRepo = "%s"
integrationBranch = "main"
worktreePrefix = "HQ"
planGlobs = ["plans/*-PLAN.md"]
planExclude = ["plans/*-STATE.md"]
compileCheck = "python.exe -W ignore -m unittest discover -s tests-py -p test*.py"

[views]
queue = "views/QUEUE.md"
'''

INTERNAL = ("workspace", "codeRepo", "repo", "worktreePrefix", "worktreeLocation", "planGlobs", "planExclude", "ignoredBuildOutput", "buildFlowDoc",
            "integratorInstructions", "buildProcessNames", "compileCheck", "compileCheckEnabled", "integrationBranch", "remoteName", "ownerTopics", "views", "queue")
# A typical value of each kind (owner decision 742): it must show without clipping.
TYPICAL = {"short": "HaiveQueen-Pilot", "path": r"C:\Users\example\Projects\Example-Workspace\hq\plans"}


def esc(path) -> str:
    """A path as text inside a TOML basic string."""
    return str(path).replace("\\", "\\\\")


def descendants(widget):
    """Every widget below `widget`, depth first."""
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ApiaryPageCase(unittest.TestCase):
    """A settings folder with two registered Projects: Plain (plans in docs/plans of its repository) and HaiveQueen (a separate plans folder)."""

    def setUp(self):
        try:
            self.root = tkinter.Tk()
            self.root.attributes("-alpha", 0.0)
        except tkinter.TclError as exc:
            self.skipTest("no display: %s" % exc)
        self.addCleanup(self.root.destroy)

        class Quiet(tkinter.Toplevel):
            """A Toplevel that is transparent from the start."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)
        quiet = mock.patch("tkinter.Toplevel", Quiet)
        quiet.start()
        self.addCleanup(quiet.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.plain = self.base / "plain"
        (self.plain / ".haivequeen").mkdir(parents=True)
        (self.plain / "docs" / "plans").mkdir(parents=True)
        self.plain_text = 'name = "Plain"\npriority = 1\n'
        (self.plain / ".haivequeen" / "project.toml").write_text(self.plain_text, encoding="utf-8")
        self.code = self.base / "HaiveQueen"
        self.code.mkdir()
        self.plans = self.base / "Example-Workspace" / "hq"
        (self.plans / ".haivequeen").mkdir(parents=True)
        self.split_text = SPLIT % esc(self.code)
        self.split_file = self.plans / ".haivequeen" / "project.toml"
        self.split_file.write_text(self.split_text, encoding="utf-8")
        (self.base / "config.toml").write_text(CONFIG % (esc(self.plain), esc(self.plans)), encoding="utf-8")
        self.conn = db.connect(self.base / "o.db")
        self.addCleanup(self.conn.close)
        db.upsert_project(self.conn, "Plain", str(self.plain), "white", 1)
        db.upsert_project(self.conn, "HaiveQueen", str(self.plans), "magenta", 0)

    def window(self, apiary=None):
        """The settings window, showing the page of the Project named `project` when given."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.base)
        if apiary is not None:
            win.pick_tab("apiaries")
            index = [a["name"] for a in win.apiaries].index(apiary)
            win.select(win.entries.index(("apiaries", index)))
        return win

    def shown(self, win) -> dict:
        """{path: shown value} of the shown page."""
        return {path: getter() for path, getter, _like in win.getters}

    def entry_of(self, win, path):
        """The line editor of `path` on the shown page."""
        return next(w for w in win.rows[path].winfo_children() if w.winfo_class() == "Entry")

    def set_entry(self, win, path, text):
        """Type `text` into the line editor of `path`."""
        widget = self.entry_of(win, path)
        widget.delete(0, "end")
        widget.insert(0, text)


class PlaceTests(unittest.TestCase):
    """Decision 741 (1), (2): the folders and the worktree prefix of a Project."""

    def test_repository_and_plans_folder_from_the_settings(self):
        ws, code = str(Path("/a/plans").resolve()), str(Path("/a/code").resolve())
        self.assertEqual(gui_config.apiary_place(ws, {}), (ws, ""))
        self.assertEqual(gui_config.apiary_place(ws, {"codeRepo": code}), (code, ws))
        self.assertEqual(gui_config.apiary_place(ws, {"codeRepo": ws}), (ws, ""))
        self.assertEqual(gui_config.apiary_place(ws, {"repo": code}), (code, ws))   # project files of the v1 migration
        self.assertEqual(gui_config.apiary_place("", {}), ("", ""))

    def test_worktree_prefix_follows_the_name(self):
        self.assertEqual(gui_config.worktree_prefix("HaiveQueen"), "HaiveQueen")
        self.assertEqual(gui_config.worktree_prefix("My game!"), "My-game")
        self.assertEqual(gui_config.worktree_prefix("???"), "wt")

    def test_releases_use_the_repository_never_the_plans_folder(self):
        calls = []

        def run(argv, cwd):
            calls.append((argv, cwd))
            return 0, "https://github.com/owner/name.git"
        env = SimpleNamespace(workspace=Path("plans"), code_repo=Path("code"), settings={"remoteName": "origin"})
        self.assertEqual(publish.github_repository(env, run), "owner/name")
        self.assertEqual(calls[0][0][:3], ["git", "-C", "code"])
        self.assertEqual(calls[0][1], Path("code"))
        publish.branch_heads(env, run)
        self.assertTrue(all(argv[2] == "code" for argv, _cwd in calls))
        settings_only = SimpleNamespace(workspace=Path("plans"), settings={"codeRepo": "code"})
        self.assertEqual(publish._repo(settings_only), Path("code"))
        self.assertEqual(publish._repo(SimpleNamespace(workspace=Path("plans"), settings={})), Path("plans"))


class ApiaryPageTests(ApiaryPageCase):
    """Decision 741: one Repository field, an optional Plans folder, labels with explanations, Advanced collapsed, no field twice."""

    def test_apiary_with_plans_in_the_repository(self):
        win = self.window("Plain")
        shown = self.shown(win)
        self.assertEqual(shown[("place", "repository")], str(self.plain))
        self.assertEqual(shown[("place", "plansFolder")], "")
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual((self.plain / ".haivequeen" / "project.toml").read_text(encoding="utf-8"), self.plain_text)

    def test_apiary_with_a_separate_plans_folder_keeps_its_values(self):
        win = self.window("HaiveQueen")
        shown = self.shown(win)
        self.assertEqual(shown[("place", "repository")], str(self.code))
        self.assertEqual(shown[("place", "plansFolder")], str(self.plans))
        self.assertEqual(shown[("file", "plans")], ["plans/*-PLAN.md", "!plans/*-STATE.md"])
        self.assertEqual(shown[("file", "compileCheck")], "python.exe -W ignore -m unittest discover -s tests-py -p test*.py")
        keys = [path[-1] for path in shown]
        self.assertEqual(len(keys), len(set(keys)), keys)   # (4): no setting twice
        for hidden in ("workspace", "codeRepo", "repo", "worktreePrefix"):
            self.assertNotIn(hidden, keys)
        win.save()
        self.assertEqual(self.split_file.read_text(encoding="utf-8"), self.split_text)
        self.assertFalse(self.split_file.with_name("project.toml.bak").exists())

    def test_changed_repository_is_saved_as_the_code_repository(self):
        other = self.base / "elsewhere"
        other.mkdir()
        win = self.window("HaiveQueen")
        self.set_entry(win, ("place", "repository"), str(other))
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        saved = gui_config.load_raw(self.split_file)
        self.assertEqual(saved["codeRepo"], str(other))
        self.assertEqual(saved["worktreePrefix"], "HQ")   # an existing prefix stays
        self.assertIn("# The plans live apart from the code.", self.split_file.read_text(encoding="utf-8"))

    def test_the_settings_folder_of_a_registered_apiary_cannot_be_dropped(self):
        win = self.window("HaiveQueen")
        self.set_entry(win, ("place", "plansFolder"), "")
        win.save()
        self.assertIn(str(self.plans), win.status.cget("text"))
        self.assertEqual(self.split_file.read_text(encoding="utf-8"), self.split_text)

    def test_repository_entered_as_plans_folder_drops_the_code_repository(self):
        win = self.window("HaiveQueen")
        self.set_entry(win, ("place", "repository"), str(self.plans))
        self.set_entry(win, ("place", "plansFolder"), "")
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertNotIn("codeRepo", gui_config.load_raw(self.split_file))

    def new_apiary(self, win, name, repository, plans=""):
        """Add a Project on the tab Projects and type its name and folders."""
        win.pick_tab("apiaries")
        win.add_entry()
        self.set_entry(win, ("entry", "name"), name)
        self.set_entry(win, ("place", "repository"), str(repository))
        self.set_entry(win, ("place", "plansFolder"), str(plans))

    def test_new_apiary_with_plans_in_its_repository(self):
        repo = self.base / "Fresh Game"
        repo.mkdir()
        win = self.window()
        self.new_apiary(win, "Fresh Game", repo)
        self.assertTrue(win.collect())
        win.target()["file"]["buildEnabled"] = False
        win.build_form()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(gui_config.load_raw(self.base / "config.toml")["projects"][-1], {"name": "Fresh Game", "workspace": str(repo)})
        self.assertEqual(gui_config.load_raw(repo / ".haivequeen" / "project.toml"), {"buildEnabled": False, "worktreePrefix": "Fresh-Game"})
        self.assertEqual(self.conn.execute("SELECT workspace FROM projects WHERE name='Fresh Game'").fetchone()[0], str(repo.resolve()))

    def test_new_apiary_with_a_separate_plans_folder(self):
        repo, plans = self.base / "code2", self.base / "plans2"
        repo.mkdir()
        plans.mkdir()
        win = self.window()
        self.new_apiary(win, "Two", repo, plans)
        win.workspace_entered()
        self.assertEqual(self.shown(win)[("place", "repository")], str(repo))
        self.assertEqual(self.shown(win)[("place", "plansFolder")], str(plans))
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertEqual(gui_config.load_raw(self.base / "config.toml")["projects"][-1], {"name": "Two", "workspace": str(plans)})
        self.assertEqual(gui_config.load_raw(plans / ".haivequeen" / "project.toml"), {"codeRepo": str(repo), "worktreePrefix": "Two"})
        self.assertFalse((repo / ".haivequeen").exists())

    def test_every_field_has_a_label_and_an_explanation_and_no_internal_key_is_shown(self):
        win = self.window("HaiveQueen")
        win.advanced_button.invoke()
        labels = [w.cget("text") for w in descendants(win.form) if w.winfo_class() in ("Label", "Labelframe")]
        self.assertFalse([text for text in labels if text in INTERNAL], labels)
        for path, _getter, _like in win.getters:
            if path == ("file", "rulesOff"):
                continue   # the lines of the Knowledge base carry their own texts
            name = ".".join(path[1:])
            self.assertIn(name, gui_config.SETTING_LABELS, path)
            self.assertIn(name, gui_config.SETTING_HELP, path)
        for label in ("Repository", "Plans folder (empty: the repository)", "Worktree location (empty: beside the repository)"):
            self.assertIn(label, labels)

    def test_a_setting_without_a_field_is_not_shown(self):
        path = self.plain / ".haivequeen" / "project.toml"
        path.write_text(self.plain_text + 'planSuffixRegex = "-?PLAN$"' + chr(10) * 2 + '[minClass]' + chr(10) + 'verify = "medium"' + chr(10), encoding="utf-8")
        win = self.window("Plain")
        win.advanced_button.invoke()
        labels = [w.cget("text") for w in descendants(win.form) if w.winfo_class() in ("Label", "Labelframe")]
        self.assertNotIn("Plan suffix regex", labels)
        self.assertNotIn("Min class", labels)
        self.assertNotIn(gui_config.OTHER_HELP, labels)
        self.assertNotIn(("file", "planSuffixRegex"), self.shown(win))
        self.assertEqual(gui_config.readable("paneCloseDelaySeconds"), "Pane close delay seconds")
        self.assertEqual(gui_config.readable("APIKey"), "API key")

    def test_advanced_settings_are_collapsed_until_opened(self):
        win = self.window("HaiveQueen")
        win.win.update()
        paths = {path for path, _g, _l in win.getters}
        advanced = {path for path in paths if path[0] == "file" and path[1] in ("plans", "worktreeLocation", "buildEnabled", "deployEnabled", "colour", "priority",
                                                                               "buildProcessNames", "ownerTopics", "buildFlowDoc", "integratorInstructions")}
        self.assertEqual(len(advanced), 10)
        self.assertFalse([path for path in advanced if win.rows[path].winfo_ismapped()])
        self.assertTrue(win.rows[("file", "compileCheck")].winfo_ismapped())
        self.assertTrue(win.rows[("file", "integrationBranch")].winfo_ismapped())
        win.advanced_button.invoke()
        win.win.update()
        self.assertTrue(all(win.rows[path].winfo_ismapped() for path in advanced))
        win.select(win.entries.index(("apiaries", 0)))   # the section stays open on the next page
        win.win.update()
        self.assertTrue(win.rows[("file", "plans")].winfo_ismapped())

    def test_github_remote_is_read_from_the_repository(self):
        with mock.patch("haivequeen.publish.github_repository", return_value=None) as remote:
            self.window("HaiveQueen")
        self.assertEqual(remote.call_args[0][0].workspace, self.code)


class FieldSizeTests(ApiaryPageCase):
    """Decision 742: on every tab a field is only as wide as its kind of value needs, at the default size and in a widened window."""

    def fields(self, win):
        """The line and list editors of the shown page with the path they belong to."""
        for path, holder in win.rows.items():
            for widget in holder.winfo_children():
                if widget.winfo_class() in ("Entry", "Text"):
                    yield path, widget

    def check_page(self, win, geometry):
        """No field grows over the full width, every field shows a typical value of its kind, labels and fields stay in one column each."""
        win.win.geometry(geometry)
        win.win.update()
        form = win.form.winfo_width()
        seen = 0
        columns = set()
        for path, widget in self.fields(win):
            if not widget.winfo_ismapped():
                continue
            seen += 1
            width = widget.winfo_width()
            self.assertLessEqual(width, widget.winfo_reqwidth() + 2, (geometry, path))   # never stretched to the window
            self.assertLess(width, form * 0.8 if form > 1200 else form, (geometry, path))
            chars = int(widget.cget("width"))
            self.assertIn(chars, (gui_config.SHORT_WIDTH, gui_config.PATH_WIDTH), path)
            typical = TYPICAL["short" if chars == gui_config.SHORT_WIDTH else "path"]
            self.assertLessEqual(tkinter.font.Font(font=widget.cget("font")).measure(typical), width, (geometry, path))
            if len(path) == 1:   # the top level of a General or provider page
                columns.add(win.rows[path].winfo_rootx())
        self.assertGreater(seen, 0)
        return columns

    def test_fields_on_every_tab_keep_their_width(self):
        win = self.window()
        for geometry in ("1100x700", "1900x1000"):
            win.select(0)
            self.assertEqual(len(self.check_page(win, geometry)), 1, "General: one field column")
            win.select(win.entries.index(("providers", 0)))
            self.assertEqual(len(self.check_page(win, geometry)), 1, "Provider: one field column")
            win.pick_tab("apiaries")
            win.select(win.entries.index(("apiaries", 1)))
            win.advanced_open = True
            win.build_form()
            self.check_page(win, geometry)
            win.win.update()
            columns = {win.rows[path].winfo_rootx() for path in win.rows if path in (("place", "repository"), ("entry", "name")) or (path[0] == "file" and len(path) == 2 and path[1] not in ("plans", "rulesOff", "upstreamRules"))}
            self.assertEqual(len(columns), 1, "Project: labels and fields in one column each")

    def test_short_and_path_fields(self):
        self.assertEqual(gui_config.field_width("integrationBranch", "main"), gui_config.SHORT_WIDTH)
        self.assertEqual(gui_config.field_width("priority", 3), gui_config.SHORT_WIDTH)
        self.assertEqual(gui_config.field_width("nextVersion", ""), gui_config.SHORT_WIDTH)
        self.assertEqual(gui_config.field_width("compileCheck", ""), gui_config.PATH_WIDTH)
        self.assertEqual(gui_config.field_width("worktreeLocation", ""), gui_config.PATH_WIDTH)


if __name__ == "__main__":
    unittest.main()
