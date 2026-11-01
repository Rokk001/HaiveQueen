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


# Test dashboard behavior.
class DashboardTest(unittest.TestCase):
    """Render with two invented projects."""

    # Create isolated dashboard data and a temporary STOP flag.
    def setUp(self):
        """Create a temp database with two projects, agents, plans, a question and usage rows."""
        self.tmp = tempfile.TemporaryDirectory()
        stop = mock.patch('haivequeen.loop.stop_flag', return_value=Path(self.tmp.name) / 'STOP')
        stop.start()
        self.addCleanup(stop.stop)
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

    # A size read failure must be displayed instead of terminating the dashboard.
    def test_run_displays_initial_screen_size_error(self):
        """An initial terminal-size failure is shown in the dashboard pane."""
        import io
        output = io.StringIO()
        with mock.patch.object(dashboard.db, "connect", return_value=mock.Mock()), \
             mock.patch.object(dashboard.runtime, "kv_get", return_value=None), \
             mock.patch.object(dashboard, "screen_size", side_effect=RuntimeError("size unavailable")), \
             mock.patch.object(dashboard.header, "enable_vt"), \
             mock.patch.object(dashboard, "KeyReader"), \
             mock.patch.object(dashboard.sys, "stdout", output):
            self.assertEqual(dashboard.run(once=True), 0)
        self.assertIn("dashboard error: size unavailable", output.getvalue())

    # A database writer must not hold the dashboard beyond its refresh interval.
    def test_locked_database_shows_error_within_refresh_interval(self):
        """A locked dashboard height update produces a visible error promptly."""
        import io
        output = io.StringIO()
        conn = db.connect(Path(self.tmp.name) / 'o.db')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            with mock.patch.object(dashboard.db, 'connect', return_value=conn), \
                 mock.patch.object(dashboard.header, 'enable_vt'), \
                 mock.patch.object(dashboard, 'KeyReader'), \
                 mock.patch.object(dashboard.sys, 'stdout', output):
                start = time.monotonic()
                dashboard.run(once=True)
                elapsed = time.monotonic() - start
        finally:
            self.conn.execute('ROLLBACK')
        self.assertLess(elapsed, dashboard.REFRESH_SECONDS)
        self.assertIn('dashboard error:', output.getvalue())

    # A key handling error must be visible without ending dashboard refreshes.
    def test_run_displays_key_error_and_keeps_refreshing(self):
        """A failed key action is displayed and a later frame is drawn."""
        import io
        output = io.StringIO()
        keys = mock.MagicMock()
        keys.__enter__.return_value.get.side_effect = ["f", KeyboardInterrupt()]
        conn = mock.Mock()
        conn.execute.return_value = []
        with mock.patch.object(dashboard.db, "connect", return_value=conn), \
             mock.patch.object(dashboard.runtime, "kv_get", return_value=None), \
             mock.patch.object(dashboard, "render", return_value="frame"), \
             mock.patch.object(dashboard, "KeyReader", return_value=keys), \
             mock.patch.object(dashboard.header, "enable_vt"), \
             mock.patch.object(dashboard, "handle_key", side_effect=RuntimeError("key unavailable")), \
             mock.patch.object(dashboard.sys, "stdout", output):
            self.assertEqual(dashboard.run(), 0)
        self.assertIn("dashboard error: key unavailable", output.getvalue())
        self.assertGreaterEqual(output.getvalue().count("frame"), 2)

    # Input polling failures must be visible and must not end dashboard refreshes.
    def test_run_displays_input_error_and_keeps_refreshing(self):
        """A failed input read is displayed and a later frame is drawn."""
        import io
        output = io.StringIO()
        keys = mock.MagicMock()
        keys.__enter__.return_value.get.side_effect = [OSError("input unavailable"), KeyboardInterrupt()]
        with mock.patch.object(dashboard.db, "connect", return_value=mock.Mock()), \
             mock.patch.object(dashboard.runtime, "kv_get", return_value=None), \
             mock.patch.object(dashboard, "render", return_value="frame"), \
             mock.patch.object(dashboard, "KeyReader", return_value=keys), \
             mock.patch.object(dashboard.header, "enable_vt"), \
             mock.patch.object(dashboard.time, "sleep"), \
             mock.patch.object(dashboard.sys, "stdout", output):
            self.assertEqual(dashboard.run(), 0)
        self.assertIn("dashboard error: input unavailable", output.getvalue())
        self.assertGreaterEqual(output.getvalue().count("frame"), 2)

    # Check that the dashboard retains sections and omits the removed weekly rows.
    def test_sections_and_projects(self):
        """All sections show; both projects appear in agent rows; texts are English."""
        t = self.text()
        for s in ("AGENTS 1", "LIMITS", "USAGE", "SYSTEM", "PLANS", "QUESTIONS", "RUNNING", "alpha | ANN", "beta | ",
                  "ended in the last hour", "Claude 5h", " 42%", "open: 1  beta#", "draft: NEW"):
            self.assertIn(s, t)
        self.assertNotIn("Claude 7d", t)
        self.assertNotIn("Codex 7d", t)
        self.assertIn("▲ Context 310k", t)
        self.assertIn("Total Input 2k / Output 100", t)
        self.assertIn("Total Input 120k / Output 3k", t)

    def test_beekeeper_shows_round_state_and_next_round(self):
        """The dashboard reports the loop's actual state and next round."""
        runtime.kv_set(self.conn, "*", "loop-state", json.dumps({"status": "round", "since": iso(5), "nextAt": iso(-1)}))
        t = self.text()
        self.assertIn("Beekeeper running", t)
        self.assertIn("12:01", t)

    def test_beekeeper_header_shows_pause(self):
        """The Beekeeper header reflects the global pause."""
        runstate.pause(self.conn)
        self.assertIn("Beekeeper paused", self.text())

    def test_beekeeper_header_shows_stop(self):
        """The Beekeeper header shows a pending STOP."""
        from unittest import mock
        with mock.patch('haivequeen.loop.stop_flag') as flag:
            flag.return_value.exists.return_value = True
            self.assertIn("Beekeeper STOP", self.text())

    def test_filter_is_persisted(self):
        """The Apiary filter survives a dashboard restart."""
        dashboard.handle_key(self.conn, "f", None, ["alpha"])
        self.assertEqual(runtime.kv_get(self.conn, "*", "dashboard-filter"), "alpha")

    def test_dashboard_restart_restores_filter(self):
        """A newly started dashboard renders the previously selected Apiary."""
        import io
        dashboard.handle_key(self.conn, "f", None, ["alpha"])
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'o.db'), \
             mock.patch('haivequeen.dashboard.render', return_value='frame') as render, \
             mock.patch('haivequeen.dashboard.KeyReader'), mock.patch('sys.stdout', new_callable=io.StringIO):
            dashboard.run(once=True)
        self.assertEqual(render.call_args.args[3], 'alpha')

    # Verify that block layout can use the dashboard's measured height.
    def test_dashboard_records_actual_rows(self):
        """The dashboard publishes the terminal height for Apiary layout."""
        import io
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'o.db'), \
             mock.patch('haivequeen.dashboard.render', return_value='frame'), \
             mock.patch('haivequeen.dashboard.KeyReader'), mock.patch('sys.stdout', new_callable=io.StringIO), \
             mock.patch('shutil.get_terminal_size', return_value=os.terminal_size((100, 40))):
            dashboard.run(once=True)
        self.assertEqual(runtime.kv_get(self.conn, '*', 'dashboard-rows'), '40')

    def test_beekeeper_events_are_visible_without_three_line_limit(self):
        """The dashboard retains recent Beekeeper events in newest-first order."""
        for i in range(5):
            db.add_event(self.conn, "alpha", "events", "beekeeper event %d" % i)
        events = dashboard.round_errors(self.conn, None, datetime.now())
        self.assertEqual([e["text"] for e in events[:5]], ["beekeeper event %d" % i for i in range(4, -1, -1)])

    # Verify that an Apiary filter retains events without an Apiary.
    def test_filter_keeps_global_beekeeper_events(self):
        """An Apiary filter keeps events that have no Apiary."""
        db.add_event(self.conn, "*", "events", "global event")
        db.add_event(self.conn, "", "events", "unnamed global event")
        db.add_event(self.conn, "other", "events", "other event")
        events = dashboard.round_errors(self.conn, "alpha", datetime.now())
        self.assertIn("global event", [e["text"] for e in events])
        self.assertIn("unnamed global event", [e["text"] for e in events])
        self.assertNotIn("other event", [e["text"] for e in events])

    # Show when no unfinished Hives remain.
    def test_dashboard_shows_no_hives_when_all_are_done(self):
        from haivequeen import hives
        hives.create(self.conn, 'alpha', 'plans/x.md', 'claude', 'white')
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES ('alpha','plans/x.md','DONE')")
        self.assertIn('Keine Hives', self.text())

    def test_empty_registered_apiaries_are_shown_with_no_hives(self):
        """Registered Apiaries remain visible when no unfinished Hive exists."""
        text = self.text()
        self.assertIn('alpha: 0 Hives', text)
        self.assertIn('beta: 0 Hives', text)
        self.assertIn('Keine Hives', text)
        filtered = self.text(project='alpha')
        self.assertIn('alpha: 0 Hives', filtered)
        self.assertNotIn('beta: 0 Hives', filtered)
        self.assertIn('Keine Hives', filtered)

    def test_hive_count_excludes_done_plan(self):
        """A stale Hive row does not count after its plan is DONE."""
        from haivequeen import hives
        hives.create(self.conn, "alpha", "plans/x.md", "claude", "white")
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES ('alpha','plans/x.md','DONE')")
        self.assertNotIn(("alpha", 1), dashboard.hive_counts(self.conn, None))

    def test_removed_apiary_is_not_shown_even_with_a_stale_hive(self):
        """A removed Apiary does not appear through its remaining Hive row."""
        from haivequeen import hives
        hives.create(self.conn, 'alpha', 'plans/x.md', 'claude', 'white')
        self.conn.execute("DELETE FROM projects WHERE name='alpha'")
        self.assertNotIn('alpha: 1 Hives', self.text())

    def test_hive_count_includes_unfinished_plan_with_stale_hive_state(self):
        """An unfinished plan counts even if its Hive row still says done."""
        from haivequeen import hives
        hives.create(self.conn, 'alpha', 'plans/x.md', 'claude', 'white')
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES ('alpha','plans/x.md','IN_PROGRESS')")
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path='plans/x.md'")
        self.assertIn(('alpha', 1), dashboard.hive_counts(self.conn, None))

    def test_hive_count_includes_plan_without_done_status(self):
        """A plan with no status is not treated as DONE."""
        from haivequeen import hives
        hives.create(self.conn, 'alpha', 'plans/x.md', 'claude', 'white')
        self.conn.execute("INSERT INTO plans(project,path,status) VALUES ('alpha','plans/x.md',NULL)")
        self.assertIn(('alpha', 1), dashboard.hive_counts(self.conn, None))

    def test_hive_counts_follow_apiary_configuration(self):
        """Dashboard Apiary lines follow the configured order."""
        from haivequeen import hives
        from unittest import mock
        hives.create(self.conn, 'alpha', 'a.md', 'claude', 'white')
        hives.create(self.conn, 'zeta', 'z.md', 'claude', 'white')
        db.upsert_project(self.conn, 'zeta', None, 'white', 0)
        with mock.patch('haivequeen.config.load_config', return_value={'projects': [{'name': 'zeta'}, {'name': 'alpha'}]}):
            self.assertEqual([name for name, _ in dashboard.hive_counts(self.conn, None)], ['zeta', 'alpha', 'beta'])

    # Check that Codex 5-hour usage remains visible without a weekly row.
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
        self.assertNotIn("Codex 7d", before)
        env = {k: v for k, v in os.environ.items() if k != "BZZ_DATA_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(usage, "home_dir", return_value=home):
            usage._codex_refresh.clear()
            out = dashboard.render(self.conn, width=100, height=60, now=datetime.now(), cfg={}, sysinfo=dict(SYS), color=False)
        self.assertIsNotNone(runtime.kv_get(self.conn, "*", "usage-codex"))
        codex_line = [x for x in out.split("\n") if "Codex" in x and "5h" in x][0]
        self.assertIn("37%", codex_line)
        self.assertNotIn("Codex 7d", out)
        self.assertNotIn("64%", out)

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
        """The dashboard does not show a triage counter."""
        self.assertNotIn("in triage", self.text())
        db.add_question(self.conn, "alpha", "x.md", "T1", "Which?", "A", status="triage")
        db.add_question(self.conn, "alpha", "x.md", "T2", "Which?", "A", status="triage")
        self.assertNotIn("in triage", self.text())

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
        got = {row["name"] for row in dashboard.blocked_tasks(runtime.list_agents(self.conn, scope="all"), NOW)}
        self.assertIn("recent-2", got)
        self.assertNotIn("old-2", got)
        self.assertNotIn("archived-2", got)
        self.assertNotIn("later-done-2", got)
        self.assertNotIn("later-running-2", got)

    # Check quota, reset, slot, model and scheduler marker display for providers.
    def test_provider_limits_show_weekly_share_slots_model_and_next(self):
        """Provider rows show weekly quota and reset, free slots and medium models; only the scheduler choice is marked next."""
        providers = []
        for name, use, source in (("green-provider", "preferred", "codex"), ("yellow-provider", "fallback", "codex"),
                                  ("red-provider", "fallback", "codex"), ("unknown-provider", "fallback", "claude"),
                                  ("no-reset-provider", "fallback", "codex"),
                                  ("lmstudio", "fallback", ""),
                                  ("local", "fallback", "")):
            providers.append({"name": name, "protocol": "codex", "use": use, "slots": 2, "limit": source,
                "classes": {"medium": {"model": name + "-model"}}})
        limits = Path(self.tmp.name) / "limits.json"
        limits.write_text(json.dumps({"green-provider": {"usedPercent": 50, "resetHours": 84},
            "yellow-provider": {"usedPercent": 80, "resetHours": 48},
            "red-provider": {"usedPercent": 95, "resetHours": 24},
            "no-reset-provider": {"usedPercent": 33}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(limits)}):
            rows = dashboard.provider_lines(self.conn, {"providers": providers}, [])
            data = {"now": NOW, "project": None, "colours": {}, "run": runstate.get_state(self.conn),
                "hives": [], "agents": [], "fin": [], "blocked": [], "provider_limits": rows,
                "cu": {"FiveHour": None, "SevenDay": None}, "xu": {"FiveHour": None, "SevenDay": None},
                "usage": [], "counts": {}, "drafts": [], "status": [], "qs": [], "triage": 0,
                "round_errors": [], "selfupdate": None, "bj": None, "orch": {"status": "idle"},
                "sysinfo": dict(SYS), "host": "h1", "last_round": "-"}
            lines = dashboard.build_lines(0, data, 120, 0)
        for got, want in zip([r["ratio"] for r in rows[:3]], [1.0, 0.7, 0.35]):
            self.assertAlmostEqual(got, want)
        self.assertEqual([r["next"] for r in rows], [True, False, False, False, False, False, False])
        self.assertFalse(rows[5]["has_limit"])
        self.assertIsNone(rows[5]["ratio"])
        flat = "\n".join("".join(text for text, _ in row) for row in lines)
        self.assertRegex(flat, r"reset \d{2}\.\d{2}\. \d{2}:\d{2}")
        self.assertIn("Quota 1.00× (50.0%/50.0%)", flat)
        self.assertIn("slots 0/2  green-provider-model  ◀ nächster", flat)
        self.assertIn("lmstudio kein Limit  slots 0/2  lmstudio-model", flat)
        self.assertIn("local kein Limit  slots 0/2  local-model", flat)
        self.assertIn("unknown-provider used unbekannt, reset unbekannt  Quota unbekannt  slots 0/2  unknown-provider-model", flat)
        self.assertIn("no-reset-provider used 33%, reset unbekannt  Quota unbekannt  slots 0/2  no-reset-provider-model", flat)
        quota_colours = [colour for line in lines for text, colour in line if "Quota " in text and colour]
        self.assertEqual(quota_colours, ["green", "yellow", "red"])
        local_line = next(line for line in lines if any(text.startswith("local kein Limit") for text, _ in line))
        self.assertEqual(local_line, [("local kein Limit  slots 0/2  local-model", "")])
        unknown_line = next(line for line in lines if any(text.startswith("unknown-provider used unbekannt") for text, _ in line))
        self.assertEqual(unknown_line, [("unknown-provider used unbekannt, reset unbekannt  Quota unbekannt  slots 0/2  unknown-provider-model", "")])
        no_reset_line = next(line for line in lines if any(text.startswith("no-reset-provider used 33%") for text, _ in line))
        self.assertEqual(no_reset_line, [("no-reset-provider used 33%, reset unbekannt  Quota unbekannt  slots 0/2  no-reset-provider-model", "")])
        exhausted = [{"provider": name, "status": "running"} for name in ("green-provider", "yellow-provider", "red-provider", "unknown-provider", "no-reset-provider", "lmstudio", "local") for _ in range(2)]
        exhausted.extend([{"provider": "green-provider", "status": "starting"}])
        with mock.patch.dict(os.environ, {"BZZ_PROVIDER_LIMITS_FILE": str(limits)}):
            exhausted_rows = dashboard.provider_lines(self.conn, {"providers": providers}, exhausted)
        self.assertFalse(any(row["next"] for row in exhausted_rows))
        self.assertEqual(exhausted_rows[0]["used_slots"], 3)
        overfull_lines = dashboard.build_lines(0, dict(data, provider_limits=exhausted_rows), 120, 0)
        self.assertIn("slots 3/2", "\n".join("".join(text for text, _ in line) for line in overfull_lines))


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


class RoundErrorTest(DashboardTest):
    """A failed round shows in the dashboard (the Beekeeper has no pane of its own any more)."""

    def add(self, text, minutes_ago, kind="rounds"):
        """An event of the project alpha written `minutes_ago` minutes before NOW."""
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO events (ts, project, kind, text) VALUES (?,?,?,?)", (iso(minutes_ago), "alpha", kind, text))

    def test_recent_round_errors_are_listed(self):
        """A section ROUND ERRORS names the error text and the time of a round error of the last day; an older one and other lines do not appear."""
        self.add("ROUND ERROR boom in plan scan", 5)
        self.add("ROUND ERROR long ago", 60 * 30)
        self.add("start Ada (claude, queen)", 4)
        t = self.text()
        self.assertIn("ROUND ERRORS", t)
        self.assertIn("boom in plan scan", t)
        self.assertNotIn("long ago", t)

    def test_no_section_without_errors(self):
        """Without a round error the section is not drawn."""
        self.assertNotIn("ROUND ERRORS", self.text())
