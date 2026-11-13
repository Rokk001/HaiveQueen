"""P2 of the slim project settings: the project page (P-8), the knowledge base (P-9) and the General and provider fields (P-11)."""
import gc
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, settings, profile, projsettings  # noqa: E402
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

MAIN_PATHS = {("entry", "name"), ("place", "repository"), ("file", "integrationBranch"), ("file", "compileCheck"), ("file", "buildCommand"),
              ("file", "deployCommand")}
ADVANCED_PATHS = {("place", "plansFolder"), ("file", "plans"), ("file", "worktreeLocation"), ("file", "deleteFinishedWorktrees"), ("file", "deleteIntegratedBranches"), ("file", "onEvent", "command"), ("file", "onEvent", "workDir"),
                  ("file", "onEvent", "timeoutSeconds"), ("file", "colour"), ("file", "priority"), ("file", "buildProcessNames"), ("file", "ownerTopics")}
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


if __name__ == "__main__":
    unittest.main()
