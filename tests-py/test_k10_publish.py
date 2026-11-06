"""K10, H-43 and H-46(k): the Apiary switch "Publish release at integration", the version count-up and the release published by HaiveQueen itself."""
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError

from bzztest import REL, RoundCase
from haivequeen import db, gui_config, publish, releases, runtime, scheduler

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
        pending = json.loads(runtime.kv_get(self.conn, "testproj", publish.PENDING_KEY))
        self.assertEqual((pending["version"], pending["commit"]), ("2.0.1", SHA))
        self.assertIn("network down", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.error = None
        self.latest = "9.9.9"   # what the lookup says later does not change a version already chosen
        publish.retry_pending(self.conn, [self.env()], run=self.run_git, latest=lambda repo: self.latest, publisher=self.publisher, token=lambda run=None: "tok")
        self.assertEqual(self.published, [("owner/tool", "2.0.1", SHA, "tok")])
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", publish.PENDING_KEY))
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))

    def test_missing_token_waits_and_is_reported(self):
        runtime.queue_upsert(self.conn, "testproj", "feature", tip="t1", plan=REL, status="integrated")
        result = publish.after_integration(self.conn, self.env(), REL, run=self.run_git, latest=lambda repo: None,
                                           publisher=self.publisher, token=lambda run=None: "")
        self.assertIsNone(result)
        self.assertIn("token", runtime.kv_get(self.conn, "testproj", publish.ERROR_KEY))
        self.assertTrue(runtime.kv_get(self.conn, "testproj", publish.PENDING_KEY))

    def test_release_command_of_the_cli_publishes(self):
        """`bzz integration release` runs the program step; the agent never pushes a tag."""
        from unittest import mock
        from haivequeen import cli
        calls = []
        with mock.patch.dict("os.environ", {"BZZ_AGENT": ""}),                 mock.patch("haivequeen.publish.after_integration", side_effect=lambda conn, env, plan, **kw: calls.append((env.name, plan))):
            cli.main(["integration", "release", "--project", "testproj", "--plan", REL])
        self.assertEqual(calls, [("testproj", REL)])


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
