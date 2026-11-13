"""Shared project locations and release field derivation."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from haivequeen import settings, publish


class PlaceTests(unittest.TestCase):
    """Decision 741 (1), (2): the folders and the worktree prefix of a Project."""

    def test_repository_and_plans_folder_from_the_settings(self):
        ws, code = str(Path("/a/plans").resolve()), str(Path("/a/code").resolve())
        self.assertEqual(settings.apiary_place(ws, {}), (ws, ""))
        self.assertEqual(settings.apiary_place(ws, {"codeRepo": code}), (code, ws))
        self.assertEqual(settings.apiary_place(ws, {"codeRepo": ws}), (ws, ""))
        self.assertEqual(settings.apiary_place(ws, {"repo": code}), (code, ws))   # project files of the v1 migration
        self.assertEqual(settings.apiary_place("", {}), ("", ""))

    def test_worktree_prefix_follows_the_name(self):
        self.assertEqual(settings.worktree_prefix("HaiveQueen"), "HaiveQueen")
        self.assertEqual(settings.worktree_prefix("My game!"), "My-game")
        self.assertEqual(settings.worktree_prefix("???"), "wt")

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


if __name__ == "__main__":
    unittest.main()
