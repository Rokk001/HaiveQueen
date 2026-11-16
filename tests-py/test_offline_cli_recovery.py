"""Recovery must exercise the remote model, not just the CLI executable."""
import json
import subprocess
from datetime import datetime, timedelta
from unittest import mock

from test_f1_rework import StateBase
from haivequeen import offline


def events(*items):
    return "\n".join(json.dumps(item) for item in items)


ANSWER = events({"type": "text", "part": {"text": "OK"}}, {"type": "step_finish", "part": {}})


class CliRecovery(StateBase):
    def provider(self, cli):
        return {"name": cli, "protocol": cli, "command": cli,
                "classes": {"medium": {"model": "vendor/probe-model"}}}

    def test_installed_cli_does_not_clear_offline_without_model_response(self):
        now = datetime(2026, 1, 2, 10)
        for cli in ("opencode", "kilo"):
            with self.subTest(cli=cli):
                p = self.provider(cli)
                offline.mark(self.conn, cli, "connection failed", now=now)
                with mock.patch.object(offline.profile, "environment", return_value={}), mock.patch.object(
                        offline.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="1.2.3", stderr="")):
                    self.assertEqual(offline.recheck(self.conn, [p], 5, now=now + timedelta(minutes=6)), [])
                self.assertIn(cli, offline.read(self.conn))

    def test_model_request_uses_configured_model_and_bounded_isolated_environment(self):
        for cli in ("opencode", "kilo"):
            with self.subTest(cli=cli):
                p = self.provider(cli)
                with mock.patch.object(offline.profile, "environment", return_value={"TEST_PROFILE": "isolated"}), mock.patch.object(
                        offline.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=ANSWER, stderr="")) as run:
                    self.assertTrue(offline.run_cli(p))
                args = run.call_args.args[0]
                self.assertEqual(args[1:4], ["run", "--format", "json"])
                self.assertIn("vendor/probe-model", args)
                self.assertNotIn("--version", args)
                self.assertNotIn("--auto", args)
                self.assertEqual(run.call_args.kwargs["env"], {"TEST_PROFILE": "isolated"})
                self.assertLessEqual(run.call_args.kwargs["timeout"], 60)

    def test_errors_empty_and_incomplete_responses_do_not_recover(self):
        for cli in ("opencode", "kilo"):
            for output in ("", "{}", ANSWER + "\n" + events({"type": "error", "error": "unauthorized"}),
                           events({"type": "step_finish"}), events({"type": "text", "part": {"text": "OK"}})):
                with self.subTest(cli=cli, output=output), mock.patch.object(offline.profile, "environment", return_value={}), mock.patch.object(
                        offline.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=output, stderr="")):
                    self.assertFalse(offline.run_cli(self.provider(cli)))

    def test_timeout_nonzero_and_unknown_protocol_remain_offline(self):
        with mock.patch.object(offline.profile, "environment", return_value={}):
            for cli in ("opencode", "kilo"):
                with mock.patch.object(offline.subprocess, "run", side_effect=subprocess.TimeoutExpired(cli, 60)):
                    self.assertFalse(offline.run_cli(self.provider(cli)))
                with mock.patch.object(offline.subprocess, "run", return_value=mock.Mock(returncode=1, stdout=ANSWER)):
                    self.assertFalse(offline.run_cli(self.provider(cli)))
            with mock.patch.object(offline.subprocess, "run") as run:
                self.assertFalse(offline.run_cli(self.provider("unknown")))
                run.assert_not_called()

    def test_completed_model_response_clears_offline_and_missing_model_never_probes(self):
        now = datetime(2026, 1, 2, 10)
        for cli in ("opencode", "kilo"):
            p = self.provider(cli)
            offline.mark(self.conn, cli, "connection failed", now=now)
            with mock.patch.object(offline.profile, "environment", return_value={}), mock.patch.object(
                    offline.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=ANSWER)):
                self.assertEqual(offline.recheck(self.conn, [p], 5, now=now + timedelta(minutes=6)), [cli])
            self.assertNotIn(cli, offline.read(self.conn))
            p["classes"] = {}
            with mock.patch.object(offline.subprocess, "run") as run:
                self.assertFalse(offline.run_cli(p))
                run.assert_not_called()
