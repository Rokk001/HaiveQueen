"""Tests of the settings for the values of the interruption handling (clock jump, retry waits, rate-limit wait without a known reset) and of the
rule that a pause survives the restart of the self-update."""
import argparse
import contextlib
import io
import json
import os
import unittest
from datetime import datetime

from bzztest import RoundCase
from haivequeen import agent_runner as ar
from haivequeen import interrupt, loop, routine, runstate
from haivequeen import scheduler as sch


class SettingsDefaultsTest(unittest.TestCase):
    """The defaults equal the former fixed values and a machine setting overrides them."""

    def test_defaults(self):
        """Clock jump 120 s, waits 1/2/4/8/15 minutes (the last one repeats), 15 minutes without a known reset."""
        s = sch.build_settings({}, {})
        self.assertEqual(s["clockJumpSeconds"], 120)
        self.assertEqual(list(s["retryWaitMinutes"]), [1, 2, 4, 8, 15])
        self.assertEqual(s["rateLimitWaitMinutes"], 15)

    def test_machine_setting_overrides(self):
        """A value in config.toml replaces the default."""
        s = sch.build_settings({"clockJumpSeconds": 30, "retryWaitMinutes": [3, 6], "rateLimitWaitMinutes": 7}, {})
        self.assertEqual((s["clockJumpSeconds"], list(s["retryWaitMinutes"]), s["rateLimitWaitMinutes"]), (30, [3, 6], 7))

    def test_backoff_follows_the_list(self):
        """The wait of the n-th retry is the n-th entry; beyond the list the last entry repeats."""
        self.assertEqual([interrupt.backoff_minutes(n, [3, 6]) for n in (1, 2, 3, 9)], [3, 6, 6, 6])

    def test_clock_watch_uses_the_given_limit(self):
        """A jump of 300 s is found with a limit of 120 s and ignored with a limit of 400 s."""
        def watch(limit):
            """Jumps found over three fake clock readings."""
            jumps, it = [], iter([0.0, 305.0, 310.0])
            ar.clock_watch(jumps, step=5.0, now=lambda: next(it), sleep=lambda s: None, rounds=2, limit=limit)
            return jumps
        self.assertEqual(len(watch(120)), 1)
        self.assertEqual(watch(400), [])


class RetryWaitSettingsTest(RoundCase):
    """The retry waits of the round come from the settings."""

    settings = {"retryWaitMinutes": [30], "rateLimitWaitMinutes": 7}

    def setUp(self):
        """Package R1 and one interrupted work agent."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "R1", "title": "first task", "class": "medium"}])

    def interrupted(self, kind):
        """A failed work agent of R1 that ended interrupted."""
        self.agent("DUMMY-R1-work", package="R1", phase="work", status="failed", last_line="error: " + kind, type="claude", provider="claude",
                   started=3, ended=1, interrupted=kind, retryAt="")

    def wait_hours(self, name):
        """Hours from now until the stored retry time of an agent row."""
        at = routine.db.parse_time(self.ag(name).get("retryAt"))
        return None if at is None else (at - datetime.now()).total_seconds() / 3600

    def test_connection_retry_waits_the_configured_minutes(self):
        """The first retry after a connection end waits 30 minutes instead of 1."""
        self.interrupted(interrupt.CONNECTION)
        self.round()
        self.assertEqual(self.names(), ["DUMMY-R1-work"])
        self.assertAlmostEqual(self.wait_hours("DUMMY-R1-work"), 0.5, delta=0.02)

    def test_rate_limit_without_known_reset_waits_the_configured_minutes(self):
        """No reset time is known and both providers are full: 7 minutes instead of 15."""
        unknown = [{"usedPercent": 100, "resetHours": None}] * 2
        f = self.base / "limits.json"
        f.write_text(json.dumps({n: {"usedPercent": 100, "resetHours": None, "windows": unknown} for n in ("claude", "codex")}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(f)
        self.interrupted(interrupt.RATE_LIMIT)
        self.round()
        self.assertAlmostEqual(self.wait_hours("DUMMY-R1-work"), 7 / 60, delta=0.02)


class PauseKeptTest(RoundCase):
    """`bzz start --keep-pause` (used by the self-update) leaves a pause in place."""

    def start(self, **kw):
        """Run `bzz start` in the foreground with a loop that ends at once."""
        args = argparse.Namespace(foreground=True, terminal="headless", rounds=0, round_minutes=0, **kw)
        orig = loop.run_loop
        loop.run_loop = lambda *a, **k: 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop.cmd_start(args, self.conn)
        finally:
            loop.run_loop = orig

    def test_keep_pause_leaves_the_pause(self):
        """With keep_pause a paused Queen stays PAUSED."""
        runstate.pause(self.conn, "test")
        self.start(keep_pause=True)
        self.assertEqual(runstate.get_state(self.conn)["state"], "PAUSED")

    def test_plain_start_still_resumes(self):
        """Without the flag `bzz start` lifts the pause as before."""
        runstate.pause(self.conn, "test")
        self.start(keep_pause=False)
        self.assertEqual(runstate.get_state(self.conn)["state"], "RUNNING")

    def test_boot_command_forwards_the_flag(self):
        """The Queen pane runs `start --foreground --keep-pause` too, so the second start does not resume either."""
        args = argparse.Namespace(foreground=False, terminal=None, rounds=0, round_minutes=0, keep_pause=True)
        self.assertIn("--keep-pause", loop.start_boot_command(args))
        args.keep_pause = False
        self.assertNotIn("--keep-pause", loop.start_boot_command(args))


if __name__ == "__main__":
    unittest.main()
