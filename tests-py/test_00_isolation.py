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

    def test_a_settings_file_rewritten_by_a_live_agent_start_is_not_a_change(self):
        """The running loop rewrites profile/codex/config.toml at every Codex start (and Codex adds its own entries while it runs): when a run folder
        of the real data folder was written at about the same time, the change is the live loop's, not a test's. Without such a run it still counts."""
        import tempfile
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = base / "profile" / "codex" / "config.toml"
            settings.parent.mkdir(parents=True)
            settings.write_text("[history]\n", encoding="utf-8")
            stray = base / "config.toml"
            stray.write_text("a", encoding="utf-8")
            before, since = isolation.snapshot_real([base]), time.time_ns()
            settings.write_text("[history]\npersistence = \"none\"\n", encoding="utf-8")
            stray.write_text("bb", encoding="utf-8")
            self.assertEqual(isolation.real_folder_changes([base], before, since), sorted([str(settings), str(stray)]))   # nothing ran: a test wrote both
            run = base / "runs" / "HaiveQueen" / "Tara-5" / "rules.md"
            run.parent.mkdir(parents=True)
            run.write_text("rules", encoding="utf-8")
            self.assertEqual(isolation.real_folder_changes([base], before, since), [str(stray)])   # the settings file was the live start's
            old = time.time() - 3600
            os.utime(run, (old, old))   # a run folder written long before does not explain the change
            self.assertEqual(isolation.real_folder_changes([base], before, since), sorted([str(settings), str(stray)]))


if __name__ == "__main__":
    unittest.main()
