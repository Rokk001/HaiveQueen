"""O4: the role files are merged (O-8) and README, docs and help describe the new flow (O-9)."""
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, roles, runtime

ROOT = Path(__file__).resolve().parent.parent
REMOVED = ("buildEnabled", "deployEnabled", "compileCheckEnabled", "buildFlowDoc", "integratorInstructions", "INTEGRATION-BUILD", "INTEGRATION-DEPLOY",
           "switched-on deploy", "switched-on build", "step package")


def role(name):
    return (ROOT / "roles" / (name + ".md")).read_text(encoding="utf-8")


class MergedRoleFiles(unittest.TestCase):
    """O-8: queen.md and bee.md hold the whole text; the start files are gone."""

    def test_start_files_are_deleted_and_the_others_stay(self):
        for name in ("queen-start", "bee-start"):
            self.assertFalse((ROOT / "roles" / (name + ".md")).exists(), name)
        for name in ("queen", "bee", "queen-cut", "ready-check"):
            self.assertTrue((ROOT / "roles" / (name + ".md")).exists(), name)

    def test_language_line_stands_once_in_queen_and_bee(self):
        for name in ("queen", "bee"):
            self.assertEqual(role(name).count("Language:"), 1, name)
            self.assertEqual(role(name).count("{{language}}"), 1, name)
        self.assertTrue(role("queen").startswith("You are the Queen of this Plan."))
        self.assertTrue(role("bee").startswith("You are an Agent."))

    def test_queen_text_has_no_integration_steps_rules_or_compile_check_before_ready(self):
        text = role("queen")
        self.assertNotIn("integration rules", text)
        self.assertNotIn("bzz compile-check", text)
        self.assertNotRegex(text, r"(?i)(?<!not )run the project check")
        self.assertIn("write the completeness answer, call `bzz ready` and end", text)
        self.assertIn("you do not merge the integration branch", text)

    def test_bee_text_keeps_the_compile_check_rule(self):
        self.assertIn("bzz compile-check", role("bee"))

    def test_queen_recovers_requirements_missing_from_the_cut(self):
        text = role("queen")
        self.assertNotIn("or a plan without requirement ids", text)
        self.assertIn("add it to `## Requirements`, cut a package for it and have it implemented", text)


class MergedRolePrompts(RoundCase):
    """O-8: a Queen prompt and an Agent prompt contain the role text once and read no start file."""

    def test_queen_prompt_holds_the_role_text_and_language_once(self):
        names = []
        real = roles.role_text

        def spy(name, values=None, roles_dir=None):
            names.append(name)
            return real(name, values, roles_dir)

        self.plan("READY", [{"id": "P1", "title": "First"}])
        with mock.patch.object(roles, "role_text", spy):
            self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("You are the Queen of this Plan."), 1)
        self.assertEqual(text.count("Language: write all free text"), 1)
        self.assertEqual(text.count("Keep token use economical."), 1)
        self.assertNotIn("integration rules", text)
        self.assertFalse([n for n in names if n.endswith("-start")], names)

    def test_bee_prompt_holds_the_role_text_and_language_once(self):
        text = bees.bee_prompt("Ada", "P1", "do it", ["a"], ["x.py"], [])
        self.assertTrue(text.startswith("You are an Agent."))
        self.assertEqual(text.count("Language: write all free text"), 1)
        self.assertEqual(text.count("Work only package P1: do it"), 1)


class FlowDocs(unittest.TestCase):
    """O-9: README, docs and help describe the new flow and none of the removed names."""

    def texts(self):
        files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "examples").glob("*")), *sorted((ROOT / "roles").glob("*.md"))]
        return {f.name: f.read_text(encoding="utf-8") for f in files if f.is_file()}

    def test_no_removed_switch_field_or_step_package_is_mentioned(self):
        for name, text in self.texts().items():
            for word in REMOVED:
                self.assertNotIn(word, text, "%s mentions %s" % (name, word))

    def test_readme_describes_the_new_flow(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for word in ("Orchestrator", "fix agent", "Build command", "Deploy command"):
            self.assertIn(word, text)
        self.assertNotIn("integrates (git", text)
        self.assertIn("never integrates", text)

    def test_agent_rules_describe_the_new_flow(self):
        text = (ROOT / "docs" / "AGENT-RULES.md").read_text(encoding="utf-8")
        self.assertNotIn("integrates (git", text)
        self.assertNotIn("execute its integration rules", text)
        self.assertIn("fix agent", text)
        self.assertIn("Orchestrator", text)

    def test_help_describes_the_new_flow(self):
        out = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), "ready", "--help"], capture_output=True, text=True).stdout
        flat = " ".join(out.split())
        self.assertIn("Orchestrator", flat)
        self.assertIn("fix agent", flat)
        source = (ROOT / "haivequeen" / "cli.py").read_text(encoding="utf-8")
        for word in REMOVED:
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
