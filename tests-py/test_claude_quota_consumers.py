"""Regression checks for shared Claude quota freshness across display and scheduling."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401
from haivequeen import overview, db, hives, loop, runtime, scheduler, usage  # noqa: E402


# Verify stale quota failures cannot keep recommending an exhausted provider.
class ClaudeQuotaConsumersTest(unittest.TestCase):
    # Create an isolated cache and enable only synthetic Claude limit reads.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "quota.db")
        self.addCleanup(self.conn.close)
        self.env = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name,
            "BZZ_RUNTIME_OVERRIDE": "", "BZZ_PROVIDER_LIMITS_FILE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cfg = {"providers": [{"name": "claude", "protocol": "claude", "slots": 5,
            "limit": {"source": "claude"}, "classes": {"medium": {"model": "test-model"}}}]}
        self.provider = scheduler.get_providers(self.cfg)[0]
        self.reset = int(time.time()) + 3600

    # Store a fictional snapshot in the existing shared cache format.
    def snapshot(self, percent, failed=False, age_hours=0):
        raw = {"saved": (datetime.now().astimezone() - timedelta(hours=age_hours)).isoformat(),
            "sevenDay": {"utilization": percent / 100, "resetsAt": self.reset}}
        if failed:
            raw["refresh_error"] = "quota refresh failed"
        runtime.kv_set(self.conn, "*", "usage-claude", json.dumps(raw))
        return raw

    # Apply the current account reading to the dashboard, ranking and start gate.
    def test_current_weekly_exhaustion_is_shared_everywhere(self):
        raw = self.snapshot(100)
        self.assertEqual(overview.window_usage(raw, datetime.now())["SevenDay"]["pct"], 100)
        self.assertEqual(scheduler.claude_usage(self.conn)["SevenDay"]["Pct"], 100)
        self.assertEqual(usage.get_provider_limit(self.conn, self.provider, read_pc=True)["free_pct"], 0)
        self.assertEqual(scheduler.provider_limit(self.provider, self.conn)["FreePct"], 0)
        self.assertIsNone(scheduler.select_provider([self.provider], "medium", [], {"claude": 5}, conn=self.conn))
        self.assertEqual(usage.limit_windows(self.conn, self.provider, read_pc=True)[0]["used_pct"], 100)

    # Hide an unverified historical percentage after a failed metadata refresh.
    def test_failed_refresh_does_not_display_old_ninety_percent(self):
        raw = self.snapshot(90, failed=True, age_hours=usage.RETAIN_HOURS + 1)
        self.assertIsNone(overview.window_usage(raw, datetime.now())["SevenDay"])
        self.assertIsNone(scheduler.claude_usage(self.conn)["SevenDay"])
        row = overview.provider_lines(self.conn, self.cfg, [])[0]
        self.assertIsNone(row["used_pct"])
        self.assertIsNone(row["ratio"])
        self.assertFalse(row["next"])

    # A failed check keeps working on the last verified reading while it is recent: 13 % used must not look like 100 %.
    def test_failed_refresh_keeps_a_recent_reading_for_starts(self):
        self.snapshot(13, failed=True)
        self.assertEqual(scheduler.provider_limit(self.provider, self.conn)["FreePct"], 87)
        self.assertEqual(usage.get_provider_limit(self.conn, self.provider, read_pc=True)["free_pct"], 87)
        self.assertEqual([round(w["used_pct"]) for w in usage.limit_windows(self.conn, self.provider, read_pc=True)], [13])
        self.assertIsNotNone(scheduler.select_provider([self.provider], "medium", [], {"claude": 5}, conn=self.conn))

    # Do not grant automatic work based on quota that could not be verified for a long time.
    def test_failed_refresh_with_an_old_reading_does_not_recommend_or_start_claude(self):
        self.snapshot(13, failed=True, age_hours=usage.RETAIN_HOURS + 1)
        self.assertEqual(scheduler.provider_limit(self.provider, self.conn)["FreePct"], 0)
        self.assertEqual(usage.get_provider_limit(self.conn, self.provider, read_pc=True)["free_pct"], 0)
        self.assertIsNone(scheduler.select_provider([self.provider], "medium", [], {"claude": 5}, conn=self.conn))
        self.assertGreaterEqual(usage.limit_windows(self.conn, self.provider, read_pc=True)[0]["used_pct"], 100)

    # A failed check says what went wrong instead of a generic text.
    def test_failed_refresh_stores_the_reason(self):
        provider = dict(self.provider, command="a-claude-cli-that-does-not-exist")
        self.assertFalse(usage.refresh_claude_usage(self.conn, [provider], run=lambda *a, **k: None, force=True))
        self.assertIn("missing CLI", usage.claude_refresh_error(self.conn))

    # The provider may confirm exhaustion without supplying a reset timestamp.
    def test_verified_percentage_without_reset_stays_known(self):
        raw = self.snapshot(100)
        raw["sevenDay"]["resetsAt"] = None
        runtime.kv_set(self.conn, "*", "usage-claude", json.dumps(raw))
        self.assertEqual(overview.window_usage(raw, datetime.now())["SevenDay"], {"pct": 100, "reset": ""})
        self.assertEqual(scheduler.claude_usage(self.conn)["SevenDay"], {"Pct": 100, "Reset": "", "At": None})
        self.assertEqual(scheduler.provider_limit(self.provider, self.conn)["FreePct"], 0)
        row = overview.provider_lines(self.conn, self.cfg, [])[0]
        self.assertEqual(row["used_pct"], 100)
        self.assertIsNone(row["reset_at"])
        self.assertFalse(row["next"])

    # Metadata I/O must finish before scheduling acquires its shared lock.
    def test_refresh_runs_outside_the_hive_scheduling_lock(self):
        with mock.patch.object(usage, "refresh_claude_usage") as refresh:
            with mock.patch.object(loop, "hive_schedule_lock") as lock:
                lock.return_value.__enter__.side_effect = lambda: refresh.assert_called_once_with(
                    self.conn, scheduler.get_providers(self.cfg))
                with mock.patch.object(hives, "schedule_round", return_value={"ok": True}):
                    loop.run_round(self.conn, self.cfg, envs=[])

    # Restore normal display and ranking after the next authoritative reading.
    def test_successful_refresh_restores_known_quota(self):
        self.snapshot(90, failed=True)
        raw = self.snapshot(40)
        self.assertEqual(overview.window_usage(raw, datetime.now())["SevenDay"]["pct"], 40)
        self.assertEqual(scheduler.provider_limit(self.provider, self.conn)["FreePct"], 60)
        row = overview.provider_lines(self.conn, self.cfg, [])[0]
        self.assertEqual(row["used_pct"], 40)
        self.assertTrue(row["next"])


if __name__ == "__main__":
    unittest.main()
