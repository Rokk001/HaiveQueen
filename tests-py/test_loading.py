"""What Queen and Agent load: the complete project rules (checked-in instruction files, upstream ones only when set) plus their own parts."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bzztest  # noqa: F401  (redirects the real folders)
from bzztest import REL, RoundCase, provider
from haivequeen import bees, db, hives, profile, runtime
from test_agent_runner import RunnerBase, claude_stream


def git_repo(root, files, tracked=True):
    """Make `root` a git repository; `files` (name -> text) are written, and checked in when `tracked`."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    if tracked and files:
        subprocess.run(["git", "-C", str(root), "add", "--"] + list(files), check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x"], check=True)
    return root


class ProjectRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def labels(self, rules):
        return [label for label, _ in rules["inline"]]

    def test_only_checked_in_instruction_files_are_rules(self):
        repo = git_repo(self.base / "repo", {"AGENTS.md": "agents rule", "CLAUDE.md": "claude rule", "README.md": "readme text"})
        (repo / "NOTES.md").write_text("internal notes", encoding="utf-8")
        (repo / "planning").mkdir()
        (repo / "planning" / "AGENTS.md").write_text("internal planning rule", encoding="utf-8")
        text = profile.rules_text(profile.project_rules({}, repo))
        self.assertIn("agents rule", text)
        self.assertIn("claude rule", text)
        for absent in ("readme text", "internal notes", "internal planning rule"):
            self.assertNotIn(absent, text)

    def test_an_untracked_workspace_file_is_no_rule(self):
        ws = git_repo(self.base / "ws", {}, tracked=False)
        (ws / "AGENTS.md").write_text("internal workspace rule", encoding="utf-8")
        self.assertEqual(profile.rules_text(profile.project_rules({}, ws)), "")

    def test_upstream_rules_only_when_the_setting_names_one(self):
        repo = git_repo(self.base / "repo", {"AGENTS.md": "own rule"})
        up = git_repo(self.base / "up", {"AGENTS.md": "upstream rule"})
        self.assertNotIn("upstream rule", profile.rules_text(profile.project_rules({}, repo)))
        both = profile.project_rules({profile.UPSTREAM_KEY: str(up)}, repo)
        self.assertEqual(self.labels(both), ["AGENTS.md", "upstream AGENTS.md"])
        self.assertIn("upstream rule", profile.rules_text(both))

    def test_a_file_the_provider_loads_itself_is_not_loaded_twice(self):
        repo = git_repo(self.base / "repo", {"AGENTS.md": "agents rule", "CLAUDE.md": "claude rule"})
        claude = profile.project_rules({}, repo, "claude", repo)
        self.assertEqual(self.labels(claude), ["AGENTS.md"])
        self.assertEqual(claude["byProvider"], ["CLAUDE.md"])
        codex = profile.project_rules({}, repo, "codex", repo)
        self.assertEqual(self.labels(codex), ["CLAUDE.md"])
        elsewhere = profile.project_rules({}, repo, "claude", self.base)   # the provider does not run in the repository: it loads nothing itself
        self.assertEqual(self.labels(elsewhere), ["AGENTS.md", "CLAUDE.md"])

    def test_the_same_text_counts_once(self):
        repo = git_repo(self.base / "repo", {"AGENTS.md": "one rule", "CLAUDE.md": "one rule\n"})
        self.assertEqual(profile.rules_text(profile.project_rules({}, repo)).count("one rule"), 1)


class QueenLoadTest(RoundCase):
    providers = [provider("codex", 2, proto="codex")]

    def setUp(self):
        super().setUp()
        git_repo(self.repo, {"AGENTS.md": "RULE-AGENTS", "CLAUDE.md": "RULE-CLAUDE", "README.md": "README-TEXT"})
        (self.repo / "INTERNAL.md").write_text("INTERNAL-TEXT", encoding="utf-8")

    def queen_prompt(self):
        self.plan("READY", [{"id": "P1", "title": "First"}])
        (self.repo / REL.replace("-PLAN.md", "-STATE.md")).write_text("# State\n\nSTATE-LINE\n\n## Handoff Old\n\nHANDOFF-LINE\n\n## Next\n\nAFTER-LINE\n", encoding="utf-8")
        self.agent("Old", phase="queen", status="done", person="Old")
        runtime.add_decision(self.conn, "testproj", "owner", "DECISION-LINE", plan=None)
        qid = db.add_question(self.conn, "testproj", REL, "item", "QUESTION-LINE", "rec")
        db.answer_question(self.conn, qid, "ANSWER-LINE", "owner")
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen" and a["name"] != "Old")
        return (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig")

    def test_context_scopes_and_deduplicates_answers(self):
        runtime.add_decision(self.conn, "testproj", "owner", "OWN-DECISION", REL)
        runtime.add_decision(self.conn, "testproj", "owner", "GLOBAL-DECISION", None)
        runtime.add_decision(self.conn, "testproj", "owner", "OTHER-DECISION", "other.md")
        runtime.add_decision(self.conn, "otherproj", "owner", "FOREIGN-DECISION", None)
        for plan, question, answer, global_answer in (
            (REL, "OWN-QUESTION", "OWN-ANSWER", False),
            ("other.md", "GLOBAL-QUESTION", "GLOBAL-ANSWER", True),
            ("other.md", "OTHER-QUESTION", "OTHER-ANSWER", False),
        ):
            qid = db.add_question(self.conn, "testproj", plan, question, question, "")
            db.answer_question(self.conn, qid, answer, "owner", apiary=global_answer)
        text = profile.queen_load(self.conn, "testproj", self.repo, REL)
        for value in ("OWN-DECISION", "GLOBAL-DECISION", "OWN-QUESTION", "OWN-ANSWER", "GLOBAL-QUESTION", "GLOBAL-ANSWER"):
            self.assertEqual(text.count(value), 1, value)
        for value in ("OTHER-DECISION", "FOREIGN-DECISION", "OTHER-QUESTION", "OTHER-ANSWER"):
            self.assertNotIn(value, text)

    def test_queen_gets_exactly_her_parts(self):
        text = self.queen_prompt()
        self.assertIn("You are the Queen", text)                       # her Queen role file
        for part in ("RULE-AGENTS", "RULE-CLAUDE", "Dummy plan", "STATE-LINE", "AFTER-LINE", "HANDOFF-LINE", "DECISION-LINE", "ANSWER-LINE"):
            self.assertIn(part, text, part)
        for absent in ("README-TEXT", "INTERNAL-TEXT"):
            self.assertNotIn(absent, text)
        self.assertEqual(text.count("HANDOFF-LINE"), 1)
        self.assertEqual(text.count("RULE-AGENTS"), 1)


class BeeLoadTest(RoundCase):
    def test_bee_gets_the_complete_rules_whatever_the_queen_passes(self):
        self.mock_agent_worktrees()
        git_repo(self.repo, {"AGENTS.md": "RULE-AGENTS", "CLAUDE.md": "RULE-CLAUDE", "README.md": "README-TEXT"})
        local = provider("local", 2, proto="codex")
        local["endpoint"] = {"baseUrl": "http://localhost"}
        self.machine["providers"] = list(self.machine["providers"]) + [local]
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First", "class": "heavy"}])
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("queen", phase="queen", status="running", pid=42)
        bee_wt = self.base / "bee-wt"
        bee_wt.mkdir()
        with mock.patch("haivequeen.bees.valid_bee_worktree", return_value=True), mock.patch("haivequeen.bees.prepare_agent_worktree", return_value=(str(bee_wt), "plan-p1")):
            started = bees.start_process(self.env(), REL, "P1", "Ada", "heavy", "do first", ["one"], ["source.py"], ["ONLY-THIS-RULE"], cwd=str(bee_wt))
        self.assertTrue(started)
        text = (self.env().run_dir("Ada") / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertIn("You are an Agent", text)
        self.assertIn("do first", text)
        for part in ("RULE-AGENTS", "RULE-CLAUDE"):
            self.assertIn(part, text)
        for absent in ("ONLY-THIS-RULE", "README-TEXT", "Dummy plan"):
            self.assertNotIn(absent, text)


class ClaudeRulesFileTest(RunnerBase):
    def test_claude_bee_and_queen_get_the_complete_rules_in_rules_md(self):
        git_repo(self.ws, {"AGENTS.md": "RULE-AGENTS", "CLAUDE.md": "RULE-CLAUDE", "README.md": "README-TEXT"})
        for name, phase in (("Ada", "bee"), ("Bea", "queen")):
            self.prepare(name)
            result = self.run_runner(name, "claude", claude_stream(), phase=phase)
            self.assertEqual(result.returncode, 0, result.stderr)
            rules = (self.runs / name / "rules.md").read_text(encoding="utf-8")
            self.assertIn("RULE-AGENTS", rules)
            self.assertNotIn("README-TEXT", rules)


if __name__ == "__main__":
    unittest.main()
