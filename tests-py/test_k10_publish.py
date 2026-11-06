"""K10, H-43 and H-46(k): the Apiary switch "Publish release at integration", the version count-up and the release published by HaiveQueen itself."""
import json
import subprocess
import unittest
from pathlib import Path
from urllib.error import HTTPError

from bzztest import REL, RoundCase
from haivequeen import db, gui_config, publish, releases, runtime, scheduler, selfupdate

SHA = "c" * 40
SHA2 = "d" * 40


class PublishCase(RoundCase):
    """One Apiary with a GitHub remote; publishing and lookups are recorded instead of sent."""

    remote = "https://github.com/owner/tool.git"

    def setUp(self):
        super().setUp()
        self.published = []
        self.latest = None
        self.head = SHA
        self.error = None
        (self.repo / "pyproject.toml").write_text('[project]\nname = "tool"\nversion = "2.0.0"\n', encoding="utf-8")

    def set_project(self, **values):
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in values.items()), encoding="utf-8")

    def run_git(self, argv, cwd):
        if "get-url" in argv:
            return 0, self.remote + "\n"
        if "ls-remote" in argv:
            return 0, "%s\trefs/heads/main\n" % self.head
        return 1, "unexpected " + " ".join(argv)

    def publisher(self, repository, version, commit, token):
        if self.error:
            raise self.error
        self.published.append((repository, version, commit, token))
        self.latest = version
        return {"tag_name": "v" + version}

    def integrate(self, tip="t1"):
        """Register and integrate a queue row of the plan, then run the step of `bzz integration release`."""
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=tip, plan=REL, status="integrated")
        return self.after()

    def after(self):
        return publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=lambda repo: self.latest,
                                         publisher=self.publisher, token=lambda run=None: "tok")


class SwitchTests(PublishCase):

    def test_defaults_off_and_empty(self):
        self.assertIs(scheduler.DEFAULT_SETTINGS["publishReleases"], False)
        self.assertEqual(scheduler.DEFAULT_SETTINGS["nextVersion"], "")
        keys = dict(gui_config.APIARY_DEFAULTS)
        self.assertIs(keys["publishReleases"], False)
        self.assertEqual(keys["nextVersion"], "")

    def test_switch_off_publishes_nothing(self):
        self.assertIsNone(self.integrate())
        self.assertEqual(self.published, [])

    def test_switch_on_but_remote_not_on_github_publishes_nothing(self):
        self.set_project(publishReleases=True)
        self.remote = "https://gitlab.example/owner/tool.git"
        self.assertIsNone(self.integrate())
        self.assertEqual(self.published, [])

    def test_the_switch_is_offered_only_for_a_github_remote(self):
        shown = {"publishReleases": False, "nextVersion": "", "remoteName": "origin"}
        self.assertIn("publishReleases", gui_config.release_fields(dict(shown), "owner/tool"))
        hidden = gui_config.release_fields(dict(shown), None)
        self.assertNotIn("publishReleases", hidden)
        self.assertNotIn("nextVersion", hidden)

    def test_next_version_is_validated_at_save(self):
        self.assertTrue(gui_config.check({"nextVersion": "2.1"}, False))
        self.assertFalse(gui_config.check({"nextVersion": "2.1.0"}, False))
        self.assertFalse(gui_config.check({"nextVersion": ""}, False))


class CountUpTests(PublishCase):

    def setUp(self):
        super().setUp()
        self.set_project(publishReleases=True)

    def test_first_release_counts_up_from_the_project_version(self):
        result = self.integrate()
        self.assertEqual(result["version"], "2.0.1")
        self.assertEqual(self.published, [("owner/tool", "2.0.1", SHA, "tok")])

    def test_each_integration_counts_the_last_digit_up_once(self):
        self.integrate("t1")
        self.assertIsNone(self.after())   # the same integration again publishes nothing
        self.head = SHA2
        self.integrate("t2")
        self.assertEqual([p[1] for p in self.published], ["2.0.1", "2.0.2"])
        self.assertEqual(self.published[1][2], SHA2)

    def test_next_version_of_the_owner_is_used_once(self):
        self.latest = "2.0.4"
        self.set_project(nextVersion="2.1.0")
        self.integrate("t1")
        self.head = SHA2
        self.integrate("t2")
        self.assertEqual([p[1] for p in self.published], ["2.1.0", "2.1.1"])

    def test_failure_keeps_the_version_and_the_next_round_retries_it(self):
        self.error = OSError("network down")
        self.assertIsNone(self.integrate())
        pending = publish.pending_releases(self.conn, "testproj")
        self.assertEqual([(p["version"], p["commit"]) for p in pending], [("2.0.1", SHA)])
        self.assertIn("network down", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.error = None
        self.latest = "9.9.9"   # what the lookup says later does not change a version already chosen
        publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=lambda repo: self.latest, publisher=self.publisher, token=lambda run=None: "tok")
        self.assertEqual(self.published, [("owner/tool", "2.0.1", SHA, "tok")])
        self.assertEqual(publish.pending_releases(self.conn, "testproj"), [])
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))

    def test_missing_token_waits_and_is_reported(self):
        runtime.queue_upsert(self.conn, "testproj", "feature", tip="t1", plan=REL, status="integrated")
        result = publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=lambda repo: None,
                                           publisher=self.publisher, token=lambda run=None: "")
        self.assertIsNone(result)
        self.assertIn("token", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.assertEqual(len(publish.pending_releases(self.conn, "testproj")), 1)

    def test_release_command_of_the_cli_publishes(self):
        """`bzz integration release` runs the program step; the agent never pushes a tag."""
        from unittest import mock
        from haivequeen import cli
        calls = []
        with mock.patch.dict("os.environ", {"BZZ_AGENT": ""}),                 mock.patch("haivequeen.publish.after_integration", side_effect=lambda conn, env, plan, **kw: calls.append((env.name, plan))):
            cli.main(["integration", "release", "--project", "testproj", "--plan", REL])
        self.assertEqual(calls, [("testproj", REL)])


class DurableQueueTests(PublishCase):
    """Every integration is recorded in order at `bzz integration release` and published later even when anything fails meanwhile."""

    def setUp(self):
        super().setUp()
        self.set_project(publishReleases=True)
        self.lookup_error = None

    def lookup(self, repo):
        if self.lookup_error:
            raise self.lookup_error
        return self.latest

    def after(self):
        return publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=self.lookup,
                                         publisher=self.publisher, token=lambda run=None: "tok")

    def retry(self):
        return publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=self.lookup,
                                     publisher=self.publisher, token=lambda run=None: "tok")

    def test_failed_preparation_is_kept_and_published_at_the_integrated_commit(self):
        self.lookup_error = OSError("api down")
        self.assertIsNone(self.integrate("t1"))
        self.assertIn("api down", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.lookup_error = None
        self.head = SHA2   # main moved on after the integration: the release still goes to the integrated commit
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.1", SHA, "tok")])
        self.assertEqual(publish.pending_releases(self.conn, "testproj"), [])

    def test_integrations_while_an_older_release_is_pending_are_all_published_in_order(self):
        self.error = OSError("network down")
        self.integrate("t1")
        self.head = SHA2
        self.integrate("t2")
        self.assertEqual(self.published, [])
        self.assertEqual([p["commit"] for p in publish.pending_releases(self.conn, "testproj")], [SHA, SHA2])
        self.error = None
        self.retry()
        self.assertEqual([(p[1], p[2]) for p in self.published], [("2.0.1", SHA), ("2.0.2", SHA2)])
        self.assertEqual(publish.pending_releases(self.conn, "testproj"), [])

    def test_a_later_integration_publishes_the_older_pending_one_first(self):
        self.error = OSError("network down")
        self.integrate("t1")
        self.error = None
        self.head = SHA2
        result = self.integrate("t2")
        self.assertEqual([(p[1], p[2]) for p in self.published], [("2.0.1", SHA), ("2.0.2", SHA2)])
        self.assertEqual((result["version"], result["commit"]), ("2.0.2", SHA2))

    def test_a_failure_in_the_middle_keeps_the_rest_and_its_chosen_version(self):
        self.error = OSError("network down")
        self.integrate("t1")
        self.head = SHA2
        self.integrate("t2")
        self.error = None
        calls = []

        def flaky(repository, version, commit, token):
            calls.append(version)
            if len(calls) == 2:
                raise OSError("again down")
            return self.publisher(repository, version, commit, token)
        publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=self.lookup, publisher=flaky, token=lambda run=None: "tok")
        self.assertEqual([p[1] for p in self.published], ["2.0.1"])
        self.latest = "7.0.0"   # a later lookup does not change a version already chosen
        self.retry()
        self.assertEqual([(p[1], p[2]) for p in self.published], [("2.0.1", SHA), ("2.0.2", SHA2)])

    def test_another_plan_integrating_meanwhile_is_queued_behind(self):
        self.error = OSError("network down")
        self.integrate("t1")
        other = REL.replace(".md", "-OTHER.md")
        runtime.queue_upsert(self.conn, "testproj", "other", tip="o1", plan=other, status="integrated")
        self.head = SHA2
        publish.after_integration(self.conn, self.env(), other, run=self.run_git, latest=self.lookup, publisher=self.publisher, token=lambda run=None: "tok")
        self.error = None
        self.retry()
        self.assertEqual([(p[1], p[2]) for p in self.published], [("2.0.1", SHA), ("2.0.2", SHA2)])

    def test_unreadable_remote_falls_back_to_the_local_remote_branch(self):
        def git(argv, cwd):
            if "ls-remote" in argv:
                return 128, "fatal: unable to access"
            if "rev-parse" in argv:
                return 0, SHA2 + "\n"
            return original(argv, cwd)
        original, self.run_git = self.run_git, git
        self.integrate("t1")
        self.assertEqual(self.published, [("owner/tool", "2.0.1", SHA2, "tok")])

    def test_the_same_integration_is_recorded_once(self):
        self.error = OSError("network down")
        self.integrate("t1")
        self.after()
        self.retry()
        self.assertEqual(len(publish.pending_releases(self.conn, "testproj")), 1)

    def test_a_pending_entry_of_the_older_format_is_still_published(self):
        runtime.kv_set(self.conn, "testproj", "release-pending", json.dumps({"queue_id": 99, "plan": REL, "version": "2.0.5", "commit": SHA}))
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.5", SHA, "tok")])
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "release-pending"))


class IntegrationPointTests(PublishCase):
    """H-43: the release goes to the exact commit of each integration, read from a real Git history, even when the remote cannot be read and
    the integration branch has moved on meanwhile."""

    def setUp(self):
        super().setUp()
        self.set_project(publishReleases=True)
        self.git_dir = self.base / "git"
        self.git_dir.mkdir()
        self.git("init", "--quiet", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.a = self.commit("a")
        self.git("switch", "--quiet", "-c", "feature")
        self.tip = self.commit("f1")
        self.git("switch", "--quiet", "main")
        self.b = self.commit("b")
        self.git("merge", "--quiet", "--no-ff", "-m", "merge feature", "feature")
        self.merge = self.git("rev-parse", "HEAD")
        self.later = self.commit("c")   # another integration after this one
        self.remote_head = None         # what ls-remote answers; None = the remote cannot be read
        self.track(self.later)

    def git(self, *args):
        res = subprocess.run(["git", "-C", str(self.git_dir)] + list(args), capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def commit(self, name):
        self.git("commit", "--quiet", "--allow-empty", "-m", name)
        return self.git("rev-parse", "HEAD")

    def track(self, sha):
        """Set (or with None delete) the local remote branch origin/main."""
        if sha:
            self.git("update-ref", "refs/remotes/origin/main", sha)
        else:
            subprocess.run(["git", "-C", str(self.git_dir), "update-ref", "-d", "refs/remotes/origin/main"], capture_output=True)

    def run_git(self, argv, cwd):
        if "get-url" in argv:
            return 0, self.remote + "\n"
        if "ls-remote" in argv:
            return (0, "%s\trefs/heads/main\n" % self.remote_head) if self.remote_head else (128, "fatal: unable to access")
        if "fetch" in argv:
            return 128, "fatal: unable to access"
        argv = list(argv)
        argv[2] = str(self.git_dir)   # git -C <Apiary> ... runs in the test history
        return selfupdate.run_command(argv, self.git_dir)

    def integrate_tip(self, tip, plan=REL, branch="feature"):
        runtime.queue_upsert(self.conn, "testproj", branch, tip=tip, plan=plan, status="integrated")
        return self.after(plan)

    def after(self, plan=REL):
        return publish.after_integration(self.conn, self.env(), plan, run=self.run_git, latest=lambda repo: self.latest,
                                         publisher=self.publisher, token=lambda run=None: "tok")

    def retry(self):
        return publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=lambda repo: self.latest,
                                     publisher=self.publisher, token=lambda run=None: "tok")

    def test_unreadable_commit_is_published_later_at_the_integration_not_at_the_moved_head(self):
        self.track(None)
        self.assertIsNone(self.integrate_tip(self.tip))
        self.assertEqual([p["commit"] for p in publish.pending_releases(self.conn, "testproj")], [""])
        self.assertIn("could not be read", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.track(self.later)   # the branch is readable again but has moved on to a later integration
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge, "tok")])
        self.assertEqual(publish.pending_releases(self.conn, "testproj"), [])

    def test_the_integration_point_is_taken_even_when_the_head_already_moved_on(self):
        self.remote_head = self.later
        self.integrate_tip(self.tip)
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge, "tok")])

    def test_a_fast_forward_integration_is_published_at_the_tip(self):
        self.git("switch", "--quiet", "-c", "ff")
        ff = self.commit("f2")
        self.git("switch", "--quiet", "main")
        self.git("merge", "--quiet", "--ff-only", "ff")
        moved = self.commit("d")
        self.track(None)
        self.integrate_tip(ff, branch="ff")
        self.track(moved)
        self.retry()
        self.assertEqual([p[2] for p in self.published], [ff])

    def test_a_tip_that_is_not_in_the_branch_is_never_published_at_another_commit(self):
        self.git("switch", "--quiet", "-c", "stray", self.a)
        stray = self.commit("never integrated")
        self.git("switch", "--quiet", "main")
        self.track(None)
        self.integrate_tip(stray, branch="stray")
        self.track(self.later)
        self.retry()
        self.assertEqual(self.published, [])
        self.assertIn("not in the history", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.assertEqual(len(publish.pending_releases(self.conn, "testproj")), 1)

    def test_an_entry_of_the_older_format_without_tip_uses_the_tip_of_its_queue_row(self):
        row = runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.tip, plan=REL, status="integrated")
        runtime.kv_set(self.conn, "testproj", publish.SINCE_KEY, "9999")   # the loop does not queue it again itself
        runtime.kv_set(self.conn, "testproj", "release-queue", json.dumps([{"queue_id": row["id"], "plan": REL, "commit": "", "version": ""}]))
        self.retry()
        self.assertEqual([p[2] for p in self.published], [self.merge])

    def test_an_integration_without_release_command_is_published_by_the_loop(self):
        runtime.kv_set(self.conn, "testproj", publish.SINCE_KEY, "2000-01-01T00:00:00")
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.tip, plan=REL, status="integrated")
        self.retry()
        self.assertEqual(self.published, [("owner/tool", "2.0.1", self.merge, "tok")])
        self.retry()
        self.assertIsNone(self.after())
        self.assertEqual(len(self.published), 1)

    def test_the_loop_leaves_an_integration_to_the_queen_that_still_holds_the_place(self):
        runtime.kv_set(self.conn, "testproj", publish.SINCE_KEY, "2000-01-01T00:00:00")
        runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.tip, plan=REL, status="integrated")
        self.conn.execute("INSERT INTO hive_integration(project, plan_path, acquired) VALUES ('testproj', ?, 1)", (REL,))
        self.conn.commit()
        self.retry()
        self.assertEqual(self.published, [])

    def test_an_integration_before_the_switch_was_on_gets_no_release(self):
        row = runtime.queue_upsert(self.conn, "testproj", "feature", tip=self.tip, plan=REL, status="integrated")
        self.conn.execute("UPDATE queue SET updated_at='2001-01-01T00:00:00' WHERE id=?", (row["id"],))
        self.conn.commit()
        runtime.kv_set(self.conn, "testproj", publish.SINCE_KEY, "2002-01-01T00:00:00")
        self.retry()
        self.assertIsNone(self.after())
        self.assertEqual(self.published, [])

    def test_the_switch_time_is_kept_at_the_first_loop_round(self):
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", publish.SINCE_KEY))
        self.retry()
        self.assertTrue(runtime.kv_get(self.conn, "testproj", publish.SINCE_KEY))


class LookupTests(unittest.TestCase):

    def test_latest_version_without_release_is_none(self):
        from unittest import mock
        err = HTTPError("u", 404, "not found", {}, None)
        with mock.patch("haivequeen.releases.urlopen", side_effect=err):
            self.assertIsNone(releases.latest_version("owner/tool"))

    def test_token_from_the_environment(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "abc"}):
            self.assertEqual(publish.token(run=lambda a, c: (1, "")), "abc")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            self.assertEqual(publish.token(run=lambda a, c: (0, "fromgh\n")), "fromgh")


if __name__ == "__main__":
    unittest.main()
