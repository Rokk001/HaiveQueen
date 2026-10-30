"""Tests of the dashboard text with invented data (no terminal, temp database)."""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import dashboard, db, runstate, runtime, usage  # noqa: E402

NOW = datetime(2026, 10, 28, 12, 0, 0)
SYS = {"cores": 12, "free_mb": 8192, "total_mb": 16384, "host": "h1", "compiler_count": None}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def iso(minutes_ago):
    """Time string a number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


class DashboardTest(unittest.TestCase):
    """Render with two invented projects."""

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
            self.conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('alpha','docs/plans/A-PLAN.md','A','IN_PROGRESS')")
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

    def text(self, **kw):
        """Render without colours unless asked."""
        args = dict(width=100, height=60, now=NOW, cfg={"buildJobs": 1}, sysinfo=dict(SYS), color=False)
        args.update(kw)
        return dashboard.render(self.conn, **args)

    def test_sections_and_projects(self):
        """All sections show; both projects appear in agent rows; texts are English."""
        t = self.text()
        for s in ("AGENTS 1", "LIMITS", "USAGE", "SYSTEM", "PLANS", "QUESTIONS", "RUNNING", "alpha | ANN", "beta | ",
                  "ended in the last hour", "Claude 5h", " 42%", " 90%", "open: 1  beta#", "draft: NEW"):
            self.assertIn(s, t)
        self.assertIn("▲ Context 310k", t)
        self.assertIn("Total Input 2k / Output 100", t)
        self.assertIn("Total Input 120k / Output 3k", t)

    def test_codex_limits_from_session_logs(self):
        """Invented Codex session logs in a temp home: the dashboard writes kv usage-codex and shows percent and reset instead of n/a."""
        home = Path(self.tmp.name) / "home"
        day = home / ".codex" / "sessions" / "2026" / "10" / "28"
        day.mkdir(parents=True)
        t = int(datetime.now().timestamp())
        rl = {"primary": {"used_percent": 37.0, "window_minutes": 300, "resets_at": t + 3600},
              "secondary": {"used_percent": 64.0, "window_minutes": 10080, "resets_at": t + 86400 * 3}}
        line = json.dumps({"payload": {"rate_limits": rl}}, separators=(",", ":"))
        (day / "rollout-a.jsonl").write_text('{"x":1}\n' + line + "\n", encoding="utf-8")
        before = self.text()
        self.assertIn("n/a", before)
        env = {k: v for k, v in os.environ.items() if k != "BZZ_DATA_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(usage, "home_dir", return_value=home):
            usage._codex_refresh.clear()
            out = dashboard.render(self.conn, width=100, height=60, now=datetime.now(), cfg={}, sysinfo=dict(SYS), color=False)
        self.assertIsNotNone(runtime.kv_get(self.conn, "*", "usage-codex"))
        codex_line = [x for x in out.split("\n") if "Codex" in x and "5h" in x][0]
        self.assertIn("37%", codex_line)
        self.assertNotIn("n/a", codex_line)
        self.assertIn("64%", [x for x in out.split("\n") if "Codex" in x and "7d" in x][0])

    def test_build_jobs_line(self):
        """The build-jobs line shows jobs, cores, per-job use and the active cores of a running build."""
        self.assertIn("Build jobs 4 of 12 cores (running) | per job not measured yet", self.text())
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO build_measurements (host, project, ts, jobs, per_job_mb) VALUES ('h1','alpha','x',2,900)")
        s = dict(SYS, compiler_count=3)
        self.assertIn("Build jobs 1 of 12 cores (next build) | per job not measured yet", self.text(sysinfo=s, project="beta"))
        with db.transaction(self.conn):
            self.conn.execute("UPDATE agents SET status='done', ended_at=? WHERE project='alpha'", (iso(1),))
        t = self.text(sysinfo=s, cfg={"buildJobs": {"coreShare": "1/2", "reserveMB": 1024}})
        self.assertIn("Build jobs 1 of 12 cores (next build) | per job 900 MB | active: 3 cores", t)   # the owner's rule: the next build always gets one job
        self.assertNotIn("Build jobs", self.text(cfg={}))

    def test_project_filter(self):
        """With a project filter only its agents, plans and questions remain."""
        t = self.text(project="alpha")
        self.assertIn("alpha-job-one", t)
        self.assertNotIn("beta-job-two", t)
        self.assertIn("none open", t)

    def press(self, key, t=None):
        """A confirmed press of P or D: the same key twice, one second apart (now by default)."""
        t = time.monotonic() if t is None else t
        dashboard.handle_key(self.conn, key, None, [], now=t)
        dashboard.handle_key(self.conn, key, None, [], now=t + 1)

    def test_single_press_changes_nothing_double_press_does(self):
        """A single D changes nothing and asks for the confirmation; D-D drains, D-D again lifts the drain; a late second press counts as a first."""
        state = lambda: runstate.get_state(self.conn)["state"]  # noqa: E731
        dashboard.handle_key(self.conn, "d", None, [], now=10.0)
        self.assertEqual(state(), "RUNNING")
        self.assertEqual(dashboard.pending_confirm(11.0), "d")
        self.assertEqual(dashboard.pending_confirm(14.0), "", "after 3 s the confirmation is gone")
        dashboard.handle_key(self.conn, "d", None, [], now=14.0)   # too late: only a new first press
        self.assertEqual(state(), "RUNNING")
        dashboard.handle_key(self.conn, "d", None, [], now=15.0)
        self.assertIn(state(), ("DRAINING", "DRAINED"))
        self.press("d", 50.0)
        self.assertEqual(state(), "RUNNING")
        kinds = [(r[0], r[1]) for r in self.conn.execute("SELECT kind, text FROM events WHERE project='*' ORDER BY id")]
        self.assertTrue(all("dashboard key" in t for k, t in kinds if k in ("drain", "resume")), kinds)

    def test_questions_in_triage_are_counted(self):
        """Under QUESTIONS the dashboard shows how many questions wait for the triage."""
        self.assertNotIn("in triage", self.text())
        db.add_question(self.conn, "alpha", "x.md", "T1", "Which?", "A", status="triage")
        db.add_question(self.conn, "alpha", "x.md", "T2", "Which?", "A", status="triage")
        self.assertIn("in triage: 2", self.text())

    def test_run_state_colour_and_keys(self):
        """The run state shows coloured in the header; P and D change it; F cycles the filter."""
        self.assertIn("\x1b[92mRUNNING", self.text(color=True))
        self.press("p")
        self.assertIn("\x1b[93mPAUSED", self.text(color=True))
        self.press("p")
        self.assertEqual(runstate.get_state(self.conn)["state"], "RUNNING")
        self.press("d")
        self.assertIn("DRAINING", self.text())
        with db.transaction(self.conn):
            self.conn.execute("UPDATE agents SET status='done', ended_at=?", (iso(1),))
        self.assertIn("DRAINED", self.text())
        names = ["alpha", "beta"]
        self.assertEqual(dashboard.handle_key(self.conn, "f", None, names), "alpha")
        self.assertIsNone(dashboard.handle_key(self.conn, "f", "beta", names))

    def test_width_and_height(self):
        """No line is wider than the pane and the text never has more lines than the height."""
        for w, h in ((40, 25), (100, 12), (30, 60)):
            plain = ANSI.sub("", self.text(width=w, height=h, color=True)).split("\n")
            self.assertLessEqual(len(plain), h)
            self.assertTrue(all(len(x) <= w for x in plain), (w, [x for x in plain if len(x) > w]))
            self.assertNotIn("\x1b", self.text(width=w, height=h))

    def test_empty_database(self):
        """An empty database renders without error."""
        conn = db.connect(Path(self.tmp.name) / "empty.db")
        t = dashboard.render(conn, 80, 40, now=NOW, cfg={}, sysinfo=dict(SYS), color=False)
        conn.close()
        self.assertIn("no agents", t)
        self.assertIn("no data", t)

    def test_blocked(self):
        """Two failed starts with the same error block the task."""
        for i in (3, 2):
            runtime.upsert_agent(self.conn, "alpha", "fail-%d" % i, {
                "plan": "p.md", "package": "X", "phase": "work", "status": "failed",
                "startedAt": iso(i * 10), "endedAt": iso(i), "lastLine": "boom"})
        self.assertIn("BLOCKED fail-2: boom", self.text())


if __name__ == "__main__":
    unittest.main()


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
        rows = dashboard.usage_models(conn, [], datetime(2026, 10, 28, 0, 0, 0))
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
        rows = dashboard.usage_models(conn, [agent], datetime(2026, 10, 28, 0, 0, 0))
        self.assertEqual([r["Label"] for r in rows], ["Sonnet 5.5"])
        self.assertEqual(rows[0]["Plans"], (130, 8))
