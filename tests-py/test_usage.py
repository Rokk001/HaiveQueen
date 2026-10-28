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


class UsageTableTest(unittest.TestCase):
    """The scenarios of the PowerShell usage test, run against invented logs."""

    def setUp(self):
        """Build invented Claude and Codex logs, a temp home and a temp database with agent and brain rows."""
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.claude = self.base / "claude" / "proj"
        self.codex = self.base / "codex" / "2026" / "10" / "27"
        self.cache = self.base / "cache"
        for d in (self.home, self.claude, self.codex):
            d.mkdir(parents=True)
        patcher = mock.patch.object(usage, "home_dir", lambda: self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.today = today
        self.t12 = today + timedelta(hours=12)
        self.yest = today - timedelta(days=1) + timedelta(hours=12)
        t12, yest = self.t12, self.yest

        write(self.claude / "a.jsonl", [
            claude_line("msg_1", SONNET, t12, 1, 0, 50, 10),
            claude_line("msg_1", SONNET, t12, 1, 20, 400, 30),
            claude_line("msg_1", SONNET, t12, 1, 20, 400, 50),
            claude_line("msg_2", SONNET, yest, 100, 0, 0, 5),
            claude_line("msg_3", "claude-opus-4-1-20250805", t12, 7, 0, 0, 3),
            claude_line("msg_s", "<synthetic>", t12, 5, 5, 5, 5),
            json.dumps({"type": "user", "timestamp": ts(t12), "message": {"content": "hello"}}),
        ])
        write(self.claude / "b.jsonl", [
            claude_line("msg_1", SONNET, t12, 1, 20, 400, 50),
            claude_line("msg_4", SONNET, t12, 10, 0, 0, 1),
        ])
        write(self.codex / "rollout-s1.jsonl", [
            codex_ctx(yest, "gpt-6.1-sol"), codex_count(today - timedelta(hours=2), 1000, 100),
            codex_count(today - timedelta(minutes=10), 2000, 200), codex_count(today + timedelta(hours=10), 3500, 300),
            codex_count(today + timedelta(hours=11), 5000, 450)])
        write(self.codex / "rollout-s2.jsonl", [
            codex_ctx(t12, "gpt-6.1-sol"), codex_count(t12, 400, 40), codex_count(t12 + timedelta(minutes=1), 900, 90)])
        write(self.codex / "rollout-s3.jsonl", [
            codex_ctx(t12, "gpt-6.1-sol"),
            '{"timestamp":"%s","ordinal":3,"type":"event_msg","payload":{"type":"token_count","info":null}}' % ts(t12)])

        self.conn = db.connect(self.base / "t.db")
        self.addCleanup(self.conn.close)
        for name, started, prov, plan, tin, tout in (
                ("A1", "2026-10-27T08:00:00+01:00", "claude", "x/REAL-PLAN.md", 1000, 100),
                ("A2", "2026-10-27T08:01:00+01:00", "codex", "x/REAL-PLAN.md", 2000, 200),
                ("A3", "2026-10-27T08:02:00+01:00", "claude", "x/PILOT-PLAN.md", 999, 99),
                ("ZZTEST-x", "2026-10-27T08:03:00+01:00", "codex", "x/REAL-PLAN.md", 888, 88),
                ("A5", "2026-10-26T08:00:00+01:00", "claude", "x/REAL-PLAN.md", 500, 50)):
            runtime.upsert_agent(self.conn, "p", name, {"started_at": started, "provider": prov, "plan_path": plan,
                                                        "tokens_in": tin, "tokens_out": tout})
        self.conn.execute("UPDATE agents SET archived=1, status='done' WHERE name='A5'")
        runtime.usage_add(self.conn, kind="brain", project="p", model="Sonnet 5.5", tokens_in=300, tokens_out=30)
        runtime.usage_add(self.conn, kind="brain", project="p", model="Opus 4", tokens_in=777, tokens_out=77)

    def tearDown(self):
        """Remove the temp folder."""
        self.conn.close()
        self.tmp.cleanup()

    def run_usage(self, conn=True):
        """Call get_usage_table with the test log folders (and the test database)."""
        return usage.get_usage_table(self.conn if conn else None, cache_dir=self.cache,
                                     claude_dir=self.base / "claude", codex_dir=self.base / "codex", spawn_zst=False)

    @staticmethod
    def model(u, key):
        """Return the model entry with the given key."""
        return next(m for m in u["models"] if m["key"] == key)

    def test_totals_labels_and_plans(self):
        """Dedup, synthetic, day split, Codex differences, labels, plan sums and the cold run."""
        u = self.run_usage()
        s, o, x = self.model(u, SONNET), self.model(u, "claude-opus-4-1"), self.model(u, "gpt-6.1-sol")
        self.assertEqual([m["key"] for m in u["models"]], [SONNET, "claude-opus-4-1", "gpt-6.1-sol"])
        self.assertEqual(s["label"], "Claude Son 5.5")
        self.assertEqual(o["label"], "Claude Opu 4.1")
        self.assertEqual(x["label"], "Codex Sol 6.1")
        self.assertEqual(s["total"], {"in": 421 + 100 + 10, "out": 50 + 5 + 1})
        self.assertEqual(s["today"], {"in": 421 + 10, "out": 50 + 1})
        self.assertEqual((o["today"]["in"], o["today"]["out"]), (7, 3))
        self.assertEqual(s["since"].date(), self.yest.date())
        self.assertEqual(x["total"], {"in": 5000 + 900, "out": 450 + 90})
        self.assertEqual(x["today"], {"in": 3000 + 900, "out": 250 + 90})
        self.assertEqual(s["plans"], {"in": 1000 + 500 + 300, "out": 100 + 50 + 30})
        self.assertEqual(x["plans"], {"in": 2000, "out": 200})
        self.assertIsNone(o["plans"]["in"])
        self.assertFalse(s["no_data"])
        self.assertEqual(u["stats"]["files_read"], 5)
        self.assertIn("Claude Code", u["retention"])

    def test_without_database_has_no_plan_sums(self):
        """No connection: the plan sums stay empty and no extra model rows appear."""
        u = self.run_usage(conn=False)
        self.assertEqual(len(u["models"]), 3)
        self.assertTrue(all(m["plans"]["in"] is None for m in u["models"]))

    def test_plan_only_model_has_no_data(self):
        """A model with plan sums but no log data appears as a no-data row."""
        runtime.upsert_agent(self.conn, "p", "A9", {"provider": "codex", "tokens_in": 5, "tokens_out": 1})
        (self.base / "config.toml").write_text('model = "gpt-9-zed"\n', encoding="utf-8")
        u = usage.get_usage_table(self.conn, cache_dir=self.cache, claude_dir=self.base / "none",
                                  codex_dir=self.base / "sessions", spawn_zst=False)
        z = self.model(u, "gpt-9-zed")
        self.assertTrue(z["no_data"])
        self.assertEqual(z["label"], "Codex Zed 9")
        self.assertEqual(z["plans"], {"in": 2005, "out": 201})
        self.assertEqual([m["key"] for m in u["models"]], [SONNET, "gpt-9-zed"])
        self.assertIsNone(z["total"]["in"])

    def test_second_run_reads_nothing(self):
        """A second run reads no file, no bytes, does not write the cache and gives the same sums."""
        u = self.run_usage()
        cache_file = self.cache / "usage-cache.bin"
        stamp = cache_file.stat().st_mtime_ns
        time.sleep(0.05)
        u2 = self.run_usage()
        self.assertEqual(u2["stats"]["files_read"], 0)
        self.assertEqual(u2["stats"]["bytes_read"], 0)
        self.assertEqual(cache_file.stat().st_mtime_ns, stamp)
        self.assertEqual(self.model(u2, SONNET)["total"], self.model(u, SONNET)["total"])

    def test_incremental_append(self):
        """Appended lines are read from the saved offset only; a higher stream state of a message wins."""
        self.run_usage()
        add1 = claude_line("msg_5", SONNET, self.t12, 1, 0, 0, 1) + "\n" + claude_line("msg_4", SONNET, self.t12, 10, 0, 0, 4) + "\n"
        add2 = codex_count(self.t12 + timedelta(minutes=2), 1500, 150) + "\n"
        with open(self.claude / "b.jsonl", "ab") as fh:
            fh.write(add1.encode("utf-8"))
        with open(self.codex / "rollout-s2.jsonl", "ab") as fh:
            fh.write(add2.encode("utf-8"))
        u = self.run_usage()
        s, x = self.model(u, SONNET), self.model(u, "gpt-6.1-sol")
        self.assertEqual(u["stats"]["files_read"], 2)
        self.assertEqual(u["stats"]["bytes_read"], len(add1.encode("utf-8")) + len(add2.encode("utf-8")))
        self.assertEqual(s["total"]["in"], 531 + 1)
        self.assertEqual(s["total"]["out"], 56 + 3 + 1)
        self.assertEqual(x["today"]["in"], 3000 + 1500)
        self.assertEqual(x["total"]["out"], 450 + 150)

    def test_partial_last_line_is_kept_for_later(self):
        """An unfinished last line is not counted; once completed it is read from the saved offset."""
        line = claude_line("msg_7", SONNET, self.t12, 3, 0, 0, 2)
        with open(self.claude / "b.jsonl", "ab") as fh:
            fh.write(line[:40].encode("utf-8"))
        before = self.model(self.run_usage(), SONNET)["total"]["out"]
        with open(self.claude / "b.jsonl", "ab") as fh:
            fh.write((line[40:] + "\n").encode("utf-8"))
        after = self.model(self.run_usage(), SONNET)["total"]["out"]
        self.assertEqual(after, before + 2)

    def test_damaged_cache_rebuilds(self):
        """A damaged cache file gives a full rebuild with the same result."""
        u = self.run_usage()
        (self.cache / "usage-cache.bin").write_bytes(bytes([1, 2, 3]))
        u2 = self.run_usage()
        self.assertEqual(self.model(u2, SONNET)["total"]["out"], self.model(u, SONNET)["total"]["out"])
        self.assertEqual(u2["stats"]["files_read"], 5)

    def test_shrunk_file_is_read_again_from_start(self):
        """A file that became shorter than the saved size is re-read from offset 0 (Codex state reset)."""
        self.run_usage()
        write(self.codex / "rollout-s2.jsonl", [codex_ctx(self.t12, "gpt-6.1-sol"), codex_count(self.t12, 100, 10)])
        u = self.run_usage()
        self.assertEqual(self.model(u, "gpt-6.1-sol")["total"]["in"], 5000 + 100)

    def test_retention_reads_settings_and_never_writes_them(self):
        """cleanupPeriodDays comes from the (temp) home settings file, which stays byte-identical."""
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir()
        raw = b'{"cleanupPeriodDays": 7}'
        settings.write_bytes(raw)
        u = self.run_usage()
        self.assertIn("cleanupPeriodDays = 7", u["retention_claude"])
        self.assertEqual(settings.read_bytes(), raw)
        settings.unlink()
        self.assertIn("no cleanupPeriodDays", self.run_usage()["retention_claude"])

    def test_codex_retention_hit(self):
        """A retention line in the Codex config shows up in the Codex retention text."""
        (self.base / "config.toml").write_text('model = "x"\nhistory_max_age = 3\n', encoding="utf-8")
        self.assertIn("history_max_age = 3", self.run_usage()["retention_codex"])


class LabelTest(unittest.TestCase):
    """Model labels and name normalisation."""

    def test_labels(self):
        """Claude and Codex keys give the fixed label forms."""
        self.assertEqual(usage.model_label("claude-sonnet-5-5", False), "Claude Son 5.5")
        self.assertEqual(usage.model_label("claude-opus-4", False), "Claude Opu 4")
        self.assertEqual(usage.model_label("claude-3-5-haiku", False), "Claude Hai 3.5")
        self.assertEqual(usage.model_label("claude-weird", False), "Claude claude-weird")
        self.assertEqual(usage.model_label("gpt-6.1-sol", True), "Codex Sol 6.1")
        self.assertEqual(usage.model_label("gpt-5", True), "Codex 5")
        self.assertEqual(usage.model_label("o3", True), "Codex o3")
        self.assertEqual(usage.model_label("other", False), "other")

    def test_norm_claude(self):
        """Date and bracket suffixes go; synthetic and empty names give None."""
        self.assertEqual(usage.norm_claude("claude-opus-4-1-20250805"), "claude-opus-4-1")
        self.assertEqual(usage.norm_claude("claude-sonnet-5-5[1m]"), "claude-sonnet-5-5")
        self.assertIsNone(usage.norm_claude("<synthetic>"))
        self.assertIsNone(usage.norm_claude(""))


class ZstTest(unittest.TestCase):
    """Compressed Codex sessions (needs Python 3.14 compression.zstd)."""

    def setUp(self):
        """Create a temp folder with one invented compressed session."""
        if not usage.has_zstd():
            self.skipTest("compression.zstd not available")
        from compression import zstd
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.codex = self.base / "codex" / "2026"
        self.codex.mkdir(parents=True)
        when = datetime.now() - timedelta(days=3)
        body = "\n".join([codex_ctx(when, "gpt-7-zst"), codex_count(when, 100, 10),
                          codex_count(when + timedelta(minutes=1), 250, 25)]) + "\n"
        (self.codex / "old.jsonl.zst").write_bytes(zstd.compress(body.encode("utf-8")))
        self.when = when
        patcher = mock.patch.object(usage, "home_dir", lambda: self.base / "home")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_scan_zst_and_merge(self):
        """The helper scan fills its cache, skips unchanged files and the table adds the sums."""
        cache, lock = self.base / "zst-cache.json", self.base / "zst.lock"
        self.assertEqual(usage.scan_zst(self.codex, cache, lock), 1)
        self.assertFalse(lock.exists())
        entry = next(iter(json.loads(cache.read_text(encoding="utf-8"))["files"].values()))
        self.assertEqual((entry["model"], entry["in"], entry["out"]), ("gpt-7-zst", 250, 25))
        self.assertEqual(usage.scan_zst(self.codex, cache, lock), 0)
        u = usage.get_usage_table(None, cache_dir=self.base / "c", claude_dir=self.base / "none",
                                  codex_dir=self.base / "codex", spawn_zst=False)
        self.assertEqual(u["models"], [])
        self.assertEqual(u["stats"]["codex_skipped_files"], 1)
        (self.base / "c" / "zst-cache.json").write_text(cache.read_text(encoding="utf-8"), encoding="utf-8")
        u = usage.get_usage_table(None, cache_dir=self.base / "c", claude_dir=self.base / "none",
                                  codex_dir=self.base / "codex", spawn_zst=False)
        m = u["models"][0]
        self.assertEqual((m["key"], m["total"]["in"], m["total"]["out"]), ("gpt-7-zst", 250, 25))
        self.assertIsNotNone(m["since"])

    def test_helper_process_runs(self):
        """start_zst_helper starts a real helper that fills the cache; a held lock stops a second start."""
        cache, lock = self.base / "zc.json", self.base / "zl.lock"
        proc = usage.start_zst_helper(self.codex, cache, lock, sys.executable)
        self.assertIsNotNone(proc)
        proc.wait(timeout=60)
        self.assertTrue(cache.is_file())
        lock.write_text(str(os.getpid()))
        self.assertIsNone(usage.start_zst_helper(self.codex, cache, lock, sys.executable))

    def test_no_python_no_helper(self):
        """Without any Python that has zstd no helper is started."""
        with mock.patch.object(usage, "has_zstd", lambda: False):
            self.assertIsNone(usage.start_zst_helper(self.codex, self.base / "a", self.base / "b", ""))


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
        """Today shows HH:mm, another day dd.MM. HH:mm, no value an empty text."""
        self.assertEqual(usage.format_reset_time(None), "")
        now = datetime.now().replace(hour=12, minute=30, second=0, microsecond=0)
        self.assertEqual(usage.format_reset_time(now.timestamp()), "12:30")
        later = now + timedelta(days=2)
        self.assertEqual(usage.format_reset_time(later.timestamp()), later.strftime("%d.%m. 12:30"))


class PathTest(unittest.TestCase):
    """Default folders per OS."""

    def test_default_roots(self):
        """Log folders hang below the home folder; the cache sits in the data folder."""
        with mock.patch.object(usage, "home_dir", lambda: Path("/h")):
            self.assertEqual(usage.claude_root(), Path("/h/.claude/projects"))
            self.assertEqual(usage.codex_root(), Path("/h/.codex/sessions"))
            self.assertEqual(usage.claude_settings_path(), Path("/h/.claude/settings.json"))
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": "/d"}):
            self.assertEqual(usage.default_cache_dir(), Path("/d/usage-cache"))


if __name__ == "__main__":
    unittest.main()
