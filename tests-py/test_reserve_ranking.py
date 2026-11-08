"""Provider ranking by the raw quota reserve (no rounding), local first between equal reserves (also without limit), and the
retained five-hour window of a failed Claude check in the scheduler reserve."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

from bzztest import RoundCase, provider
from haivequeen import bees, runtime
from haivequeen import scheduler as sch


def norm(*entries):
    """The providers as the scheduler sees them."""
    return sch.get_providers({"providers": list(entries)})


class RankingTests(RoundCase):
    def table(self, **used):
        """Name -> used percent, all with the same reset."""
        return {name: {"usedPercent": pct, "resetHours": 100} for name, pct in used.items()}

    def test_raw_reserve_decides_not_a_rounded_one(self):
        """A reserve that is better by less than 1e-6 still wins over the local provider."""
        a, = norm(provider("a", 1, limit="claude"))
        b, = norm(provider("b", 1, limit="claude"))
        limits = {"a": {"usedPercent": 50.0000001, "resetHours": 100}, "b": {"usedPercent": 50.0, "resetHours": 100}}
        free = {"a": 1, "b": 1}
        with mock.patch.object(sch.servers, "is_local", side_effect=lambda p: p["name"] == "a"):
            self.assertEqual(sch.select_provider([a, b], "medium", [], free, limits=limits)["name"], "b")

    def test_equal_reserves_local_first_then_settings_order(self):
        a, = norm(provider("a", 1, limit="claude"))
        b, = norm(provider("b", 1, limit="claude"))
        c, = norm(provider("c", 1, limit="claude"))
        limits = self.table(a=40, b=40, c=40)
        free = {"a": 1, "b": 1, "c": 1}
        with mock.patch.object(sch.servers, "is_local", side_effect=lambda p: p["name"] == "c"):
            self.assertEqual(sch.select_provider([a, b, c], "medium", [], free, limits=limits)["name"], "c")
        with mock.patch.object(sch.servers, "is_local", return_value=False):
            self.assertEqual(sch.select_provider([a, b, c], "medium", [], free, limits=limits)["name"], "a")

    def test_two_providers_without_limit_local_first(self):
        a, = norm(provider("a", 1))
        b, = norm(provider("b", 1))
        free = {"a": 1, "b": 1}
        with mock.patch.object(sch.servers, "is_local", side_effect=lambda p: p["name"] == "b"):
            self.assertEqual(sch.select_provider([a, b], "medium", [], free)["name"], "b")
            self.assertEqual([p["name"] for p in bees.by_reserve([a, b], [a, b])], ["b", "a"])
        with mock.patch.object(sch.servers, "is_local", return_value=False):
            self.assertEqual(sch.select_provider([a, b], "medium", [], free)["name"], "a")
            self.assertEqual([p["name"] for p in bees.by_reserve([a, b], [a, b])], ["a", "b"])


class RetainedFiveHourTests(RoundCase):
    def test_retained_five_hour_window_counts_in_the_reserve(self):
        """After a failed check the kept five-hour window (used up) gives reserve 0 like the kept weekly one."""
        reset = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        saved = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        runtime.kv_set(self.conn, "*", "usage-claude:claude", json.dumps({
            "saved": saved, "refresh_error": "x",
            "fiveHour": {"utilization": 1.0, "resetsAt": reset},
            "sevenDay": {"utilization": 0.1, "resetsAt": reset + 5 * 24 * 3600}}))
        prov = {"name": "claude", "protocol": "claude", "limit": {"source": "claude"}, "slots": 1}
        env = {k: v for k, v in os.environ.items() if k != "BZZ_RUNTIME_OVERRIDE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(sch.provider_reserve(prov, self.conn), 0.0)
            self.assertTrue(sch.reserve_zero(prov, self.conn))
