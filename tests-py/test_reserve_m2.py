"""M-2 of HQ-LIMITS-ROUTING-FIX: failing tests for the quota reserve from both usage windows (the pure formula in haivequeen/reserve.py).

Per window quota = free percent / percent of the window's time left until its reset (1.0 = the quota lasts exactly until the reset).
Reserve = weekly quota x 5-hour quota, where the 5-hour quota counts at most 1.0. Shown as (reserve - 1) in whole percent, at most +99 %;
a used-up window (reserve 0) shows "0 %" instead of -100 %. A window whose reset time is unknown counts as 100 % time left; the time left
counts at least 1 %; a passed reset counts as reset (quota 1.0). No 5-hour window: the weekly quota alone; only a 5-hour window: weekly 1.0.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import reserve as rsv


class WindowQuota(unittest.TestCase):
    """Per window quota = free percent / percent of the window's time left until its reset."""

    def test_free_over_time_left(self):
        # week 80 % free with half of the week (84 h) to the reset: 80/50 = 1.6
        self.assertAlmostEqual(rsv.window_quota(80, now=0, reset_epoch=84 * 3600), 1.6)

    def test_full_window_is_one(self):
        # 100 % free with the full time left: the quota lasts exactly until the reset
        self.assertAlmostEqual(rsv.window_quota(100, now=0, reset_epoch=168 * 3600), 1.0)

    def test_unknown_reset_counts_as_full_time_left(self):
        # a window whose reset time is unknown counts as 100 % time left: 60 free gives 0.6
        self.assertAlmostEqual(rsv.window_quota(60, now=0, reset_epoch=None), 0.6)

    def test_passed_reset_counts_as_reset(self):
        # a window whose reset time has passed counts as reset: quota 1.0
        self.assertAlmostEqual(rsv.window_quota(30, now=100 * 3600, reset_epoch=50 * 3600), 1.0)

    def test_time_left_counts_at_least_one_percent(self):
        # 84 min left of the week is under 1 %: it counts as 1 %, so 80 free gives 80
        self.assertAlmostEqual(rsv.window_quota(80, now=0, reset_epoch=84 * 60), 80.0)

    def test_used_up_window_is_zero(self):
        # no quota left in the window: quota 0
        self.assertEqual(rsv.window_quota(0, now=0, reset_epoch=100 * 3600), 0.0)


class Reserve(unittest.TestCase):
    """Reserve = weekly quota x 5-hour quota; the 5-hour quota counts at most 1.0."""

    def test_used_up_five_hour_window_blocks(self):
        # week 80 % free / 50 % time left = 1.6, 5 h 0 % free = 0 -> reserve 0 (blocked)
        self.assertEqual(rsv.reserve(1.6, rsv.window_quota(0, now=0, reset_epoch=2 * 3600, window_hours=5)), 0.0)

    def test_full_five_hour_window_never_raises(self):
        # week 1.6, 5 h 60 % free / 60 % time = 1.0 -> the reserve stays at the weekly quota (+60 %)
        self.assertAlmostEqual(rsv.reserve(1.6, rsv.window_quota(60, now=0, reset_epoch=3 * 3600, window_hours=5)), 1.6)

    def test_five_hour_quota_is_capped_at_one(self):
        # week 1.0, 5 h 90 % free / 20 % time = 4.5 capped at 1.0 -> +0 %
        self.assertAlmostEqual(rsv.reserve(1.0, rsv.window_quota(90, now=0, reset_epoch=3600, window_hours=5)), 1.0)

    def test_low_five_hour_window_lowers(self):
        # week 1.0, 5 h 30 % free / 60 % time = 0.5 -> -50 %
        self.assertAlmostEqual(rsv.reserve(1.0, rsv.window_quota(30, now=0, reset_epoch=3 * 3600, window_hours=5)), 0.5)

    def test_without_five_hour_window_the_week_counts_alone(self):
        # no 5-hour window: the weekly quota alone is the reserve
        self.assertAlmostEqual(rsv.reserve(1.2), 1.2)

    def test_only_a_five_hour_window_counts_the_week_as_one(self):
        # a provider that reports only a 5-hour window counts its weekly quota as 1.0
        self.assertAlmostEqual(rsv.reserve(1.0, rsv.window_quota(30, now=0, reset_epoch=3 * 3600, window_hours=5)), 0.5)

    def test_passed_reset_counts_as_reset_in_the_reserve(self):
        # the weekly window already reset (quota 1.0), the 5-hour window is used up: still blocked
        week = rsv.window_quota(80, now=200 * 3600, reset_epoch=100 * 3600)
        self.assertEqual(rsv.reserve(week, 0.0), 0.0)


class Display(unittest.TestCase):
    """Shown as (reserve - 1) in whole percent, at most +99 %; a used-up window shows "0 %"."""

    def test_used_up_shows_zero(self):
        # reserve 0 is the used-up case: it shows "0 %", not "-100 %"
        self.assertEqual(rsv.display(0.0), "0 %")

    def test_examples(self):
        self.assertEqual(rsv.display(1.6), "+60 %")
        self.assertEqual(rsv.display(1.0), "+0 %")
        self.assertEqual(rsv.display(0.5), "-50 %")

    def test_at_most_plus_ninety_nine(self):
        # a provider without limit counts +99 % as today; no reserve shows more
        self.assertEqual(rsv.display(1.99), "+99 %")
        self.assertEqual(rsv.display(2.5), "+99 %")

    def test_never_below_minus_ninety_nine(self):
        # a tiny positive reserve must not show -100 % (that is the used-up case, shown "0 %")
        self.assertEqual(rsv.display(0.004), "-99 %")


if __name__ == "__main__":
    unittest.main()
