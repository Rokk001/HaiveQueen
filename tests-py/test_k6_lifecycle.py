"""K6 plan life cycle: statuses and old names (H-20), start on a draft (H-21), created branch (H-22), question for an existing branch (H-23),
visible header errors (H-26), faults without an Apiary (H-30, H-46 g), a stopped Bee stays stopped (H-31) and the same-round notice of startable packages (H-47)."""
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bzztest import REL, RoundCase, runtime  # noqa: E402
from haivequeen import bees, db, faults, gui_data, gui_hives, hives, plans, scheduler  # noqa: E402

NEW = ("DRAFT", "SENT", "PREPARING", "QUEUED", "RUNNING", "WAITING", "REVIEW", "DONE", "PAUSED", "CANCELLED")


class StatusNameTests(RoundCase):
    """H-20: the new names everywhere; old names are read as the new ones and written new at the next change."""

    def status(self):
        return self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]

    def file_text(self):
        return (self.repo / REL).read_text(encoding="utf-8")

    def test_the_statuses_are_the_new_names(self):
        self.assertEqual(plans.STATUSES, NEW)
        for old in ("SUBMITTED", "READY", "IN_PROGRESS", "REJECTED"):
            self.assertNotIn(old, plans.STATUSES)

    def test_old_names_are_read_as_the_new_ones(self):
        for old, new in (("SUBMITTED", "SENT"), ("READY", "QUEUED"), ("IN_PROGRESS", "RUNNING"), ("REJECTED", "CANCELLED")):
            self.assertEqual(plans.normalize_status(old.lower()), new)
            self.assertEqual(plans.parse_plan_text('+++\nstatus = "%s"\n+++\n# T\n' % old).status, new)
            self.plan(old, [{"id": "P1", "title": "First"}])
            self.assertEqual(self.status(), new)

    def test_an_old_name_is_written_new_at_the_next_status_change(self):
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        self.assertIn('status = "IN_PROGRESS"', self.file_text())   # nothing rewrites the file without a status change
        hives.pause_plan(self.conn, "testproj", REL)
        hives.start_again(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "RUNNING")
        self.assertIn('status = "RUNNING"', self.file_text())

    def test_old_database_rows_get_the_new_names(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.execute("CREATE TABLE plans (project TEXT NOT NULL, path TEXT NOT NULL, title TEXT, status TEXT CHECK (status IN "
                     "('DRAFT','SUBMITTED','READY','IN_PROGRESS','DONE','PAUSED','REJECTED')), updated_at TEXT, PRIMARY KEY (project, path))")
        conn.executemany("INSERT INTO plans VALUES ('P',?,?,?,NULL)", [("a.md", "A", "SUBMITTED"), ("b.md", "B", "READY"), ("c.md", "C", "IN_PROGRESS"),
                                                                       ("d.md", "D", "REJECTED"), ("e.md", "E", "DONE")])
        db.rebuild_plans_table(conn, db.SCHEMA_FILE.read_text(encoding="utf-8"))
        self.assertEqual([r[0] for r in conn.execute("SELECT status FROM plans ORDER BY path")], ["SENT", "QUEUED", "RUNNING", "CANCELLED", "DONE"])
        for status in NEW:
            conn.execute("INSERT INTO plans VALUES ('Q',?,NULL,?,NULL)", (status + ".md", status))

    def test_cut_moves_a_sent_plan_to_queued_and_a_start_to_running(self):
        """PREPARING while the Queen cuts, QUEUED after a good cut, RUNNING once an agent works on it."""
        self.plan("SENT")
        self.round()
        self.assertEqual(self.status(), "PREPARING")
        env = self.env()
        hives.update_waiting(self.conn, env)
        self.assertEqual(self.status(), "PREPARING")   # still cut by its Queen
        runtime.set_plan_status(self.conn, "testproj", REL, "QUEUED")
        hives.update_waiting(self.conn, env)   # the Queen that cut it is alive: the plan runs
        self.assertEqual(self.status(), "RUNNING")

    def test_running_and_waiting(self):
        """RUNNING <-> WAITING: no live agent and a waiting Hive make it WAITING; what for is shown; a live agent makes it RUNNING again."""
        self.plan("RUNNING", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        self.conn.execute("UPDATE hives SET state='answer', questions_json='[7]' WHERE project='testproj' AND plan_path=?", (REL,))
        env = self.env()
        hives.update_waiting(self.conn, env)
        self.assertEqual(self.status(), "WAITING")
        self.assertIn('status = "WAITING"', self.file_text())
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)
        self.assertIn("answer", gui_hives.status_text(row))
        self.agent("Ada", phase="queen", status="running", pid=__import__("os").getpid())
        hives.update_waiting(self.conn, env)
        self.assertEqual(self.status(), "RUNNING")

    def test_cancelled_replaces_rejected(self):
        self.plan("DRAFT", [{"id": "P1", "title": "First"}])
        hives.cancel_plan(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "CANCELLED")
        self.assertIn('status = "CANCELLED"', self.file_text())
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)
        self.assertEqual([a for _, a in gui_hives.actions(row)], ["start", "draft"])
        hives.start_again(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "DRAFT")


class ReviewTests(RoundCase):
    """H-20: REVIEW while the integrated plan waits for the owner check, DONE after it."""

    def test_integrated_plan_waits_in_review(self):
        self.plan("RUNNING", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        env = self.env()
        with mock.patch.object(hives, "integrated_ready", return_value={"id": 1, "updated_at": db.now()}), \
                mock.patch.object(hives, "owner_check_wanted", return_value=True):
            hives.reconcile_completed(self.conn, env)
        status = self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]
        self.assertEqual(status, "REVIEW")
        self.assertTrue(runtime.list_questions(self.conn, "testproj", status="open", plan=REL, item="owner-check"))


class StartDraftTests(RoundCase):
    """H-21: Start on a DRAFT sets SENT, so the plan is prepared like any sent plan."""

    def test_start_on_a_draft_sets_sent_and_gets_a_queen_that_cuts_it(self):
        self.plan("DRAFT", [{"id": "P1", "title": "First"}])
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)
        gui_hives.run_action(self.conn, row, "start", lambda text: True)
        self.assertEqual(self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0], "SENT")
        self.assertIn('status = "SENT"', (self.repo / REL).read_text(encoding="utf-8"))
        self.assertIsNone(runtime.cut_assignments(self.conn, "testproj", REL, (self.repo / REL).read_text(encoding="utf-8")))   # the cut is recorded by the plan check, not by Start
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        prompt = Path(queen["prompt_file"]).read_text(encoding="utf-8") if queen.get("prompt_file") else ""
        status = self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]
        self.assertEqual(status, "PREPARING")
        if prompt:
            self.assertIn("bzz plan check", prompt)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=True).stdout


class BranchTests(RoundCase):
    """H-22 and H-23 on a real repository with a remote."""

    def setUp(self):
        super().setUp()
        self.remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(self.remote)], check=True)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "T")
        (self.repo / "README.md").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")
        self.plan("QUEUED", [{"id": "P1", "title": "First"}], rel="docs/plans/dummy/Feature-X-PLAN.md")
        self.rel = "docs/plans/dummy/Feature-X-PLAN.md"

    def test_plan_without_branch_gets_a_created_branch_from_the_integration_branch(self):
        env = self.env()
        branch, worktree = bees.plan_worktree(env, self.rel)
        self.assertEqual(branch, "feature-x")
        cwd = bees.prepare_queen_worktree(env, self.rel)
        self.assertTrue(cwd and Path(cwd).is_dir())
        self.assertEqual(git(cwd, "rev-parse", "--abbrev-ref", "HEAD").strip(), "feature-x")
        self.assertEqual(git(cwd, "rev-parse", "HEAD").strip(), git(self.repo, "rev-parse", "origin/main").strip())

    def test_existing_branch_without_history_asks_the_owner_and_waits(self):
        git(self.repo, "branch", "feature-x")
        env = self.env()
        self.assertTrue(bees.foreign_plan_branch(env, self.rel))
        self.assertEqual(bees.resolve_existing_branch(env, self.rel), "")
        self.assertEqual(bees.resolve_existing_branch(env, self.rel), "")   # asked once
        open_q = runtime.list_questions(self.conn, "testproj", status="open", plan=self.rel, item=bees.BRANCH_ITEM)
        self.assertEqual(len(open_q), 1)
        self.assertIn("feature-x", open_q[0]["question"])
        db.answer_question(self.conn, open_q[0]["id"], "take over", "owner")
        self.assertEqual(bees.resolve_existing_branch(env, self.rel), "take over")
        self.assertFalse(bees.foreign_plan_branch(env, self.rel))

    def test_owner_chooses_a_new_branch(self):
        git(self.repo, "branch", "feature-x")
        env = self.env()
        bees.resolve_existing_branch(env, self.rel)
        q = runtime.list_questions(self.conn, "testproj", status="open", plan=self.rel, item=bees.BRANCH_ITEM)[0]
        db.answer_question(self.conn, q["id"], "neu", "owner")
        self.assertEqual(bees.resolve_existing_branch(env, self.rel), "new")
        self.assertEqual(bees.plan_worktree(env, self.rel)[0], "feature-x-2")
        self.assertFalse(bees.foreign_plan_branch(env, self.rel))

    def test_round_waits_for_the_branch_answer(self):
        git(self.repo, "branch", "feature-x")
        from haivequeen import loop
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertEqual([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"], [])
        self.assertIn("waiting for the owner", runtime.kv_get(self.conn, "testproj", "hive-blocker:" + self.rel))
        self.assertEqual(len(runtime.list_questions(self.conn, "testproj", status="open", plan=self.rel, item=bees.BRANCH_ITEM)), 1)

    def test_branch_choice_words(self):
        for text, want in (("take over", "take over"), ("Übernehmen bitte", "take over"), ("new", "new"), ("einen neuen", "new"), ("hmm", "")):
            self.assertEqual(bees.branch_choice(text), want)


class HeaderErrorTests(RoundCase):
    """H-26: a broken header, an unknown status or an unknown class is a visible error, not a silent DRAFT or medium."""

    def write(self, text):
        path = self.repo / REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_errors_are_found(self):
        self.assertTrue(plans.parse_plan_text('+++\nstatus = "READY\n+++\n# T\n').errors)
        self.assertIn("unknown", plans.parse_plan_text('+++\nstatus = "STARTED"\n+++\n# T\n').errors[0])
        text = '+++\nstatus = "QUEUED"\n[[packages]]\nid = "P1"\ntitle = "x"\nclass = "huge"\n+++\n# T\n'
        self.assertIn("huge", plans.parse_plan_text(text).errors[0])
        self.assertEqual(plans.parse_plan_text('+++\nstatus = "IN_PROGRESS"\n[[packages]]\nid = "P1"\nclass = "heavy"\n+++\n# T\n').errors, [])
        self.assertEqual(plans.parse_plan_text("# Free text without header\n").errors, [])

    def test_the_hives_window_shows_the_error(self):
        self.write('+++\nstatus = "STARTED"\n+++\n# T\n')
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)
        self.assertIn("STARTED", row["error"])
        self.assertIn("STARTED", gui_hives.status_text(row))
        self.write('+++\nstatus = "QUEUED"\n[[packages]]\nid = "P1"\ntitle = "x"\nclass = "huge"\n+++\n# T\n')
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)
        self.assertIn("huge", gui_hives.status_text(row))


class GlobalFaultTests(RoundCase):
    """H-30 and H-46(g): a fault without an Apiary is shown in the GUI status panel and as a desktop message, never as an inbox question."""

    def test_loop_vanished_is_shown_not_asked(self):
        shown = []
        with mock.patch("haivequeen.desktop.notify", shown.append):
            from haivequeen import loop
            loop.report_loop_failure(self.conn, "Loop ended unexpectedly (pid 1)")
            loop.report_loop_failure(self.conn, "Loop ended unexpectedly (pid 1)")
        self.assertEqual(db.rows(self.conn, "SELECT * FROM questions WHERE plan_path=?", (faults.FAULT_PLAN,)), [])
        self.assertEqual(len(shown), 1)
        self.assertIn("loop_vanished", shown[0])
        self.assertEqual([f["kind"] for f in faults.global_faults(self.conn)], ["loop_vanished"])
        sections = dict(gui_data.info_sections({**self.fake_overview(), "global_faults": faults.global_faults(self.conn)}))
        self.assertTrue(any("loop_vanished" in text for text, _ in sections["FAULTS"]))

    def test_loop_vanished_resolves_after_a_long_run(self):
        with mock.patch("haivequeen.desktop.notify", lambda text: None):
            faults.file_global_fault(self.conn, "loop_vanished", "gone")
        created = db.parse_time(faults.global_faults(self.conn)[0]["at"])
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-unexpected-end-at", (created - timedelta(minutes=1)).isoformat(timespec="seconds"))
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-started-at", created.isoformat(timespec="seconds"))
        faults.check_round(self.env(), 0, False, now=created + timedelta(minutes=61))
        self.assertEqual(faults.global_faults(self.conn, now=created + timedelta(minutes=61)), [])

    def test_apiary_fault_goes_into_its_own_inbox(self):
        self.add_project("Other")
        qid = faults.file_fault(self.conn, "Other", "no_start", "Apiary Other: no starts")
        self.assertEqual(db.rows(self.conn, "SELECT project FROM questions WHERE id=?", (qid,))[0]["project"], "Other")

    def fake_overview(self):
        return {"run": {"state": "RUNNING"}, "project": None, "orch": {"status": "idle"}, "counts": {}, "drafts": [], "usage": [], "bj": "",
                "sysinfo": {"free_mb": 1024, "total_mb": 2048}, "status": [], "qs": [], "round_errors": [], "selfupdate": None}


class StoppedPackageTests(RoundCase):
    """H-31: "Stop Bee" leaves the package stopped until the owner releases it with "Resume package"."""

    def setUp(self):
        super().setUp()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='testproj' AND plan_path=?", (REL,))
        self.agent("Queen-A", phase="queen", status="running", pid=__import__("os").getpid(), cwd=str(self.wt))

    def start(self, package):
        env = self.env()
        with mock.patch.object(bees, "valid_bee_worktree", return_value=True):
            return bees.start_process(env, REL, package, "Bea", "medium", "task", ["step"], [], "", cwd=str(self.wt))

    def test_a_stopped_package_is_refused_until_it_is_resumed(self):
        bees.stop_package(self.conn, "testproj", REL, "P1")
        self.assertFalse(self.start("P1"))
        self.assertIn("owner stopped", bees.LAST_REFUSAL)
        self.assertEqual(bees.stopped_packages(self.conn), [("testproj", REL, "P1")])
        bees.resume_package(self.conn, "testproj", REL, "P1")
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "P1"))
        self.start("P1")
        self.assertNotIn("owner stopped", bees.LAST_REFUSAL or "")
        with self.assertRaises(ValueError):
            bees.resume_package(self.conn, "testproj", REL, "P1")

    def test_the_dashboard_offers_resume_for_a_stopped_package(self):
        bees.stop_package(self.conn, "testproj", REL, "P2")
        rows = gui_data.bee_list([], {}, set(), datetime.now(), stopped=bees.stopped_packages(self.conn))
        row = next(r for r in rows if r["task"] == "P2")
        self.assertEqual(row["status"], "stopped")
        self.assertTrue(row["stopped"])
        from haivequeen import gui
        app = mock.Mock(conn=self.conn, confirm=lambda text: True)
        gui.Dashboard.resume_package(app, row) if hasattr(gui, "Dashboard") else self.resume_with_class(gui, app, row)
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "P2"))

    def resume_with_class(self, gui, app, row):
        cls = next(c for c in vars(gui).values() if isinstance(c, type) and hasattr(c, "resume_package"))
        cls.resume_package(app, row)

    def test_stop_bee_marks_the_package_stopped(self):
        from haivequeen import gui
        self.agent("Bea", phase="bee", package="P1", status="running", pid=999999)
        bee = {"kind": "bee", "agent": "Bea", "project": "testproj", "plan_path": REL, "name": "Bea", "task": "P1"}
        cls = next(c for c in vars(gui).values() if isinstance(c, type) and hasattr(c, "stop_bee"))
        app = mock.Mock(conn=self.conn, confirm=lambda text: True, selected=lambda table: bee)
        with mock.patch("haivequeen.platform.kill_process_tree") as kill:
            cls.stop_bee(app)
        kill.assert_called_once_with(999999)
        self.assertTrue(bees.package_stopped(self.conn, "testproj", REL, "P1"))

    def stop_notice(self):
        f = self.env().run_dir("Queen-A") / bees.STOP_NOTICE_FILE
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    def test_stop_bee_on_an_embedded_bee_stops_it_and_tells_its_queen(self):
        """An embedded (subagent) Bee has no process of its own: X stops its package, frees its place and tells the Queen to end the subagent."""
        from haivequeen import gui
        bees.record_embedded_start(self.conn, "testproj", REL, "P1", "Cia", "medium", "claude")
        queen = dict(runtime.list_agents(self.conn, "testproj", "active")[0])
        row = next(r for r in gui_data.bee_list([queen], {}, set(), datetime.now(), embedded=gui_data.embedded_rows(self.conn)) if r["name"] == "Cia")
        self.assertTrue(row.get("embedded"))
        cls = next(c for c in vars(gui).values() if isinstance(c, type) and hasattr(c, "stop_bee"))
        app = mock.Mock(conn=self.conn, confirm=lambda text: True, selected=lambda table: row)
        with mock.patch("haivequeen.platform.kill_process_tree") as kill:
            cls.stop_bee(app)
        kill.assert_not_called()   # the Queen's process must survive
        app.inform.assert_not_called()   # no "has not started yet"
        self.assertTrue(bees.package_stopped(self.conn, "testproj", REL, "P1"))
        self.assertEqual(gui_data.embedded_rows(self.conn), [])
        self.assertEqual(self.conn.execute("SELECT outcome FROM embedded_bees WHERE name='Cia'").fetchone()[0], "stopped")
        self.assertEqual(bees.free_bee_places(self.env(), REL, 5), 3)
        self.assertIn("P1", self.stop_notice())
        self.assertFalse(self.start("P1"))   # the Queen cannot start it again as a subagent either
        self.assertIn("owner stopped", bees.LAST_REFUSAL)
        bees.resume_package(self.conn, "testproj", REL, "P1")
        self.assertEqual(self.stop_notice(), "")

    def test_the_stop_notice_names_every_stopped_package_of_the_plan(self):
        bees.stop_package(self.conn, "testproj", REL, "P1")
        bees.stop_package(self.conn, "testproj", REL, "P2")
        self.assertIn("P1, P2", self.stop_notice())
        bees.resume_package(self.conn, "testproj", REL, "P1")
        self.assertNotIn("P1", self.stop_notice())
        self.assertIn("P2", self.stop_notice())

    def test_the_queen_rule_names_the_stop_notice(self):
        from haivequeen import roles
        self.assertIn("STOP-PACKAGES", roles.role_text("queen", {"class": "medium", "bee_limit": 3, "escalate_after": 3, "context_limit": 1, "language": "English"}))


class StartableNoticeTests(RoundCase):
    """H-47: in the same round the Queen is told which startable packages to start when Bee places are free; the dashboard shows them."""

    def setUp(self):
        super().setUp()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}, {"id": "P3", "title": "Third", "depends": ["P1"]}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='testproj' AND plan_path=?", (REL,))
        self.agent("Queen-A", phase="queen", status="running", pid=__import__("os").getpid(), cwd=str(self.wt))

    def notice(self):
        f = self.env().run_dir("Queen-A") / bees.NOTICE_FILE
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    def test_round_tells_the_queen_and_the_dashboard_shows_them(self):
        self.round()
        text = self.notice()
        self.assertIn("P1", text)
        self.assertIn("P2", text)
        self.assertNotIn("P3", text)   # its dependency is not done
        self.assertEqual(bees.startable_map(self.conn)[("testproj", REL)], ["P1", "P2"])
        rows = gui_data.bee_list([], {}, set(), datetime.now(), startable=bees.startable_map(self.conn))
        self.assertEqual([(r["task"], r["status"]) for r in rows], [("P1", "startable, not started"), ("P2", "startable, not started")])

    def test_started_and_stopped_packages_and_free_places_count(self):
        self.agent("Bea", phase="bee", package="P1", status="running", pid=__import__("os").getpid())
        bees.stop_package(self.conn, "testproj", REL, "P2")
        self.round()
        self.assertEqual(self.notice(), "")
        self.assertNotIn(("testproj", REL), bees.startable_map(self.conn))
        bees.resume_package(self.conn, "testproj", REL, "P2")
        env = self.env()
        env.settings["beesPerQueen"] = 1   # the one place is taken by Bea
        self.assertEqual(bees.notify_startable(env, REL, {"name": "Queen-A"}, 5), [])
        env.settings["beesPerQueen"] = 3
        self.assertEqual(bees.notify_startable(env, REL, {"name": "Queen-A"}, 5), ["P2"])
        self.assertEqual(bees.notify_startable(env, REL, {"name": "Queen-A"}, 0), [])   # no free provider slot

    def test_a_queen_told_to_hand_off_gets_no_start_notice(self):
        """A Queen whose HANDOFF file is written must start no new Bees: the old START-PACKAGES notice and the dashboard list are cleared in that round."""
        self.round()
        self.assertIn("P1", self.notice())
        (self.env().run_dir("Queen-A") / "HANDOFF").write_text("context limit", encoding="utf-8")   # written by an earlier round
        self.round()
        self.assertEqual(self.notice(), "")
        self.assertNotIn(("testproj", REL), bees.startable_map(self.conn))
        self.round()   # later rounds do not write it again
        self.assertEqual(self.notice(), "")

    def test_the_queen_rule_names_the_notice(self):
        from haivequeen import roles
        self.assertIn("START-PACKAGES", roles.role_text("queen", {"class": "medium", "bee_limit": 3, "escalate_after": 3, "context_limit": 1, "language": "English"}))


if __name__ == "__main__":
    unittest.main()
