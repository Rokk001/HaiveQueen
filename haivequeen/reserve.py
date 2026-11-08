"""M-2 of HQ-LIMITS-ROUTING-FIX: the quota reserve from both usage windows, as a pure formula.

Per window quota = free percent / percent of the window's time left until its reset (1.0 = the quota lasts exactly until the reset).
Reserve = weekly quota x 5-hour quota, where the 5-hour quota counts at most 1.0 (a full 5-hour window never raises the reserve).
Shown as (reserve - 1) in whole percent, at most +99 %; a used-up window (reserve 0) shows "0 %" instead of -100 %.
A window whose reset time is unknown counts as 100 % time left; the time left counts at least 1 %; a passed reset counts as reset.
No 5-hour window: the weekly quota alone; only a 5-hour window: the weekly quota counts as 1.0 (the caller passes it).
"""


def window_quota(free_pct, reset_epoch=None, now=None, window_hours=168.0):
    """Quota of one usage window: free percent / percent of its time left until the reset.

    `free_pct` is 0-100 (the unused part of the window). `reset_epoch` is the reset as an epoch second; None = unknown, which counts
    as 100 % time left. A passed reset counts as reset: quota 1.0. The time left never counts below 1 %, so a nearly spent window
    does not divide by zero and a used-up one (free 0) gives quota 0."""
    if now is None:
        import time

        now = time.time()
    free = max(0.0, float(free_pct))
    if reset_epoch is None:
        return free / 100.0
    left_hours = (float(reset_epoch) - float(now)) / 3600.0
    if left_hours <= 0.0:
        return 1.0
    time_left_pct = max(1.0, min(100.0, left_hours / float(window_hours) * 100.0))
    return free / time_left_pct


def reserve(weekly_quota, five_hour_quota=None):
    """Reserve of a provider: weekly quota x 5-hour quota; the 5-hour quota counts at most 1.0 and never below 0.

    Without a 5-hour window (`five_hour_quota` None) the weekly quota alone is the reserve. A used-up window (quota 0 in either
    window) gives reserve 0: no new work until that window resets."""
    if five_hour_quota is None:
        return max(0.0, float(weekly_quota))
    return max(0.0, float(weekly_quota)) * min(1.0, max(0.0, float(five_hour_quota)))


def display(reserve_value):
    """The reserve as shown to the owner: (reserve - 1) in whole percent, at most +99 % and never below -99 %.

    A used-up window (reserve 0 or less, also None) shows "0 %" instead of "-100 %"; a caller without any reading keeps its own
    'unknown' text and does not pass here."""
    if reserve_value is None:
        return "0 %"
    value = float(reserve_value)
    if value <= 0.0:
        return "0 %"
    pct = max(-99, min(99, int(round((value - 1.0) * 100))))
    return ("+%d %%" if pct >= 0 else "%d %%") % pct
