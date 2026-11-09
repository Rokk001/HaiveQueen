"""P2 of the slim project settings: the project page (P-8), the knowledge base (P-9) and the General and provider fields (P-11)."""
import gc
import subprocess
import sys
import tempfile
import tkinter
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, gui_config, profile, projsettings  # noqa: E402
from haivequeen.i18n import tr  # noqa: E402

CONFIG = '''slots = 2

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--effort", "{effort}"]
slots = 3
use = "preferred"
[providers.classes]
medium = { "model" = "claude-sonnet-5-5", "effort" = "medium" }

[[projects]]
name = "Demo"
workspace = "%s"
'''

MAIN_PATHS = {("entry", "name"), ("place", "repository"), ("file", "integrationBranch"), ("file", "compileCheck")}
ADVANCED_PATHS = {("place", "plansFolder"), ("file", "plans"), ("file", "worktreeLocation"), ("file", "buildEnabled"), ("file", "deployEnabled"),
                  ("file", "deleteFinishedWorktrees"), ("file", "deleteIntegratedBranches"), ("file", "onEvent", "command"), ("file", "onEvent", "workDir"),
                  ("file", "onEvent", "timeoutSeconds"), ("file", "colour"), ("file", "priority"), ("file", "buildProcessNames"), ("file", "ownerTopics"),
                  ("file", "buildFlowDoc"), ("file", "integratorInstructions")}
KNOWLEDGE_PATHS = {("file", "upstreamRules"), ("file", "rulesOff")}


def git(folder, *args):
    """Run git in `folder` with a fixed identity; returns the output."""
    cmd = ["git", "-C", str(folder), "-c", "user.email=t@example.org", "-c", "user.name=t", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def make_repo(folder, files):
    """A git repository at `folder` with the given tracked files {name: text}."""
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "-q", "-b", "main")
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
        git(folder, "add", name)
    git(folder, "commit", "-q", "-m", "init")
    return folder


def descendants(widget):
    """Every widget below `widget`, depth first."""
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class RulesBase(unittest.TestCase):
    """A repository with AGENTS.md and CLAUDE.md and a second repository to act as the upstream."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.repo = make_repo(self.base / "repo", {"AGENTS.md": "agents own rules\n", "CLAUDE.md": "claude own rules\n"})
        self.up = make_repo(self.base / "up", {"AGENTS.md": "agents upstream rules\n", "CLAUDE.md": "claude upstream rules\n"})

    def text(self, settings, protocol=None, cwd=None):
        """What an agent gets inline plus the files its CLI loads itself, as one text."""
        rules = profile.project_rules(settings, self.repo, protocol, cwd)
        return profile.rules_text(rules), rules["byProvider"]

    def add_upstream_remote(self, fetch):
        """Name the second repository as the Git remote upstream of the first; fetch it when asked."""
        git(self.repo, "remote", "add", "upstream", str(self.up))
        if fetch:
            git(self.repo, "fetch", "-q", "upstream")


class KnowledgeBaseTest(RulesBase):
    """P-9: each rule file with path, tokens and a switch; a switched-off file reaches no agent."""

    def test_each_rule_file_is_listed_with_path_tokens_and_switch(self):
        entries = profile.rule_entries({}, self.repo)
        self.assertEqual([e["label"] for e in entries], ["AGENTS.md", "CLAUDE.md"])
        for entry in entries:
            self.assertEqual(entry["path"], str(self.repo / entry["label"]))
            self.assertIsInstance(entry["tokens"], int)
            self.assertGreater(entry["tokens"], 0)
            self.assertTrue(entry["on"])

    def test_switched_off_file_reaches_no_agent(self):
        settings = {"rulesOff": ["CLAUDE.md"]}
        for protocol in ("claude", "codex", None):
            text, by_provider = self.text(settings, protocol, self.repo)
            self.assertNotIn("claude own rules", text, protocol)
            self.assertNotIn("CLAUDE.md", by_provider, protocol)
        text, _by = self.text(settings, "codex")
        self.assertIn("claude own rules".replace("claude", "agents"), text)
        self.assertFalse([e for e in profile.rule_entries(settings, self.repo) if e["label"] == "CLAUDE.md" and e["on"]])

    def test_everything_on_by_default(self):
        text, _by = self.text({})
        self.assertIn("agents own rules", text)
        self.assertIn("claude own rules", text)

    def test_file_loaded_by_a_cli_itself_is_named_and_stays_switchable(self):
        entries = {e["label"]: e for e in profile.rule_entries({}, self.repo)}
        self.assertIn("Claude Code", entries["CLAUDE.md"]["note"])
        self.assertIn("Codex", entries["AGENTS.md"]["note"])
        self.assertNotIn("Codex", entries["CLAUDE.md"]["note"])
        # the CLI loads it itself, so it is not copied; the switch still works for the other CLIs
        text, by_provider = self.text({}, "claude", self.repo)
        self.assertEqual(by_provider, ["CLAUDE.md"])
        self.assertNotIn("claude own rules", text)
        self.assertIn("agents own rules", text)
        text, by_provider = self.text({"rulesOff": ["AGENTS.md"]}, "claude", self.repo)
        self.assertNotIn("agents own rules", text)

    def test_upstream_switch_is_off_by_default_with_only_a_remote(self):
        self.add_upstream_remote(fetch=True)
        state = profile.upstream_state({}, self.repo)
        self.assertTrue(state["usable"])
        self.assertFalse(state["on"])
        self.assertEqual(state["source"], "remote")
        text, _by = self.text({})
        self.assertNotIn("upstream rules", text)
        self.assertEqual([e["label"] for e in profile.rule_entries({}, self.repo)], ["AGENTS.md", "CLAUDE.md"])

    def test_upstream_remote_files_are_read_when_switched_on(self):
        self.add_upstream_remote(fetch=True)
        settings = {"upstreamRules": True}
        text, _by = self.text(settings)
        self.assertIn("agents upstream rules", text)
        self.assertIn("claude upstream rules", text)
        labels = [e["label"] for e in profile.rule_entries(settings, self.repo)]
        self.assertEqual(labels, ["AGENTS.md", "CLAUDE.md", "upstream AGENTS.md", "upstream CLAUDE.md"])
        text, _by = self.text({"upstreamRules": True, "rulesOff": ["upstream CLAUDE.md"]})
        self.assertIn("agents upstream rules", text)
        self.assertNotIn("claude upstream rules", text)

    def test_switch_is_greyed_without_remote_and_folder(self):
        state = profile.upstream_state({"upstreamRules": True}, self.repo)
        self.assertFalse(state["usable"])
        self.assertFalse(state["on"])
        self.assertTrue(state["note"])
        text, _by = self.text({"upstreamRules": True})
        self.assertNotIn("upstream rules", text)

    def test_folder_sets_the_switch_on_and_wins_over_a_remote(self):
        other = make_repo(self.base / "other", {"AGENTS.md": "agents folder rules\n"})
        settings = {"upstreamRepo": str(other)}
        state = profile.upstream_state(settings, self.repo)
        self.assertTrue(state["usable"] and state["on"])
        self.assertEqual(state["source"], "folder")
        self.add_upstream_remote(fetch=True)
        state = profile.upstream_state(settings, self.repo)
        self.assertEqual(state["source"], "folder")
        text, _by = self.text(settings)
        self.assertIn("agents folder rules", text)
        self.assertNotIn("agents upstream rules", text)
        text, _by = self.text({**settings, "upstreamRules": False})
        self.assertNotIn("agents folder rules", text)

    def test_unfetched_remote_gives_no_rules_and_says_so(self):
        self.add_upstream_remote(fetch=False)
        settings = {"upstreamRules": True}
        state = profile.upstream_state(settings, self.repo)
        self.assertTrue(state["usable"] and state["on"])
        self.assertFalse(state["fetched"])
        self.assertEqual(state["note"], "not fetched yet")
        text, _by = self.text(settings)
        self.assertNotIn("upstream rules", text)

    def test_migration_turns_the_switch_on_for_an_upstream_folder(self):
        new, _removed = projsettings.migrate_data({"name": "x", "upstreamRepo": str(self.up)}, self.base, {})
        self.assertEqual(new["upstreamRepo"], str(self.up))   # the folder stays and sets the switch on
        self.assertTrue(profile.upstream_state(new, self.repo)["on"])
        self.assertIn("AGENTS.md", {e["name"] for e in profile.rule_entries(new, self.repo) if e["upstream"]})
        again, removed = projsettings.migrate_data(new, self.base, {})
        self.assertEqual(again, new)
        self.assertEqual(removed, [])

    def test_the_new_keys_survive_a_migration(self):
        new, removed = projsettings.migrate_data({"name": "x", "upstreamRules": False, "rulesOff": ["CLAUDE.md"]}, self.base, {})
        self.assertEqual((new["upstreamRules"], new["rulesOff"]), (False, ["CLAUDE.md"]))
        self.assertEqual(removed, [])

    def test_agent_rules_document_says_all_switched_on_files(self):
        text = (ROOT / "docs" / "AGENT-RULES.md").read_text(encoding="utf-8")
        self.assertIn("switched-on", text)


class WindowCase(unittest.TestCase):
    """A settings folder with one Project whose repository holds rule files."""

    SETTINGS = 'name = "Demo"\n'

    def setUp(self):
        try:
            self.root = tkinter.Tk()
            self.root.attributes("-alpha", 0.0)
        except tkinter.TclError as exc:
            self.skipTest("no display: %s" % exc)
        self.addCleanup(gc.collect)   # Tk objects are freed in the main thread, never by the collector in a reading thread
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
        self.repo = make_repo(self.base / "Demo-Repo", {"AGENTS.md": "agents own rules\n" * 10, "CLAUDE.md": "claude own rules\n"})
        (self.repo / ".haivequeen").mkdir()
        self.file = self.repo / ".haivequeen" / "project.toml"
        self.file.write_text(self.SETTINGS, encoding="utf-8")
        esc = str(self.repo).replace("\\", "\\\\")
        (self.base / "config.toml").write_text(CONFIG % esc, encoding="utf-8")
        self.conn = db.connect(self.base / "o.db")
        self.addCleanup(self.conn.close)
        db.upsert_project(self.conn, "Demo", str(self.repo), "white", 1)

    def window(self, page=None):
        """The settings window, showing the Project page when `page` is "project"."""
        win = gui_config.ConfigWindow(self.root, self.conn, self.base, wait=True)
        if page == "project":
            win.pick_tab("apiaries")
            win.select(win.entries.index(("apiaries", 0)))
        return win

    def paths(self, win):
        """The set of paths of the editors on the shown page."""
        return {path for path, _getter, _like in win.getters}

    def entry_of(self, win, path):
        """The line editor of `path`."""
        return next(w for w in win.rows[path].winfo_children() if w.winfo_class() == "Entry")

    def set_entry(self, win, path, text):
        """Type `text` into the line editor of `path`."""
        widget = self.entry_of(win, path)
        widget.delete(0, "end")
        widget.insert(0, text)


class ProjectPageTest(WindowCase):
    """P-8: the project page shows exactly the fields of P-1 and P-2."""

    def test_page_shows_exactly_the_main_and_advanced_fields(self):
        win = self.window("project")
        self.assertEqual(self.paths(win) - KNOWLEDGE_PATHS, MAIN_PATHS | ADVANCED_PATHS)

    def test_every_field_has_a_label_and_one_line_of_help(self):
        win = self.window("project")
        texts = {w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"}
        for path in MAIN_PATHS | ADVANCED_PATHS:
            name = ".".join(path[1:])
            self.assertIn(name, gui_config.SETTING_LABELS, name)
            self.assertIn(name, gui_config.SETTING_HELP, name)
            self.assertIn(tr(gui_config.SETTING_LABELS[name]), texts, name)
            self.assertIn(tr(gui_config.SETTING_HELP[name]), texts, name)

    def test_no_generic_lines_and_no_add_rows(self):
        self.file.write_text(self.SETTINGS + 'owner = "Someone"\nviews = {queue = "q.md"}\nmadeUp = 3\npaneCloseDelaySeconds = 2\n', encoding="utf-8")
        win = self.window("project")
        texts = {w.cget("text") for w in descendants(win.form) if w.winfo_class() in ("Label", "Button")}
        self.assertNotIn(tr(gui_config.OTHER_HELP), texts)
        self.assertNotIn("new setting", texts)
        self.assertNotIn("Add", texts)
        self.assertTrue({"owner", "views", "madeUp", "paneCloseDelaySeconds"}.isdisjoint({p[1] for p in self.paths(win) if p[0] == "file"}))
        self.assertEqual(self.paths(win) - KNOWLEDGE_PATHS, MAIN_PATHS | ADVANCED_PATHS)

    def test_defaults_name_from_the_folder_and_branch_main(self):
        self.file.write_text("", encoding="utf-8")
        (self.base / "config.toml").write_text(CONFIG.replace('name = "Demo"\nworkspace', 'name = ""\nworkspace') % str(self.repo).replace("\\", "\\\\"), encoding="utf-8")
        win = self.window("project")
        shown = {path: getter() for path, getter, _like in win.getters}
        self.assertEqual(shown[("file", "integrationBranch")], "main")
        self.assertEqual(shown[("place", "repository")], str(self.repo))
        self.assertEqual(shown[("file", "compileCheck")], "")
        self.assertEqual(shown[("entry", "name")], "Demo-Repo")

    def test_plans_field_holds_patterns_and_exclusions_and_replaces_the_old_keys(self):
        self.file.write_text(self.SETTINGS + 'planGlobs = ["plans/*.md"]\nplanExclude = ["plans/*-STATE.md"]\n', encoding="utf-8")
        win = self.window("project")
        shown = {path: getter() for path, getter, _like in win.getters}
        self.assertEqual(shown[("file", "plans")], ["plans/*.md", "!plans/*-STATE.md"])
        self.assertTrue(win.collect())
        self.assertNotIn("plans", win.apiaries[0]["file"])   # nothing changed, nothing written
        text = win.rows[("file", "plans")].winfo_children()[0]
        text.insert("end", "\nextra/*.md")
        self.assertTrue(win.collect())
        data = win.apiaries[0]["file"]
        self.assertEqual(data["plans"], ["plans/*.md", "!plans/*-STATE.md", "extra/*.md"])
        self.assertNotIn("planGlobs", data)
        self.assertNotIn("planExclude", data)

    def test_remote_field_only_when_it_has_to_be_chosen(self):
        win = self.window("project")
        self.assertNotIn(("file", "remoteName"), self.paths(win))
        git(self.repo, "remote", "add", "alpha", str(self.base))
        git(self.repo, "remote", "add", "beta", str(self.base))
        win = self.window("project")
        self.assertIn(("file", "remoteName"), self.paths(win))
        self.assertIn("remoteName", gui_config.SETTING_HELP)

    def test_main_part_is_the_four_fields_of_p1(self):
        self.assertEqual({p[-1] for p in MAIN_PATHS}, {"name", "repository", "integrationBranch", "compileCheck"})
        win = self.window("project")
        advanced = set()
        for path in ADVANCED_PATHS:
            holder = win.rows[path]
            parent = holder
            while parent is not None and parent is not win.advanced_box:
                parent = parent.master
            advanced.add(parent is win.advanced_box)
        self.assertEqual(advanced, {True})
        for path in MAIN_PATHS:
            parent = win.rows[path]
            while parent is not None and parent is not win.advanced_box:
                parent = parent.master
            self.assertIsNone(parent, path)


class KnowledgeWindowTest(WindowCase):
    """P-9 on the project page."""

    def test_lines_show_path_tokens_and_a_switch(self):
        win = self.window("project")
        self.assertEqual(sorted(win.rule_vars), ["AGENTS.md", "CLAUDE.md"])
        for label, text in win.rule_lines.items():
            self.assertIn(str(self.repo / label), text.cget("text"))
            self.assertRegex(text.cget("text"), r"\d+ tokens")
        self.assertIn("Claude Code", win.rule_lines["CLAUDE.md"].cget("text"))
        self.assertTrue(all(v.get() for v in win.rule_vars.values()))
        self.assertIn("own role texts", " ".join(w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"))

    def test_switching_a_file_off_is_written_as_a_list(self):
        win = self.window("project")
        win.rule_vars["CLAUDE.md"].set(False)
        self.assertTrue(win.collect())
        self.assertEqual(win.apiaries[0]["file"]["rulesOff"], ["CLAUDE.md"])
        win.rule_vars["CLAUDE.md"].set(True)
        self.assertTrue(win.collect())
        self.assertNotIn("rulesOff", win.apiaries[0]["file"])

    def test_upstream_switch_is_greyed_without_remote_and_folder(self):
        win = self.window("project")
        self.assertEqual(str(win.upstream_check.cget("state")), "disabled")
        self.assertFalse(win.upstream_var.get())
        self.assertTrue(win.upstream_note.cget("text"))

    def test_upstream_switch_is_off_by_default_and_usable_with_a_remote(self):
        up = make_repo(self.base / "up", {"AGENTS.md": "agents upstream rules\n"})
        git(self.repo, "remote", "add", "upstream", str(up))
        git(self.repo, "fetch", "-q", "upstream")
        win = self.window("project")
        self.assertEqual(str(win.upstream_check.cget("state")), "normal")
        self.assertFalse(win.upstream_var.get())
        self.assertNotIn("upstream AGENTS.md", win.rule_vars)
        win.upstream_var.set(True)
        self.assertTrue(win.collect())
        self.assertIs(win.apiaries[0]["file"]["upstreamRules"], True)

    def test_upstream_folder_shows_the_switch_on_and_its_files(self):
        up = make_repo(self.base / "up", {"AGENTS.md": "agents upstream rules\n"})
        self.file.write_text(self.SETTINGS + 'upstreamRepo = "%s"\n' % str(up).replace("\\", "\\\\"), encoding="utf-8")
        win = self.window("project")
        self.assertTrue(win.upstream_var.get())
        self.assertIn("upstream AGENTS.md", win.rule_vars)

    def test_unfetched_remote_says_so(self):
        up = make_repo(self.base / "up", {"AGENTS.md": "agents upstream rules\n"})
        git(self.repo, "remote", "add", "upstream", str(up))
        self.file.write_text(self.SETTINGS + "upstreamRules = true\n", encoding="utf-8")
        win = self.window("project")
        self.assertIn("not fetched yet", win.upstream_note.cget("text"))


class GeneralAndProviderTest(WindowCase):
    """P-11: General shows Owner name, Queen class and Agents per Queen; every provider shows Context limit in Use."""

    def test_general_fields(self):
        win = self.window()
        self.assertEqual(win.node, ("general", None))
        shown = {path: getter() for path, getter, _like in win.getters}
        self.assertEqual((shown[("owner",)], shown[("queenClass",)], shown[("beesPerQueen",)]), ("", "medium", "3"))
        texts = {w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"}
        for key, label in (("owner", "Owner name"), ("queenClass", "Queen class"), ("beesPerQueen", "Agents per Queen")):
            self.assertEqual(tr(gui_config.GENERAL_LABELS[key]), tr(label))
            self.assertIn(tr(label), texts)
            self.assertIn(tr(gui_config.GENERAL_HELP[key]), texts)
        self.assertIn("queenClass", gui_config.CHOICES)

    def test_general_fields_are_saved_as_typed(self):
        win = self.window()
        self.set_entry(win, ("owner",), "Alex")
        self.set_entry(win, ("beesPerQueen",), "5")
        self.assertTrue(win.collect())
        self.assertEqual((win.data["owner"], win.data["beesPerQueen"]), ("Alex", 5))
        self.assertNotIn("queenClass", win.data)   # unchanged default stays unwritten

    def test_provider_shows_context_limit_in_use(self):
        win = self.window()
        win.select(1)
        path = ("contextLimit",)
        self.assertIn(path, self.paths(win))
        self.assertTrue(win.in_frame(path, win.use_frame))
        self.assertEqual({p: g() for p, g, _l in win.getters}[path], "300000")
        self.assertTrue(win.notes["contextLimit"])
        self.set_entry(win, path, "200000")
        self.assertTrue(win.collect())
        self.assertEqual(win.data["providers"][0]["contextLimit"], 200000)

    def test_provider_context_limit_shows_the_value_it_has(self):
        text = (self.base / "config.toml").read_text(encoding="utf-8").replace('slots = 3\n', 'slots = 3\ncontextLimit = 123000\n')
        (self.base / "config.toml").write_text(text, encoding="utf-8")
        win = self.window()
        win.select(1)
        self.assertEqual({p: g() for p, g, _l in win.getters}[("contextLimit",)], "123000")


if __name__ == "__main__":
    unittest.main()
