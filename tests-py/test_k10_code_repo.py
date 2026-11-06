"""K10, owner decision 740: the release repository, the base version, the tags and releases and the commit of a release are those of the code
repository of the Apiary (codeRepo when it is set, else the workspace), also for the GitHub check that offers the switch. Tested with a workspace and
a codeRepo that are different Git repositories with different remotes."""
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from bzztest import REL, RoundCase
from haivequeen import gui_config, publish, runtime, selfupdate

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
    """Workspace (remote owner/workspace, no pyproject.toml) and codeRepo (remote owner/tool, version 2.0.0) are separate repositories."""

    def setUp(self):
        super().setUp()
        new_repo(self.repo, WORKSPACE_REMOTE)
        self.ws_head = commit(self.repo, "workspace notes")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.ws_head)
        self.code = self.base / "code"
        new_repo(self.code, CODE_REMOTE)
        (self.code / "pyproject.toml").write_text('[project]\nname = "tool"\nversion = "2.0.0"\n', encoding="utf-8")
        git(self.code, "add", "pyproject.toml")
        commit(self.code, "a")
        git(self.code, "switch", "--quiet", "-c", "feature")
        self.tip = commit(self.code, "f1")
        git(self.code, "switch", "--quiet", "main")
        commit(self.code, "b")
        git(self.code, "merge", "--quiet", "--no-ff", "-m", "merge feature", "feature")
        self.merge = git(self.code, "rev-parse", "HEAD")
        git(self.code, "update-ref", "refs/remotes/origin/main", self.merge)
        self.set_project(publishReleases=True, codeRepo=str(self.code))
        self.published, self.asked, self.folders = [], [], []
        self.releases = {}   # owner/name -> latest release version on GitHub

    def set_project(self, **values):
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in values.items()), encoding="utf-8")

    def run_git(self, argv, cwd):
        """Real Git in the folder named by -C; the remotes themselves cannot be reached."""
        if argv[:1] == ["git"] and len(argv) > 2 and argv[1] == "-C":
            self.folders.append(Path(argv[2]).resolve())
        if "ls-remote" in argv or "fetch" in argv:
            return 128, "fatal: unable to access"
        return selfupdate.run_command(argv, cwd)

    def latest(self, repository):
        self.asked.append(repository)
        return self.releases.get(repository)

    def publisher(self, repository, version, commit_id, token):
        self.published.append((repository, version, commit_id))
        self.releases[repository] = version
        return {"tag_name": "v" + version}

    def integrate(self, tip=None):
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=tip or self.tip, plan=REL, status="integrated")
        return publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=self.latest,
                                         publisher=self.publisher, token=lambda run=None: "tok")

    def retry(self):
        return publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=self.latest,
                                     publisher=self.publisher, token=lambda run=None: "tok")


class ReleaseRepositoryTests(CodeRepoCase):

    def test_the_release_goes_to_the_code_repository_with_its_version_and_commit(self):
        done = self.integrate()
        self.assertEqual(done["version"], "2.0.1")
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge)])
        self.assertEqual(self.asked, ["owner/tool"])

    def test_nothing_is_created_or_read_at_the_workspace_remote(self):
        self.integrate()
        self.integrate(commit(self.code, "later"))   # not in origin/main: kept in the queue, never published at a workspace commit
        self.assertNotIn("owner/workspace", [p[0] for p in self.published])
        self.assertNotIn("owner/workspace", self.asked)
        self.assertNotIn(self.repo.resolve(), self.folders)
        self.assertNotIn(self.ws_head, [p[2] for p in self.published])

    def test_releases_in_the_workspace_repository_do_not_count(self):
        """Decision 740 (2): the releases v0.0.1 and v0.0.2 published by mistake at the workspace remote are not counted up from."""
        self.releases["owner/workspace"] = "0.0.2"
        runtime.kv_set(self.conn, "testproj", publish.LAST_KEY, "0.0.2")   # recorded before the repository was stored
        self.assertEqual(self.integrate()["version"], "2.0.1")
        runtime.kv_set(self.conn, "testproj", publish.LAST_KEY, "7.0.0")
        runtime.kv_set(self.conn, "testproj", publish.LAST_REPO_KEY, "owner/workspace")
        git(self.code, "switch", "--quiet", "-c", "feature2")
        tip2 = commit(self.code, "f2")
        git(self.code, "switch", "--quiet", "main")
        git(self.code, "merge", "--quiet", "--no-ff", "-m", "merge feature2", "feature2")
        git(self.code, "update-ref", "refs/remotes/origin/main", git(self.code, "rev-parse", "HEAD"))
        self.releases["owner/tool"] = None   # GitHub does not list 2.0.1 yet
        self.assertEqual(self.integrate(tip2)["version"], "2.0.1")   # 7.0.0 went to another repository
        self.assertEqual(runtime.kv_get(self.conn, "testproj", publish.LAST_REPO_KEY), "owner/tool")

    def test_the_last_release_of_the_code_repository_counts(self):
        self.integrate()
        self.assertEqual(publish.last_published(self.conn, "testproj", "owner/tool"), "2.0.1")
        self.assertEqual(publish.last_published(self.conn, "testproj", "owner/workspace"), "")

    def test_a_queued_entry_keeps_the_commit_read_at_integration(self):
        """The commit read right after the integration (the head, the tip being unknown) is kept for the retry, not read again."""
        runtime.queue_upsert(self.conn, "testproj", "feature", tip="e" * 40, plan=REL, status="integrated")
        self.assertIsNone(publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=self.latest,
                                                    publisher=self.publisher, token=lambda run=None: ""))   # no token: stays queued
        self.assertEqual([(e["commit"], e["repository"], e["version"]) for e in publish.pending_releases(self.conn, "testproj")],
                         [(self.merge, "owner/tool", "2.0.1")])
        git(self.code, "update-ref", "refs/remotes/origin/main", commit(self.code, "moved on"))
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge)])

    def test_a_queued_entry_read_from_the_workspace_is_chosen_again(self):
        """An entry queued by the older code (commit of the workspace, version counted from 0.0.0) is published at the code repository."""
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.tip, plan=REL, status="integrated")
        qid = self.conn.execute("SELECT id FROM queue WHERE project='testproj' AND plan_path=?", (REL,)).fetchone()[0]
        runtime.kv_set(self.conn, "testproj", publish.QUEUE_KEY,
                       json.dumps([{"queue_id": qid, "plan": REL, "tip": self.tip, "commit": self.ws_head, "version": "0.0.3"}]))
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge)])

    def test_without_code_repo_the_workspace_is_the_code_repository(self):
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text("\n".join(l for l in toml.read_text(encoding="utf-8").splitlines() if not l.startswith("codeRepo")) + "\n", encoding="utf-8")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "ws"\nversion = "1.4.0"\n', encoding="utf-8")
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.ws_head, plan=REL, status="integrated")
        publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=self.latest,
                                  publisher=self.publisher, token=lambda run=None: "tok")
        self.assertEqual(self.published, [("owner/workspace", "1.4.1", self.ws_head)])


class SwitchCheckTests(CodeRepoCase):
    """The switch is offered by the GitHub check of the code repository, not of the workspace."""

    def window_class(self):
        for value in vars(gui_config).values():
            if isinstance(value, type) and "github_repository" in vars(value):
                return value
        self.fail("no settings window with github_repository")

    def check(self, shown):
        fake = SimpleNamespace(apiaries=[{"workspace": str(self.repo)}], node=("apiaries", 0))
        return self.window_class().github_repository(fake, shown)

    def test_the_code_repository_remote_decides(self):
        self.assertEqual(self.check({"remoteName": "origin", "codeRepo": str(self.code)}), "owner/tool")
        self.assertEqual(self.check({"remoteName": "origin"}), "owner/workspace")

    def test_a_code_repository_off_github_hides_the_switch_even_with_a_github_workspace(self):
        git(self.code, "remote", "set-url", "origin", "https://gitlab.example/owner/tool.git")
        self.assertIsNone(self.check({"remoteName": "origin", "codeRepo": str(self.code)}))
        self.assertIsNone(publish.github_repository(self.env()))
        self.assertIsNone(self.integrate())
        self.assertEqual(self.published, [])

    def test_a_code_repository_on_github_offers_the_switch_with_a_workspace_off_github(self):
        git(self.repo, "remote", "set-url", "origin", "https://gitlab.example/owner/workspace.git")
        self.assertEqual(self.check({"remoteName": "origin", "codeRepo": str(self.code)}), "owner/tool")
        self.assertEqual(self.integrate()["version"], "2.0.1")


class LegacyRetiredTests(CodeRepoCase):

    def test_a_release_recorded_without_its_repository_does_not_retire_the_checkout_update(self):
        live = self.base / "live"
        new_repo(live, CODE_REMOTE)
        commit(live, "a")
        runtime.kv_set(self.conn, selfupdate.PROJECT, publish.LAST_KEY, "0.0.2")
        self.assertEqual(selfupdate.legacy_retired(self.conn, live, self.run_git), "")
        runtime.kv_set(self.conn, selfupdate.PROJECT, publish.LAST_REPO_KEY, "owner/tool")
        self.assertIn("0.0.2", selfupdate.legacy_retired(self.conn, live, self.run_git))


if __name__ == "__main__":
    unittest.main()
