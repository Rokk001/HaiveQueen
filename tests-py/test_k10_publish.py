"""Compatibility of the retired integration-release command and queue."""
import json
import unittest
from unittest import mock
from urllib.error import HTTPError

from bzztest import REL, RoundCase
from haivequeen import cli, publish, releases, runtime, settings


class LegacyReleaseTests(RoundCase):
    def test_cli_cannot_publish_a_release_from_an_integrated_queue_row(self):
        path = self.repo / ".haivequeen" / "project.toml"
        with path.open("a", encoding="utf-8") as stream:
            stream.write("publishReleases = true\n")
        runtime.queue_upsert(self.conn, "testproj", "feature", tip="a" * 40, plan=REL, status="integrated")
        with mock.patch.dict("os.environ", {"BZZ_AGENT": ""}), \
             mock.patch.object(publish, "record") as record, \
             mock.patch.object(publish, "pipeline_release") as release:
            cli.main(["integration", "release", "--project", "testproj", "--plan", REL])
        record.assert_not_called()
        release.assert_not_called()
        self.assertEqual(publish.pending_releases(self.conn, "testproj"), [])

    def test_loop_leaves_legacy_pending_entries_untouched(self):
        entry = {"queue_id": 1, "plan": REL, "commit": "a" * 40, "version": "2.0.1"}
        payload = json.dumps([entry])
        runtime.kv_set(self.conn, "testproj", publish.QUEUE_KEY, payload)
        with mock.patch.object(publish, "discover") as discover, mock.patch.object(publish, "_drain") as drain:
            self.assertEqual(publish.retry_pending(self.conn, [self.env()]), [])
        discover.assert_not_called()
        drain.assert_not_called()
        self.assertEqual(runtime.kv_get(self.conn, "testproj", publish.QUEUE_KEY), payload)

    def test_next_version_is_validated_at_save(self):
        self.assertTrue(settings.check({"nextVersion": "2.1"}, False))
        self.assertFalse(settings.check({"nextVersion": "2.1.0"}, False))
        self.assertFalse(settings.check({"nextVersion": ""}, False))


class LookupTests(unittest.TestCase):

    def test_latest_version_without_release_is_none(self):
        err = HTTPError("u", 404, "not found", {}, None)
        with mock.patch("haivequeen.releases.urlopen", side_effect=err):
            self.assertIsNone(releases.latest_version("owner/tool"))

    def test_token_from_the_environment(self):
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "abc"}):
            self.assertEqual(publish.token(run=lambda a, c: (1, "")), "abc")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            self.assertEqual(publish.token(run=lambda a, c: (0, "fromgh\n")), "fromgh")


if __name__ == "__main__":
    unittest.main()
