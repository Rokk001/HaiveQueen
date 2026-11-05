"""Tests of haivequeen.usage with invented log files in a temp folder and a temp database (no real logs touched)."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, runtime, usage  # noqa: E402

SONNET = "claude-sonnet-5-5"


def ts(local: datetime) -> str:
    """Return an ISO UTC timestamp for a naive local time."""
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def claude_line(mid, model, when, tin, cc, cr, out) -> str:
    """Build one Claude assistant log line."""
    return json.dumps({"type": "assistant", "timestamp": ts(when), "message": {
        "id": mid, "model": model, "role": "assistant",
        "usage": {"input_tokens": tin, "cache_creation_input_tokens": cc, "cache_read_input_tokens": cr,
                  "output_tokens": out, "iterations": [{"input_tokens": 999, "output_tokens": 999}]}}},
        separators=(",", ":"))


def codex_count(when, tin, out) -> str:
    """Build one Codex token_count line (cumulative)."""
    return ('{"timestamp":"%s","ordinal":5,"type":"event_msg","payload":{"type":"token_count","info":'
            '{"total_token_usage":{"input_tokens":%d,"cached_input_tokens":1,"output_tokens":%d,"total_tokens":%d}}}}'
            % (ts(when), tin, out, tin + out))


def codex_ctx(when, model) -> str:
    """Build one Codex turn_context line."""
    return '{"timestamp":"%s","ordinal":2,"type":"turn_context","payload":{"model":"%s"}}' % (ts(when), model)


def write(path: Path, lines) -> None:
    """Write lines (each ending in a newline) to a file."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")








class WindowTest(unittest.TestCase):
    """5h/7d windows and weekly-limit sources."""

    def setUp(self):
        """Open a temp database."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = db.connect(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.conn.close)
        self.now = int(time.time())

    def event(self, five_reset, seven_reset, five=0.27, seven=0.8):
        """Build a rate_limit_event with two windows."""
        return {"rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": five, "resetsAt": five_reset},
            "seven_day": {"utilization": seven, "resetsAt": seven_reset}}}}

    def test_claude_windows_roundtrip(self):
        """Saved windows come back as percent and reset; an event without windows is ignored."""
        self.assertFalse(usage.save_claude_usage(self.conn, {"rate_limit_info": {}}))
        self.assertEqual(usage.get_claude_usage(self.conn), {"five_hour": None, "seven_day": None})
        self.assertTrue(usage.save_claude_usage(self.conn, self.event(self.now + 3600, self.now + 86400)))
        u = usage.get_claude_usage(self.conn)
        self.assertEqual(u["five_hour"]["pct"], 27)
        self.assertEqual(u["seven_day"]["pct"], 80)
        self.assertEqual(u["seven_day"]["at"], self.now + 86400)
        self.assertEqual(runtime.kv_get(self.conn, "*", "usage-claude") is not None, True)
        self.assertEqual(usage.format_usage(u["five_hour"]), "27%% (until %s)" % u["five_hour"]["reset"])
        self.assertEqual(usage.format_usage(None), "n/a")

    def test_claude_expired_window(self):
        """A window whose reset time has passed shows no percent and 'new window'."""
        usage.save_claude_usage(self.conn, self.event(self.now - 10, self.now + 86400))
        u = usage.get_claude_usage(self.conn)
        self.assertEqual(u["five_hour"], {"pct": None, "reset": "new window"})
        self.assertEqual(usage.format_usage(u["five_hour"]), "n/a")

    def test_project_scoped_key(self):
        """The windows can be stored per project; other scopes stay empty."""
        usage.save_claude_usage(self.conn, self.event(self.now + 60, self.now + 600), project="p")
        self.assertEqual(usage.get_claude_usage(self.conn, "p")["five_hour"]["pct"], 27)
        self.assertIsNone(usage.get_claude_usage(self.conn)["five_hour"])

    def test_codex_windows(self):
        """Codex windows come from the last rate_limits block of the newest log, told apart by window_minutes."""
        d = Path(self.tmp.name) / "codex"
        d.mkdir()
        line = {"payload": {"rate_limits": {
            "primary": {"used_percent": 12.4, "window_minutes": 300, "resets_at": self.now + 1000},
            "secondary": {"used_percent": 60.0, "window_minutes": 10080, "resets_at": self.now + 90000}}}}
        old = {"payload": {"rate_limits": {"primary": {"used_percent": 99, "window_minutes": 300, "resets_at": self.now + 5}}}}
        (d / "r.jsonl").write_text(json.dumps(old, separators=(",", ":")) + "\n" + "x\n" + json.dumps(line, separators=(",", ":")) + "\n" + '{"a":1}\n', encoding="utf-8")
        u = usage.get_codex_usage(d)
        self.assertEqual(u["five_hour"]["pct"], 12)
        self.assertEqual(u["seven_day"]["pct"], 60)
        self.assertEqual(u["seven_day"]["at"], self.now + 90000)
        self.assertEqual(usage.get_codex_usage(Path(self.tmp.name) / "missing"), {"five_hour": None, "seven_day": None})

    def test_codex_expired_window_is_skipped(self):
        """Codex windows already reset are not reported."""
        d = Path(self.tmp.name) / "codex"
        d.mkdir()
        line = {"payload": {"rate_limits": {"primary": {"used_percent": 5, "window_minutes": 300, "resets_at": self.now - 5}}}}
        (d / "r.jsonl").write_text(json.dumps(line, separators=(",", ":")) + "\n", encoding="utf-8")
        self.assertIsNone(usage.get_codex_usage(d)["five_hour"])

    def test_provider_limit_sources(self):
        """Weekly limit per provider: claude from kv, codex from logs, none/unknown = 100 % free in 168 h."""
        usage.save_claude_usage(self.conn, self.event(self.now + 3600, self.now + 7200, seven=0.75))
        d = Path(self.tmp.name) / "codex"
        d.mkdir()
        line = {"payload": {"rate_limits": {"secondary": {"used_percent": 40, "window_minutes": 10080,
                                                          "resets_at": self.now + 36000}}}}
        (d / "r.jsonl").write_text(json.dumps(line, separators=(",", ":")) + "\n", encoding="utf-8")
        provs = [{"name": "c", "limit": {"source": "claude"}}, {"name": "x", "limit": {"source": "codex"}},
                 {"name": "l", "limit": {"source": "none"}}, {"name": "n"}]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BZZ_PROVIDER_LIMITS_FILE", None)
            os.environ.pop("BZZ_DATA_DIR", None)
            lim = usage.provider_limits(self.conn, provs, codex_dir=d)
        self.assertEqual(lim["c"]["free_pct"], 25.0)
        self.assertAlmostEqual(lim["c"]["hours"], 2.0, delta=0.05)
        self.assertEqual(lim["x"]["free_pct"], 60.0)
        self.assertAlmostEqual(lim["x"]["hours"], 10.0, delta=0.05)
        for k in ("l", "n"):
            self.assertEqual((lim[k]["free_pct"], lim[k]["hours"]), (100.0, 168.0))

    def test_provider_limit_test_runtime_reads_nothing(self):
        """With a test runtime (BZZ_DATA_DIR) and no limits file nothing is read from this PC."""
        usage.save_claude_usage(self.conn, self.event(self.now + 3600, self.now + 7200, seven=0.75))
        env = {"BZZ_DATA_DIR": self.tmp.name}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("BZZ_PROVIDER_LIMITS_FILE", None)
            lim = usage.get_provider_limit(self.conn, {"name": "c", "limit": {"source": "claude"}})
        self.assertEqual((lim["free_pct"], lim["hours"]), (100.0, 168.0))

    def test_provider_limit_file(self):
        """BZZ_PROVIDER_LIMITS_FILE overrides the sources (used by scheduler tests)."""
        f = Path(self.tmp.name) / "limits.json"
        f.write_text('{"c":{"usedPercent":80,"resetHours":24},"d":{"usedPercent":10}}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(f)}):
            c = usage.get_provider_limit(self.conn, {"name": "c", "limit": {"source": "claude"}})
            d = usage.get_provider_limit(self.conn, {"name": "d"})
            e = usage.get_provider_limit(self.conn, {"name": "e"})
        self.assertEqual((c["free_pct"], c["hours"]), (20.0, 24.0))
        self.assertEqual((d["free_pct"], d["hours"]), (90.0, 168.0))
        self.assertEqual((e["free_pct"], e["hours"]), (100.0, 168.0))

    def test_format_reset_time(self):
        """Today shows the system time, another day the system date and time, and no value an empty text."""
        self.assertEqual(usage.format_reset_time(None), "")
        now = datetime.now().replace(hour=12, minute=30, second=0, microsecond=0)
        self.assertEqual(usage.format_reset_time(now.timestamp()), now.strftime("%X"))
        later = now + timedelta(days=2)
        self.assertEqual(usage.format_reset_time(later.timestamp()), later.strftime("%x %X"))




if __name__ == "__main__":
    unittest.main()
