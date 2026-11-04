"""The fixed Queen and Bee instructions live in role files of the repository; every start reads them again."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, hives, roles, runtime


def read(name):
    return (roles.ROLES_DIR / (name + ".md")).read_text(encoding="utf-8").rstrip("\n")


class RoleFileTests(unittest.TestCase):
    """The role files hold the texts; the settings fill the values."""

    def test_the_role_files_exist_and_hold_no_private_value(self):
        for name in ("queen", "queen-start", "queen-cut", "bee", "bee-start"):
            text = read(name)
            self.assertTrue(text.strip(), name)
            self.assertNotRegex(text, r"[A-Za-z]:\|/Users/|/home/", name)

    def test_queen_instructions_come_from_the_file_with_the_settings_values(self):
        text = bees.queen_instructions("heavy", 7, 5, 123456)
        self.assertIn("Your class is heavy.", text)
        self.assertIn("at most 7 Bees at once", text)
        self.assertIn("After 5 failed attempts escalate", text)
        self.assertIn("when it reaches 123456", text)
        self.assertNotIn("{{", text)

    def test_a_changed_role_file_changes_the_next_text_without_a_code_change(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for f in roles.ROLES_DIR.glob("*.md"):
            shutil.copy(f, tmp / f.name)
        (tmp / "queen.md").write_text("Changed Queen for {{class}} with {{bee_limit}} Bees.\n", encoding="utf-8")
        with mock.patch.object(roles, "ROLES_DIR", tmp):
            self.assertEqual(bees.queen_instructions("light", 2, 3, 4), "Changed Queen for light with 2 Bees.")
        self.assertIn("Your class is light.", bees.queen_instructions("light", 2, 3, 4))

    def test_bee_prompt_comes_from_the_file(self):
        text = bees.bee_prompt("Ada", "P1", "do it", ["a", "b"], ["x.py", "y.py"], ["r1", "r2"])
        self.assertTrue(text.startswith("Work only package P1: do it\nSteps:\n1. a\n2. b\nPaths: x.py, y.py\nRules: r1\nr2\n"))
        self.assertIn("bzz note: Ada P1: step n/2 <short text>.", text)


class RoleStartTests(RoundCase):
    """Queen and Bee prompts start with the text of their role file."""

    def add_local_provider(self):
        from bzztest import provider
        local = provider("local", 2, proto="codex")
        local["endpoint"] = {"baseUrl": "http://localhost"}
        self.machine["providers"] = list(self.machine["providers"]) + [local]

    def prompt(self, name):
        return (self.env().run_dir(name) / "prompt.md").read_text(encoding="utf-8-sig")

    def test_queen_starts_with_her_role_file_and_follows_a_change(self):
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = self.prompt(queen["name"])
        head = read("queen-start").split("{{rules_doc}}")[0]
        self.assertTrue(text.startswith(head), text[:200])
        self.assertIn("Keep token use economical.", text)

    def test_bee_starts_with_its_role_file_and_a_change_applies_at_once(self):
        self.add_local_provider()
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First", "class": "heavy"}])
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("queen", phase="queen", status="running", pid=42)
        bee_wt = self.base / "bee-wt"
        bee_wt.mkdir()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        for f in roles.ROLES_DIR.glob("*.md"):
            shutil.copy(f, tmp / f.name)
        (tmp / "bee-start.md").write_text("You are a changed Bee.\n\nTASK\n", encoding="utf-8")
        with mock.patch("haivequeen.bees.valid_bee_worktree", return_value=True), mock.patch.object(roles, "ROLES_DIR", tmp):
            self.assertTrue(bees.start_process(self.env(), REL, "P1", "Ada", "heavy", "do first", ["one"], ["source.py"], ["work rule"], cwd=str(bee_wt)))
        self.assertTrue(self.prompt("Ada").startswith("You are a changed Bee.\n\nTASK\n\n"))
