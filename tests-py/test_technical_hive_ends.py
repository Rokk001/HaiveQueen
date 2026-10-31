"""Regression tests for technical Hive ends."""
import unittest
from bzztest import RoundCase, runtime, REL
from haivequeen import db, hives, reattach


class TechnicalEndTests(RoundCase):
    """Technical ends are interrupted and never stop the Hive."""

    def test_reattach_marks_queen_and_bee(self):
        """Vanished processes are explicitly recorded as interrupted."""
        for name, phase in (("Ada", "queen"), ("Bea", "bee")):
            self.agent(name, phase=phase, status="running", pid=123)
        reattach.reattach(self.conn, "testproj", lambda pid: False)
        for name in ("Ada", "Bea"):
            self.assertEqual(self.ag(name).get("interrupted"), "vanished")

    def test_technical_end_restarts_without_question(self):
        """An ended Queen is retried even when she did no work."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = next(a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen")
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "last_line": "pane never started"})
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertEqual(runtime.list_questions(self.conn, "testproj", status="open", plan=REL), [])

    def test_repair_lifts_only_automatic_technical_question(self):
        """A technical end lifts its own automatic question, not agent questions."""
        self.agent("Ada", phase="queen", status="failed", last_line="process gone after orchestrator restart")
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        qid = hives.ask_about_end(self.conn, self.env(), REL, {"name": "Ada", "status": "failed"}, "")
        other = db.add_question(self.conn, "testproj", REL, "P1", "Owner decision?", "")
        hives.wait_for_answers(self.conn, "testproj", REL, [qid])
        hives.repair_technical_ends(self.conn, "testproj")
        questions = {q["id"]: q for q in runtime.list_questions(self.conn, "testproj")}
        self.assertEqual(questions[qid]["answer"], "lifted: only a technical end (window closed, killed, crashed)")
        self.assertEqual(questions[other]["status"], "open")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "waiting")

    def test_matching_bee_failure_is_taken_back_once(self):
        """Only a vanished Bee's recorded result is removed from its class."""
        from haivequeen import bees
        self.agent("Bea", phase="bee", package="P1", status="failed", last_line="runner process vanished", **{"class": "medium"})
        bees.record_attempt(self.conn, "testproj", REL, "P1", "medium", "failed", "Bea")
        bees.record_attempt(self.conn, "testproj", REL, "P1", "medium", "failed", "Real")
        hives.repair_technical_ends(self.conn, "testproj")
        hives.repair_technical_ends(self.conn, "testproj")
        self.assertEqual(bees.failure_count(self.conn, "testproj", REL, "P1", "medium"), 1)

    def test_three_pane_failures_raise_one_fault_and_keep_retrying(self):
        """Three consecutive failed starts alert once without blocking retries."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        for _ in range(4):
            self.round()
            queen = next(a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running")
            runtime.upsert_agent(self.conn, "testproj", queen["name"], {"status": "failed", "pid": None, "last_line": "pane never started"})
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        faults = [q for q in runtime.list_questions(self.conn, "HaiveQueen") if q["item"] == "no_start"]
        self.assertEqual(len(faults), 1)
    def test_real_errors_and_agent_questions_stay_open(self):
        """Real errors do not lift a Queen question or an agent question."""
        self.agent("Ada", phase="queen", status="failed", last_line="real error")
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        qid = hives.ask_about_end(self.conn, self.env(), REL, {"name": "Ada", "status": "failed"}, "")
        hives.wait_for_answers(self.conn, "testproj", REL, [qid])
        hives.repair_technical_ends(self.conn, "testproj")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", plan=REL)[0]["status"], "open")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "answer")

    def test_three_vanished_bees_raise_one_fault(self):
        """Three lost Bee processes raise one fault without counting a failure."""
        for name in ("Bea", "Bia", "Bo"):
            self.agent(name, phase="bee", package="P1", status="failed", last_line="runner process vanished")
        hives.technical_start_faults(self.conn, "testproj")
        hives.technical_start_faults(self.conn, "testproj")
        faults = runtime.list_questions(self.conn, "HaiveQueen", plan="HQ-FAULTS", item="no_start")
        self.assertEqual(len(faults), 1)
    def test_old_archived_technical_end_is_marked(self):
        """Historical archived agents are repaired by their own row ID."""
        self.agent("Ada", phase="queen", status="failed", last_line="pane never started")
        self.conn.execute("UPDATE agents SET archived=1 WHERE name='Ada'")
        hives.repair_technical_ends(self.conn, "testproj")
        self.assertEqual(runtime.list_agents(self.conn, "testproj", "all")[0].get("interrupted"), "vanished")
    def test_uncounted_technical_signal_does_not_remove_other_failure(self):
        """A Bee with an uncounted signal never reduces another Bee's count."""
        from haivequeen import bees
        self.agent("Bea", phase="bee", package="P1", status="failed", last_line="runner process vanished", **{"class": "medium"})
        bees.ensure_attempts(self.conn)
        self.conn.execute("CREATE TABLE bee_attempt_signals (project TEXT, plan_path TEXT, package TEXT, class TEXT, attempt TEXT, open_requirements TEXT, error TEXT, counted INTEGER)")
        self.conn.execute("INSERT INTO bee_attempts VALUES (?,?,?,?,?)", ("testproj", REL, "P1", "medium", 1))
        self.conn.execute("INSERT INTO bee_attempt_reports VALUES (?,?,?,?)", ("testproj", REL, "P1", "Bea"))
        self.conn.execute("INSERT INTO bee_attempt_signals VALUES (?,?,?,?,?,?,?,?)", ("testproj", REL, "P1", "medium", "Bea", "[]", "", 0))
        hives.repair_technical_ends(self.conn, "testproj")
        self.assertEqual(bees.failure_count(self.conn, "testproj", REL, "P1", "medium"), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bee_attempt_signals").fetchone()[0], 0)
