"""Tests of the overview data (haivequeen/overview.py) with invented data (temp database, no window)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import db, overview, runstate, runtime, usage  # noqa: E402

NOW = datetime(2026, 10, 28, 12, 0, 0)
SYS = {"cores": 12, "free_mb": 8192, "total_mb": 16384, "host": "h1", "compiler_count": None}


def iso(minutes_ago):
    """Time string a number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


# Test the overview data.
class OverviewTest(unittest.TestCase):
    """Collect with two invented projects."""

    def setUp(self):
        """Create a temp database with two projects, agents, plans, a question and usage rows."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "alpha", None, "cyan", 1)
        db.upsert_project(self.conn, "beta", None, "magenta", 0)
        runtime.upsert_agent(self.conn, "alpha", "alpha-job-one", {
            "plan": "docs/plans/A-PLAN.md", "package": "P1", "phase": "work", "provider": "claude",
            "model": "claude-sonnet-5-5", "effort": "medium", "status": "running", "startedAt": iso(5),
            "tokensIn": 120000, "tokensOut": 3000, "person": "Ann", "color": "FF8800", "ctxTokens": 310000,
            "buildJobs": 4})
        runtime.upsert_agent(self.conn, "beta", "beta-job-two", {
            "plan": "docs/plans/B-PLAN.md", "package": "P2", "phase": "verify", "provider": "codex",
            "model": "gpt-6.1-sol", "effort": "medium", "status": "done", "startedAt": iso(30), "endedAt": iso(10),
            "tokensIn": 5000, "tokensOut": 900})
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('alpha','docs/plans/A-PLAN.md','A','RUNNING')")
            self.conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('beta','docs/plans/NEW-PLAN.md','N','DRAFT')")
        db.add_question(self.conn, "beta", "docs/plans/B-PLAN.md", "I1", "Which?", "A")
        runtime.usage_add(self.conn, project="alpha", provider="claude", model="claude-sonnet-5-5", tokens_in=2000,
                          tokens_out=100, kind="log", ts=iso(60))
        runtime.usage_add(self.conn, project="alpha", provider="claude", model="claude-sonnet-5-5", tokens_in=700,
                          tokens_out=50, kind="brain", ts=iso(20))
        runtime.kv_set(self.conn, "*", "usage-claude", '{"fiveHour":{"utilization":0.42,"resetsAt":%d},'
                       '"sevenDay":{"utilization":0.9,"resetsAt":%d}}' % (NOW.timestamp() + 3600, NOW.timestamp() + 86400 * 3))

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def data(self, **kw):
        """collect() with the fixed clock and system values unless asked otherwise."""
        args = dict(now=NOW, cfg={"buildJobs": 1}, sysinfo=dict(SYS))
        args.update(kw)
        project = args.pop("project", None)
        return overview.collect(self.conn, project, **args)

    # Check the collected sections.
    def test_sections_and_projects(self):
        """Both projects, the running and the ended agent, the plan counts, the open question and the Claude quota are collected."""
        d = self.data()
        self.assertEqual([a["name"] for a in d["agents"]], ["alpha-job-one"])
        self.assertEqual([a["name"] for a in d["fin"]], ["beta-job-two"])
        self.assertEqual(d["projects"], ["alpha", "beta"])
        self.assertEqual((d["counts"]["RUNNING"], d["counts"]["DRAFT"]), (1, 1))
        self.assertEqual(d["drafts"], ["NEW"])
        self.assertEqual([q["project"] for q in d["qs"]], ["beta"])
        self.assertEqual(d["cu"]["FiveHour"]["pct"], 42)
        self.assertEqual(d["cu"]["SevenDay"]["pct"], 90)
        self.assertEqual(overview.fmt_tok0(310000), "310k")
        self.assertEqual(overview.fmt_tok0(None), "–")

    # Check that Codex usage is read from the session logs.
    def test_codex_limits_from_session_logs(self):
        """Invented Codex session logs in a temp profile: collect writes kv usage-codex and returns the 5 hour percent."""
        home = Path(self.tmp.name) / "home"
        day = home / "codex" / "sessions" / "2026" / "10" / "28"
        day.mkdir(parents=True)
        t = int(datetime.now().timestamp())
        rl = {"primary": {"used_percent": 37.0, "window_minutes": 300, "resets_at": t + 3600},
              "secondary": {"used_percent": 64.0, "window_minutes": 10080, "resets_at": t + 86400 * 3}}
        line = json.dumps({"payload": {"rate_limits": rl}}, separators=(",", ":"))
        (day / "rollout-a.jsonl").write_text('{"x":1}\n' + line + "\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "BZZ_DATA_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(usage, "home_dir", return_value=home):
            usage._codex_refresh.clear()
            d = overview.collect(self.conn, None, datetime.now(), {"providers": [{"name": "codex", "protocol": "codex", "limit": {"source": "codex"}}]}, dict(SYS))
        self.assertIsNotNone(runtime.kv_get(self.conn, "*", "usage-codex"))
        self.assertEqual(d["xu"]["FiveHour"]["pct"], 37)

    def test_build_jobs_line(self):
        """The build line shows the running job count during a build, else the count acquire would hand out and the median per job; old measurements are not shown."""
        self.assertIn("Build jobs 4 of 12 cores (running) | per job not measured yet", self.data(project="alpha")["bj"])
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO build_measurements (host, project, ts, jobs, per_job_mb) VALUES ('h1','alpha','x',2,900)")   # old whole-machine value: ignored
        s = dict(SYS, compiler_count=3)
        self.assertIn("Build jobs 1 of 12 cores (next build) | per job not measured yet", self.data(sysinfo=s, project="beta")["bj"])
        with db.transaction(self.conn):
            self.conn.execute("UPDATE agents SET status='done', ended_at=? WHERE project='alpha'", (iso(1),))
            for ts, per in (("a", 900), ("b", 700), ("c", 800)):
                self.conn.execute("INSERT INTO build_measurements (host, project, ts, jobs, per_job_mb, peak_mb, parallel) VALUES ('h1','alpha',?,2,?,?,1)", (ts, per, per * 2))
        bj = self.data(sysinfo=s, project="alpha", cfg={"buildJobs": {"cores": 5, "reserveMB": 1024}})["bj"]
        self.assertIn("Build jobs 5 of 12 cores (next build) | per job 800 MB | active: 3 cores", bj)
        both = self.data(sysinfo=s, cfg={"buildJobs": {"cores": 5, "perJobMB": "auto"}})["bj"]   # one line, every project with its own median
        self.assertIn("alpha: Build jobs 5 of 12 cores (next build) | per job 800 MB", both)
        self.assertIn("beta: Build jobs 2 of 12 cores (next build) | per job not measured yet", both)
        self.assertIsNone(self.data(sysinfo=dict(s, build_projects=[]))["bj"])   # no project with buildProcessNames: no build line

    def test_build_line_without_build_table(self):
        """No buildJobs table (or 0) means the defaults: the line stays, with and without a stored measurement."""
        for cfg in ({}, {"buildJobs": 0}):
            line = self.data(cfg=cfg, project="beta")["bj"]
            self.assertIsNotNone(line)
            self.assertIn("Build jobs 2 of 12 cores (next build) | per job not measured yet", line)
        with db.transaction(self.conn):
            for ts, per in (("a", 900), ("b", 700), ("c", 800)):
                self.conn.execute("INSERT INTO build_measurements (host, project, ts, jobs, per_job_mb, peak_mb, parallel) VALUES ('h1','beta',?,2,?,?,1)", (ts, per, per * 2))
        self.assertIn("per job 800 MB", self.data(cfg={}, project="beta")["bj"])
        self.assertIsNone(self.data(cfg={}, sysinfo=dict(SYS, build_projects=[]))["bj"])

    def test_project_filter(self):
        """With a project filter only its agents, plans and questions remain."""
        d = self.data(project="alpha")
        self.assertEqual([a["name"] for a in d["agents"]], ["alpha-job-one"])
        self.assertEqual(d["fin"], [])
        self.assertEqual(d["qs"], [])
        self.assertEqual(d["counts"]["DRAFT"], 0)

    def test_run_state(self):
        """The run state follows the pause."""
        self.assertEqual(self.data()["run"]["state"], "RUNNING")
        runstate.pause(self.conn, "test")
        self.assertEqual(self.data()["run"]["state"], "PAUSED")

    def test_empty_database(self):
        """An empty database collects without error."""
        conn = db.connect(Path(self.tmp.name) / "empty.db")
        d = overview.collect(conn, None, NOW, {}, dict(SYS))
        conn.close()
        self.assertEqual((d["agents"], d["fin"], d["usage"], d["qs"]), ([], [], [], []))

    def test_blocked(self):
        """Two failed starts with the same error block the task."""
        for i in (3, 2):
            runtime.upsert_agent(self.conn, "alpha", "fail-%d" % i, {
                "plan": "p.md", "package": "X", "phase": "work", "status": "failed",
                "startedAt": iso(i * 10), "endedAt": iso(i), "lastLine": "boom"})
        blocked = self.data()["blocked"]
        self.assertEqual([(b["name"], b["error"]) for b in blocked], [("fail-2", "boom")])

    # Check that only two recent newest failures without a later run are blocked.
    def test_blocked_requires_recent_two_failed_runs_and_no_newer_run(self):
        """Only a nonarchived task with two newest identical failures ending within 12h is blocked."""
        # Add one invented run for a task package.
        def add(name, status, minutes_ago, package, line="boom", ended=True):
            runtime.upsert_agent(self.conn, "alpha", name, {"plan": "p.md", "package": package, "phase": "work",
                "status": status, "startedAt": iso(minutes_ago + 1), "endedAt": iso(minutes_ago) if ended else None, "lastLine": line})
        add("recent-1", "failed", 30, "recent")
        add("recent-2", "failed", 10, "recent")
        add("old-1", "failed", 60 * 13, "old")
        add("old-2", "failed", 60 * 12 + 1, "old")
        add("archived-1", "failed", 20, "archived")
        add("archived-2", "failed", 10, "archived")
        self.conn.execute("UPDATE agents SET archived=1 WHERE name LIKE 'archived-%'")
        add("later-done-1", "failed", 20, "later")
        add("later-done-2", "failed", 10, "later")
        add("later-done-3", "done", 5, "later")
        add("later-running-1", "failed", 20, "running")
        add("later-running-2", "failed", 10, "running")
        add("later-running-3", "running", 5, "running", ended=False)
        got = {row["name"] for row in overview.blocked_tasks(runtime.list_agents(self.conn, scope="all"), NOW)}
        self.assertIn("recent-2", got)
        self.assertNotIn("old-2", got)
        self.assertNotIn("archived-2", got)
        self.assertNotIn("later-done-2", got)
        self.assertNotIn("later-running-2", got)

    # Check quota, reset, slot, model and scheduler marker data for providers.
    def test_provider_limits_show_weekly_share_slots_model_and_next(self):
        """Provider rows hold the weekly quota and reset, free slots and medium models; only the scheduler choice is marked next."""
        providers = []
        for name, use, source in (("green-provider", "preferred", "codex"), ("yellow-provider", "fallback", "codex"),
                                  ("red-provider", "fallback", "codex"), ("unknown-provider", "fallback", "claude"),
                                  ("no-reset-provider", "fallback", "codex"),
                                  ("lmstudio", "fallback", ""),
                                  ("local", "fallback", "")):
            access = {"access": "llm"} if name in ("lmstudio", "local") else {}   # a Local LLM has no usage limit
            providers.append({"name": name, "protocol": "codex", "use": use, "slots": 2, "limit": source, **access,
                "classes": {"medium": {"model": name + "-model"}}})
        limits = Path(self.tmp.name) / "limits.json"
        limits.write_text(json.dumps({"green-provider": {"usedPercent": 50, "resetHours": 84},
            "yellow-provider": {"usedPercent": 80, "resetHours": 48},
            "red-provider": {"usedPercent": 95, "resetHours": 24},
            "no-reset-provider": {"usedPercent": 33}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(limits)}):
            rows = overview.provider_lines(self.conn, {"providers": providers}, [])
        for got, want in zip([r["ratio"] for r in rows[:3]], [1.0, 0.7, 0.35]):
            self.assertAlmostEqual(got, want)
        self.assertEqual([r["next"] for r in rows], [True, False, False, False, False, False, False])
        self.assertFalse(rows[5]["has_limit"])
        self.assertIsNone(rows[5]["ratio"])
        self.assertEqual(rows[0]["model"], "green-provider-model")
        self.assertEqual(rows[0]["used_pct"], 50.0)
        self.assertIsNone(rows[4]["hours"])
        exhausted = [{"provider": name, "status": "running"} for name in ("green-provider", "yellow-provider", "red-provider", "unknown-provider", "no-reset-provider", "lmstudio", "local") for _ in range(2)]
        exhausted.extend([{"provider": "green-provider", "status": "starting"}])
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(limits)}):
            exhausted_rows = overview.provider_lines(self.conn, {"providers": providers}, exhausted)
        self.assertFalse(any(row["next"] for row in exhausted_rows))
        self.assertEqual(exhausted_rows[0]["used_slots"], 3)


class MixedTimeTest(unittest.TestCase):
    """Times with and without a UTC offset (imported v1 rows next to new rows) must not break the readers."""

    def test_parse_time_is_naive_local(self):
        """db.parse_time returns local time without offset for naive and offset input, the default for junk."""
        want = datetime(2026, 10, 28, 9, 0, 0).replace(tzinfo=None)
        off = db.parse_time("2026-10-28T10:00:00.1234567+01:00")
        self.assertIsNone(off.tzinfo)
        self.assertEqual(db.parse_time("2026-10-28T09:00:00"), want)
        self.assertIsNone(db.parse_time("junk"))
        self.assertEqual(db.parse_time("", "d"), "d")

    def test_usage_models_with_mixed_times(self):
        """providers_usage rows with and without offset are summed without a naive/aware comparison error."""
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        db.upsert_project(conn, "alpha", None, "cyan", 1)
        runtime.usage_add(conn, kind="brain", project="alpha", model="m", tokens_in=1, tokens_out=2,
                          ts="2026-10-28T12:48:59.9456903+01:00")
        runtime.usage_add(conn, kind="brain", project="alpha", model="m", tokens_in=3, tokens_out=4,
                          ts="2026-10-28T12:50:00")
        rows = overview.usage_models(conn, [], datetime(2026, 10, 28, 0, 0, 0))
        self.assertEqual(rows[0]["Today"], (4, 6))

    def test_usage_one_entry_per_model(self):
        """The label "Sonnet 5.5" (older rows, written before the key was unified) and the id "claude-sonnet-5-5" are one entry with summed tokens."""
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        db.upsert_project(conn, "alpha", None, "cyan", 1)
        self.assertEqual(runtime.model_key("Sonnet 5.5"), "claude-sonnet-5-5")
        self.assertEqual(runtime.model_key("claude-sonnet-5-5[1m]"), "claude-sonnet-5-5")
        self.assertEqual(runtime.model_key("gpt-6.1-terra"), "gpt-6.1-terra")
        runtime.usage_add(conn, kind="brain", project="alpha", model="Sonnet 5.5", tokens_in=10, tokens_out=1, ts="2026-10-28T12:00:00")
        self.assertEqual(conn.execute("SELECT model FROM providers_usage").fetchone()[0], "claude-sonnet-5-5")
        conn.execute("INSERT INTO providers_usage (ts, model, tokens_in, tokens_out, kind, project) VALUES ('2026-10-28T12:01:00','Sonnet 5.5',20,2,'brain','alpha')")
        conn.commit()
        agent = {"model": "claude-sonnet-5-5", "tokens_in": 100, "tokens_out": 5, "started_at": "2026-10-28T12:02:00"}
        rows = overview.usage_models(conn, [agent], datetime(2026, 10, 28, 0, 0, 0))
        self.assertEqual([r["Label"] for r in rows], ["Sonnet 5.5"])
        self.assertEqual(rows[0]["Plans"], (130, 8))

    def test_usage_bare_family_name_joins_its_model_and_empty_rows_are_left_out(self):
        """A row that names only the family ("Sonnet") adds its numbers to the one model of that family; a model whose today, plans and total are all 0 gets no row."""
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        db.upsert_project(conn, "alpha", None, "cyan", 1)
        runtime.usage_add(conn, kind="brain", project="alpha", model="claude-sonnet-5-5", tokens_in=10, tokens_out=1, ts="2026-10-28T12:00:00")
        runtime.usage_add(conn, kind="brain", project="alpha", model="Sonnet", tokens_in=5, tokens_out=2, ts="2026-10-28T12:01:00")
        runtime.usage_add(conn, kind="brain", project="alpha", model="Opus", tokens_in=None, tokens_out=None, ts="2026-10-28T12:02:00")   # no such model with numbers: all 0
        runtime.usage_add(conn, kind="brain", project="alpha", model="gpt-6.1-sol", tokens_in=0, tokens_out=0, ts="2026-10-28T12:03:00")
        rows = overview.usage_models(conn, [], datetime(2026, 10, 28, 0, 0, 0))
        self.assertEqual([r["Label"] for r in rows], ["Sonnet 5.5"])
        self.assertEqual((rows[0]["Today"], rows[0]["Plans"], rows[0]["Total"]), ((15, 3), (15, 3), (15, 3)))

    def test_usage_bare_family_name_alone_with_numbers_stays(self):
        """A bare family name without any full model of that family keeps its row when it has numbers."""
        tmp = tempfile.TemporaryDirectory()
        conn = db.connect(Path(tmp.name) / "o.db")
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        db.upsert_project(conn, "alpha", None, "cyan", 1)
        runtime.usage_add(conn, kind="brain", project="alpha", model="Opus", tokens_in=7, tokens_out=1, ts="2026-10-28T12:02:00")
        rows = overview.usage_models(conn, [], datetime(2026, 10, 28, 0, 0, 0))
        self.assertEqual([r["Label"] for r in rows], ["Opus"])


class RoundErrorTest(OverviewTest):
    """A failed round is collected for the dashboard (the Beekeeper has no window of its own)."""

    def add(self, text, minutes_ago, kind="rounds"):
        """An event of the project alpha written `minutes_ago` minutes before NOW."""
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO events (ts, project, kind, text) VALUES (?,?,?,?)", (iso(minutes_ago), "alpha", kind, text))

    def test_recent_round_errors_are_listed(self):
        """The round errors of the last day are collected with their text; an older one and other lines are not."""
        self.add("ROUND ERROR boom in plan scan", 5)
        self.add("ROUND ERROR long ago", 60 * 30)
        self.add("start Ada (claude, queen)", 4)
        texts = [e["text"] for e in self.data()["round_errors"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("boom in plan scan", texts[0])

    def test_no_section_without_errors(self):
        """Without a round error the list is empty."""
        self.assertEqual(self.data()["round_errors"], [])


if __name__ == "__main__":
    unittest.main()
