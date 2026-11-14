"""Repository resolution for release operations and the legacy update fallback."""
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from bzztest import RoundCase
from haivequeen import git_provider, publish, runtime, selfupdate

WORKSPACE_REMOTE = "https://github.com/owner/workspace.git"
CODE_REMOTE = "https://github.com/owner/tool.git"


def git(folder, *args):
    res = subprocess.run(["git", "-C", str(folder)] + list(args), capture_output=True, text=True, check=True)
    return res.stdout.strip()


def new_repo(folder, remote):
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "--quiet", "-b", "main")
    git(folder, "config", "user.email", "t@example.invalid")
    git(folder, "config", "user.name", "t")
    git(folder, "config", "commit.gpgsign", "false")
    git(folder, "remote", "add", "origin", remote)


def commit(folder, name):
    git(folder, "commit", "--quiet", "--allow-empty", "-m", name)
    return git(folder, "rev-parse", "HEAD")


class CodeRepoCase(RoundCase):
    def setUp(self):
        super().setUp()
        new_repo(self.repo, WORKSPACE_REMOTE)
        self.code = self.base / "code"
        new_repo(self.code, CODE_REMOTE)
        self.set_project(codeRepo=str(self.code))

    def set_project(self, **values):
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in values.items()), encoding="utf-8")


class ReleaseRepositoryTests(CodeRepoCase):
    def test_publishing_adapter_uses_the_code_repository_remote(self):
        git(self.repo, "remote", "set-url", "origin", "https://gitlab.com/owner/workspace.git")
        provider, errors = git_provider.for_project(self.env())
        self.assertEqual(errors, [])
        self.assertEqual((provider.name, provider.repository), ("github", "owner/tool"))

    def test_publishing_adapter_uses_the_workspace_when_code_repo_is_unset(self):
        env = SimpleNamespace(workspace=self.repo, settings={"remoteName": "origin"})
        provider, errors = git_provider.for_project(env)
        self.assertEqual(errors, [])
        self.assertEqual((provider.name, provider.repository), ("github", "owner/workspace"))

    def test_legacy_github_detection_uses_code_repo_when_configured(self):
        git(self.code, "remote", "set-url", "origin", "https://gitlab.com/owner/tool.git")
        self.assertIsNone(publish.github_repository(self.env()))
        env = SimpleNamespace(workspace=self.repo, settings={"remoteName": "origin"})
        self.assertEqual(publish.github_repository(env), "owner/workspace")


class LegacyRetiredTests(CodeRepoCase):

    def test_a_release_recorded_without_its_repository_does_not_retire_the_checkout_update(self):
        live = self.base / "live"
        new_repo(live, CODE_REMOTE)
        commit(live, "a")
        runtime.kv_set(self.conn, selfupdate.PROJECT, publish.LAST_KEY, "0.0.2")
        self.assertEqual(selfupdate.legacy_retired(self.conn, live), "")
        runtime.kv_set(self.conn, selfupdate.PROJECT, publish.LAST_REPO_KEY, "owner/tool")
        self.assertIn("0.0.2", selfupdate.legacy_retired(self.conn, live))


if __name__ == "__main__":
    unittest.main()
