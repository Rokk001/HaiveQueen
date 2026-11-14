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

    def test_dashboard_cannot_start_a_live_quota_worker(self):
        from unittest.mock import Mock, patch
        from haivequeen import usage
        with patch.dict(os.environ), patch.object(usage, "_dashboard_refresh_at", 0):
            for key in ("BZZ_DATA_DIR", "BZZ_RUNTIME_OVERRIDE", "BZZ_PROVIDER_LIMITS_FILE"):
                os.environ.pop(key, None)
            connection = Mock()
            usage.schedule_dashboard_refresh(connection, [{"limit": "claude"}])
            connection.execute.assert_not_called()

    def test_cli_runtime_files_in_the_profile_are_live(self):
        """The session logs, caches and state a CLI writes in the profile while a Hive runs are not counted as changes made by a test."""
        for rel in ("profile/claude/.claude.json", "profile/claude/projects/p/s.jsonl", "profile/claude/sessions/1.json", "profile/codex/sessions/2026/a.jsonl",
                    "profile/codex/state_5.sqlite", "profile/opencode/data/opencode/storage/x.json", "profile/kilo/data/kilo/kilo.db"):
            self.assertTrue(isolation.is_live(isolation.Path(rel)), rel)

    def test_files_the_running_loop_and_dashboard_rewrite_are_live(self):
        """The loop's periodic server test result and the dashboard's update check are rewritten while the suite runs: not a change made by a test."""
        for rel in ("server-tests.json", "update-check.json", "removal-locks/branch-1.lock.guard"):
            self.assertTrue(isolation.is_live(isolation.Path(rel)), rel)

    def test_files_haivequeen_writes_itself_are_not_live(self):
        """The settings files HaiveQueen writes into the profile and everything outside the profile still count: a test that writes them is found."""
        for rel in ("profile/claude/settings.json", "profile/codex/config.toml", "config.toml", "logs/notes.txt", "profile/stray.txt"):
            self.assertFalse(isolation.is_live(isolation.Path(rel)), rel)

    def test_removal_lock_files_of_a_live_loop_are_not_a_change(self):
        """The live loop creates removal-locks/<kind>-<hash>.lock.guard files; other files in that folder still count."""
        for rel in ("removal-locks/branch-1.lock.guard", "removal-locks/worktree-2.lock.guard"):
            self.assertTrue(isolation.is_live(isolation.Path(rel)), rel)
        for rel in ("removal-locks/stray.txt", "removal-locks/sub/branch-1.lock.guard"):
            self.assertFalse(isolation.is_live(isolation.Path(rel)), rel)

    def test_concurrent_compile_lock_changes_are_ignored_but_writes_are_guarded(self):
        """A concurrent check changes its locks, while unrelated files and test writes remain visible."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lock = base / "compile-check.lock"
            lock.write_text("pid:1\n", encoding="utf-8")
            before = isolation.snapshot_real([base])
            lock.unlink()
            guard = base / "compile-check.lock.guard"
            guard.write_bytes(b"0")
            other = base / "compile-check.lock.unrelated"
            other.write_text("stray", encoding="utf-8")
            nested = base / "compile-check.lock.directory" / "file"
            nested.parent.mkdir()
            nested.write_text("stray", encoding="utf-8")
            self.assertEqual(isolation.real_folder_changes([base], before), sorted([str(other), str(nested)]))
            for path in (lock, guard):
                self.assertTrue(isolation.guard_event("open", (str(path), "w", 0), self.guarded(base)))
                self.assertTrue(isolation.guard_event("os.remove", (str(path), None), self.guarded(base)))

    def test_a_live_rewrite_of_a_settings_file_is_not_a_change_and_hides_nothing(self):
        """The running loop rewrites profile/codex/config.toml at every Codex start: the snapshot leaves that file out whatever its time, and every
        other file still counts. A test's write to the settings file is found by the audit guard instead, also while a live run folder is written."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            settings = base / "profile" / "codex" / "config.toml"
            settings.parent.mkdir(parents=True)
            settings.write_text("[history]\n", encoding="utf-8")
            stray = base / "config.toml"
            stray.write_text("a", encoding="utf-8")
            before = isolation.snapshot_real([base])
            run = base / "runs" / "HaiveQueen" / "Tara-5" / "rules.md"
            run.parent.mkdir(parents=True)
            run.write_text("rules", encoding="utf-8")   # the live loop writes a run folder at the same moment
            settings.write_text("[history]\npersistence = \"none\"\n", encoding="utf-8")
            stray.write_text("bb", encoding="utf-8")
            self.assertEqual(isolation.real_folder_changes([base], before), [str(stray)])
            self.assertIn(os.path.normcase(str(settings)), isolation.guard_event("open", (str(settings), "w", 0), [os.path.normcase(str(base))]))

    def guarded(self, base):
        return [os.path.normcase(os.path.abspath(str(base)))]

    def test_the_guard_finds_writes_but_not_reads(self):
        """Opening for writing, removing, renaming into, copying into or connecting a database in a guarded folder is a hit; reading is not."""
        base = isolation.Path(isolation.tempfile.gettempdir()) / "HaiveQueen-guard-check"
        g, inside, outside = self.guarded(base), str(base / "profile" / "codex" / "config.toml"), str(base.parent / "elsewhere.txt")
        for event, args in (("open", (inside, "w", 0)), ("open", (inside, "a", 0)), ("open", (inside, "r+", 0)), ("open", (inside, None, os.O_WRONLY | os.O_CREAT)),
                            ("os.remove", (inside, None)), ("os.rename", (outside, inside, None, None)), ("os.mkdir", (str(base / "x"), 0o777, None)),
                            ("shutil.copyfile", (outside, inside)), ("shutil.rmtree", (str(base), None)), ("sqlite3.connect", (str(base / "haivequeen.db"),))):
            self.assertTrue(isolation.guard_event(event, args, g), event)
        for event, args in (("open", (inside, "r", 0)), ("open", (inside, None, os.O_RDONLY)), ("open", (outside, "w", 0)), ("open", (3, "w", 0)),
                            ("os.remove", (outside, None)), ("os.listdir", (inside,))):
            self.assertEqual(isolation.guard_event(event, args, g), [], event)

    def test_the_guard_finds_child_processes_that_would_use_the_real_folders(self):
        """A child process whose BZZ_* or AppData folders, or whose fallback to the home folder, point into a guarded folder is a hit; the redirected
        environment of this session is not."""
        home = isolation.Path(isolation.tempfile.gettempdir()) / "guard-home"
        real = home / "AppData" / "Local" / "HaiveQueen"
        g = self.guarded(real) + self.guarded(home / "AppData" / "Roaming" / "HaiveQueen")
        own = {"LOCALAPPDATA": str(isolation.ROOT / "local"), "APPDATA": str(isolation.ROOT / "roaming"), "XDG_DATA_HOME": str(isolation.ROOT / "local"),
               "XDG_CONFIG_HOME": str(isolation.ROOT / "roaming")}
        self.assertEqual(isolation.guard_event("subprocess.Popen", ("py", ["py"], None, own), g, home), [])
        self.assertTrue(isolation.guard_event("subprocess.Popen", ("py", ["py"], None, dict(own, BZZ_DATA_DIR=str(real))), g, home))
        self.assertTrue(isolation.guard_event("subprocess.Popen", ("py", ["py"], None, {}), g, home))   # no folders at all: the home fallback is real
        self.assertTrue(isolation.guard_event("subprocess.Popen", ("py", ["py"], str(real / "runs"), own), g, home))
        self.assertEqual(isolation.guard_event("subprocess.Popen", ("py", ["py"], None, None), self.guarded(real), home), [])   # None: this session's own environment

    def test_the_audit_hook_records_a_real_write(self):
        """End to end: the installed hook records a write made through the normal file API into a guarded folder, without stopping it."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = isolation.Path(tmp) / "profile" / "codex" / "config.toml"
            target.parent.mkdir(parents=True)
            isolation.GUARDED.append(os.path.normcase(os.path.abspath(tmp)))
            try:
                target.write_text("x", encoding="utf-8")
                found = [h for h in isolation.GUARD_HITS if h.endswith(os.path.normcase(str(target)))]
            finally:
                isolation.GUARDED.pop()
                isolation.GUARD_HITS[:] = [h for h in isolation.GUARD_HITS if os.path.normcase(os.path.abspath(tmp)) not in h]
            self.assertTrue(found)
            self.assertEqual(target.read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()
