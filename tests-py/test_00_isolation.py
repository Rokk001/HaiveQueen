"""Runs first (discover sorts the files): loads isolation.py before any test can touch the real data folder."""
import os
import unittest

import isolation


class IsolationActiveTest(unittest.TestCase):
    """The folders the tool falls back to are the temp ones."""

    def test_fallback_folders_are_redirected(self):
        """LOCALAPPDATA and APPDATA point into the temp folder of this session."""
        self.assertTrue(os.environ["LOCALAPPDATA"].startswith(str(isolation.ROOT)))
        self.assertTrue(os.environ["APPDATA"].startswith(str(isolation.ROOT)))

    def test_cli_runtime_files_in_the_profile_are_live(self):
        """The session logs, caches and state a CLI writes in the profile while a Hive runs are not counted as changes made by a test."""
        for rel in ("profile/claude/.claude.json", "profile/claude/projects/p/s.jsonl", "profile/claude/sessions/1.json", "profile/codex/sessions/2026/a.jsonl",
                    "profile/codex/state_5.sqlite", "profile/opencode/data/opencode/storage/x.json", "profile/kilo/data/kilo/kilo.db"):
            self.assertTrue(isolation.is_live(isolation.Path(rel)), rel)

    def test_files_haivequeen_writes_itself_are_not_live(self):
        """The settings files HaiveQueen writes into the profile and everything outside the profile still count: a test that writes them is found."""
        for rel in ("profile/claude/settings.json", "profile/codex/config.toml", "config.toml", "logs/notes.txt", "profile/stray.txt"):
            self.assertFalse(isolation.is_live(isolation.Path(rel)), rel)


if __name__ == "__main__":
    unittest.main()
