"""Own-operation faults (K7): file_fault is the only place that files them, the loop detects each fault kind, and no triage or work agent starts for them."""
import unittest
from datetime import datetime

from bzztest import REL, RoundCase, runtime
from haivequeen import db, faults, triage

OTHER_LOG = """Event[0]:
  Log Name: Application
  Source: Application Error
  Date: 2026-10-29T09:00:00.000
  Description:
  Faulting application name: other.exe, version: 1.0
"""
CRASH_LOG = """Event[0]:
  Log Name: Application
  Source: Application Error
  Date: 2026-10-29T10:43:21.123
  Description:
  Faulting application name: WindowsTerminal.exe, version: 1.2
""" + OTHER_LOG.replace("Event[0]", "Event[1]")
VANISHED = "runner process vanished (ended by executor or crashed)"


class FaultsTest(RoundCase):
    """Rounds on the dummy project with a registered project HaiveQueen that owns the fault questions."""

    def setUp(self):
        """Register the fault project next to the dummy project."""
        super().setUp()
        self.add_project("HaiveQueen")

    # Read all fault reports, including those awaiting automatic checks.
    def faults_of(self, kind=None):
        """Fault questions of the fault project (optionally of one kind)."""
        return db.rows(self.conn, "SELECT * FROM questions WHERE project=? AND plan_path=?" + (" AND item=?" if kind else "") + " ORDER BY id", ("HaiveQueen", faults.FAULT_PLAN, kind) if kind else ("HaiveQueen", faults.FAULT_PLAN))

    def check(self, started=1, can_start=True, log="", now=None):
        """One fault check of the dummy project; `log` is the text the event log reader returns."""
        faults.check_round(self.env(), started, can_start, now=now or datetime(2026, 10, 29, 12, 0), reader=lambda: log)

    def test_file_fault_files_an_open_question_with_the_fixed_plan(self):
        """A fault becomes an open question of plan HQ-FAULTS with the kind as item."""
        qid = faults.file_fault(self.conn, "HaiveQueen", "self_update_rollback", "rolled back")
        q = self.faults_of("self_update_rollback")[0]
        self.assertEqual((q["id"], q["status"], q["plan_path"], q["item"]), (qid, "open", "HQ-FAULTS", "self_update_rollback"))
        self.assertIn("rolled back", q["question"])

    def test_duplicate_per_kind_and_day_is_not_filed(self):
        """The same kind twice on one day files one question; another kind or another day files a new one."""
        self.assertIsNotNone(faults.file_fault(self.conn, "HaiveQueen", "no_start", "a"))
        self.assertIsNone(faults.file_fault(self.conn, "HaiveQueen", "no_start", "b"))
        self.assertIsNotNone(faults.file_fault(self.conn, "HaiveQueen", "runner_vanished", "c"))
        self.conn.execute("UPDATE questions SET created_at='2020-01-01T00:00:00' WHERE item='no_start'")
        self.conn.commit()
        self.assertIsNotNone(faults.file_fault(self.conn, "HaiveQueen", "no_start", "d"))
        self.assertEqual(len(self.faults_of("no_start")), 2)

    def test_no_start_after_two_idle_rounds_with_startable_packages(self):
        """Startable packages and 0 starts in 2 rounds in a row file no_start; one such round does not, and a start resets the count."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.check(started=0)
        self.assertEqual(self.faults_of("no_start"), [])
        self.check(started=1)
        self.check(started=0)
        self.assertEqual(self.faults_of("no_start"), [])
        self.check(started=0)
        self.assertEqual(len(self.faults_of("no_start")), 1)

    def test_idle_rounds_without_startable_packages_or_start_permission_are_no_fault(self):
        """No package, or a round that could not start anything (RAM, STOP, build), counts nothing."""
        self.check(started=0)
        self.check(started=0)
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.check(started=0, can_start=False)
        self.check(started=0, can_start=False)
        self.assertEqual(self.faults_of(), [])

    # Verify vanished runners are retried without being counted as a task fault.
    def test_runner_vanished_does_not_file_fault(self):
        """An interrupted runner does not create a failure question."""
        self.check()
        self.agent("DUMMY-NEW-work", status="failed", started=5, ended=1, last_line=VANISHED)
        self.check()
        self.assertEqual(self.faults_of("runner_vanished"), [])

    def test_agent_ending_without_result_is_filed(self):
        """A done or failed agent with an empty or missing final answer is filed as agent_no_result; one with a final answer is not."""
        self.check()
        self.agent("DUMMY-A-work", status="done", started=5, ended=1, last_line="finished")
        self.finish("DUMMY-A-work", final="Done.")
        self.agent("DUMMY-B-work", status="failed", started=5, ended=1, last_line="exit 1")
        self.check()
        q = self.faults_of("agent_no_result")
        self.assertEqual(len(q), 1)
        self.assertIn("DUMMY-B-work", q[0]["question"])

    def test_windows_terminal_crash_in_the_event_log_is_filed(self):
        """A WindowsTerminal.exe entry of the Application log files terminal_crash; an entry of another program does not."""
        self.check(log=OTHER_LOG)
        self.assertEqual(self.faults_of(), [])
        runtime.kv_delete(self.conn, "HaiveQueen", faults.EVENTLOG_KEY)
        self.check(log=CRASH_LOG)
        self.assertEqual(len(self.faults_of("terminal_crash")), 1)

    def test_event_log_is_read_at_most_every_ten_minutes(self):
        """A second check inside 10 minutes does not read the log, one after 10 minutes does."""
        calls = []

        def reader():
            """Count the reads."""
            calls.append(1)
            return ""

        for minute in (0, 9, 10):
            faults.check_round(self.env(), 1, True, now=datetime(2026, 10, 29, 12, minute), reader=reader)
        self.assertEqual(len(calls), 2)

    def test_wevtutil_query_names_the_application_error_provider(self):
        """The event log query is the agreed wevtutil call."""
        self.assertEqual(faults.WEVTUTIL[:3], ["wevtutil", "qe", "Application"])
        self.assertIn("Application Error", faults.WEVTUTIL[3])
        self.assertIn("/f:text", faults.WEVTUTIL)

    # Verify non-interrupted faults remain detected by a real round.
    def test_round_runs_the_detection(self):
        """A real round files a new non-interrupted agent with no result."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.seed()
        self.round()
        self.agent("DUMMY-V-work", status="failed", started=5, ended=1, last_line="exit 1")
        self.round()
        self.assertEqual(len(self.faults_of("agent_no_result")), 1)

    # Verify no separate triage agent starts for any question.
    def test_no_triage_agent_for_fault_or_normal_question(self):
        """The Queen handles plan questions without launching triage agents."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        db.add_question(self.conn, "testproj", faults.FAULT_PLAN, "no_start", "[no_start] x", "", status="triage")
        db.add_question(self.conn, "testproj", REL, "N1", "Which?", "A", status="triage")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["status"] == "running"]
        self.assertTrue(all(a["phase"] != "triage" for a in active))

    # Ensure an answered fault does not start a work agent.
    def test_no_work_agent_for_an_answered_fault_question(self):
        """An answered fault question starts no work agent."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.seed()
        qid = faults.file_fault(self.conn, "testproj", "no_start", "x")
        db.answer_question(self.conn, qid, "looked at it", "coordinator")
        before = set(self.names())
        self.round()
        self.assertFalse([n for n in set(self.names()) - before if "NO" in n.upper() and "Q-" in n.upper()])

    # Check each fault against later evidence without starting an agent.
    def test_open_faults_are_resolved_only_with_evidence(self):
        """Later successful starts and runs close their matching fault questions."""
        for kind in ("no_start", "runner_vanished", "agent_no_result"):
            faults.file_fault(self.conn, "HaiveQueen", kind, "Agent DUMMY-A-work of Apiary testproj: failed. Apiary testproj:")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00'")
        self.conn.commit()
        self.agent("DUMMY-A-work", status="done", started_at="2026-10-29T10:30:00", ended_at="2026-10-29T10:40:00")
        self.check(now=datetime(2026, 10, 29, 11, 0))
        self.assertEqual(self.faults_of("no_start")[0]["status"], "open")
        self.check(now=datetime(2026, 10, 29, 11, 1))
        for kind in ("no_start", "runner_vanished", "agent_no_result"):
            q = self.faults_of(kind)[0]
            self.assertEqual((q["status"], q["answered_by"]), ("answered", "coordinator"))
            self.assertTrue(q["answer"])

    # A crash entry after the question confirms the fault; absence only resolves after an hour.
    def test_terminal_fault_waits_and_distinguishes_new_crash(self):
        """A new crash remains open; otherwise the hour of observation resolves it."""
        qid = faults.file_fault(self.conn, "HaiveQueen", "terminal_crash", "check")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00' WHERE id=?", (qid,))
        self.conn.commit()
        self.check(now=datetime(2026, 10, 29, 10, 59), log=CRASH_LOG)
        self.assertEqual(self.faults_of("terminal_crash")[0]["status"], "open")
        self.check(now=datetime(2026, 10, 29, 11, 0))
        self.assertEqual(self.faults_of("terminal_crash")[0]["status"], "open")
        self.check(now=datetime(2026, 10, 29, 11, 1))
        self.assertEqual(self.faults_of("terminal_crash")[0]["status"], "open")
        db.answer_question(self.conn, qid, "reviewed", "owner")
        qid = db.add_question(self.conn, "HaiveQueen", faults.FAULT_PLAN, "terminal_crash", "check again", "")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T11:01:00' WHERE id=?", (qid,))
        self.conn.commit()
        self.check(now=datetime(2026, 10, 29, 12, 1))
        self.assertEqual(self.faults_of("terminal_crash")[-1]["status"], "answered")

    # A restart must run continuously for an hour after the latest unexpected end.
    def test_loop_vanished_requires_continuous_runtime(self):
        """A later unexpected end resets the recovery observation."""
        from haivequeen import scheduler
        qid = faults.file_fault(self.conn, "HaiveQueen", "loop_vanished", "unexpected end")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00' WHERE id=?", (qid,))
        self.conn.commit()
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-unexpected-end-at", "2026-10-29T09:59:00")
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-started-at", "2026-10-29T10:01:00")
        self.check(now=datetime(2026, 10, 29, 11, 0))
        self.assertEqual(self.faults_of("loop_vanished")[0]["status"], "open")
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-unexpected-end-at", "2026-10-29T10:50:00")
        self.check(now=datetime(2026, 10, 29, 11, 1))
        self.assertEqual(self.faults_of("loop_vanished")[0]["status"], "open")
        runtime.kv_set(self.conn, scheduler.GLOBAL, "loop-started-at", "2026-10-29T11:02:00")
        self.check(now=datetime(2026, 10, 29, 12, 2))
        self.assertEqual(self.faults_of("loop_vanished")[0]["status"], "answered")

    # Never automatically close verified self-update faults.
    def test_self_update_faults_stay_open(self):
        """Rollback and pane failures are real regardless of elapsed time."""
        for kind in ("self_update_rollback", "self_update_pane"):
            faults.file_fault(self.conn, "HaiveQueen", kind, "real")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00'")
        self.conn.commit()
        self.check(now=datetime(2026, 10, 29, 12, 0))
        self.assertTrue(all(q["status"] == "open" for q in self.faults_of()))

    # Open reports retain coordinator-visible observations after an hour.
    def test_unresolved_faults_record_evidence_once(self):
        """Each unresolved kind stays open and records its observed recovery state."""
        kinds = ("no_start", "runner_vanished", "agent_no_result", "terminal_crash",
                 "loop_vanished", "self_update_rollback", "self_update_pane")
        for kind in kinds:
            faults.file_fault(self.conn, "HaiveQueen", kind,
                              "Agent DUMMY-A-work of Apiary testproj: failed. Apiary testproj:")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00'")
        self.conn.commit()
        runtime.kv_set(self.conn, "HaiveQueen", faults.EVENTLOG_KEY, "2026-10-29T11:00:00")
        self.check(started=0, now=datetime(2026, 10, 29, 11, 0), log=CRASH_LOG)
        self.check(started=0, now=datetime(2026, 10, 29, 11, 1), log=CRASH_LOG)
        events = runtime.list_events(self.conn, "HaiveQueen", kind="note")
        for q in self.faults_of():
            self.assertEqual(q["status"], "open")
            notes = [e for e in events if "Fault question %s remains open" % q["id"] in e["text"]]
            self.assertEqual(len(notes), 1, q["item"])
            self.assertIn(q["item"], notes[0]["text"])

    # Skipped and deferred rounds still check fault questions without starting work.
    def test_skipped_and_deferred_rounds_check_faults(self):
        """Low RAM and deferred migration do not skip fault checking."""
        from unittest.mock import patch
        from haivequeen import hives
        with patch("haivequeen.faults.check_round") as check:
            self.round(free_ram=lambda: 0.0)
            self.assertTrue(any(call.args[0].name == "testproj" and call.args[1:] == (0, False) for call in check.call_args_list))
        with patch("haivequeen.hives.migrate_once", return_value={"testproj": "deferred"}), patch("haivequeen.faults.check_round") as check:
            self.round()
            self.assertTrue(any(call.args[0].name == "testproj" and call.args[1:] == (0, False) for call in check.call_args_list))

    # Pending fault reports are checked privately before they reach the owner.
    def test_pending_fault_is_hidden_until_observation_expires(self):
        """Unresolved faults become open with evidence after an hour, not before."""
        from haivequeen import views, dashboard, cli
        from unittest.mock import patch
        import argparse
        qid = faults.file_fault(self.conn, "HaiveQueen", "no_start", "Apiary testproj: no starts")
        self.assertIsNone(faults.file_fault(self.conn, "HaiveQueen", "no_start", "duplicate"))
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00' WHERE id=?", (qid,))
        self.conn.commit()
        self.check(started=0, now=datetime(2026, 10, 29, 10, 59))
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen", "open"), [])
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen"), [])
        self.assertEqual(db.open_questions(self.conn, "HaiveQueen", only_open=True), [])
        with patch("haivequeen.cli.out") as output:
            cli.cmd_questions(argparse.Namespace(project="HaiveQueen", open=False, status=None, plan=None, item=None), self.conn)
            output.assert_called_once_with([])
        self.assertEqual(dashboard.collect(self.conn, "HaiveQueen", cfg={}, sysinfo={"host": "test", "cores": 1, "free_mb": 1024, "total_mb": 2048, "compiler_count": 0})["qs"], [])
        self.assertNotIn("no starts", views.render_questions(self.conn, "HaiveQueen"))
        self.check(started=0, now=datetime(2026, 10, 29, 11, 0))
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen", "open")[0]["id"], qid)
        self.assertTrue(any("remains open" in e["text"] for e in runtime.list_events(self.conn, "HaiveQueen", kind="note")))

    # A recovered pending fault is answered without first becoming an open question.
    def test_pending_recovery_resolves_without_owner_question(self):
        """Two later starts resolve the report while it remains hidden from open questions."""
        qid = faults.file_fault(self.conn, "HaiveQueen", "no_start", "Apiary testproj: no starts")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00' WHERE id=?", (qid,))
        self.conn.commit()
        self.check(now=datetime(2026, 10, 29, 10, 30))
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen"), [])
        self.check(now=datetime(2026, 10, 29, 10, 31))
        q = self.faults_of("no_start")[0]
        self.assertEqual((q["status"], q["answered_by"]), ("answered", "coordinator"))
        self.assertEqual(runtime.list_questions(self.conn, "HaiveQueen", "open"), [])
        self.assertFalse(any(e["kind"] == "question" and str(qid) in str(e["data_json"]) for e in runtime.list_events(self.conn, "HaiveQueen")))

    # Legacy fault wording is checked alongside current Apiary wording.
    def test_legacy_project_wording_is_resolved(self):
        """Later runs and starts resolve older project-labelled reports."""
        for kind in ("runner_vanished", "agent_no_result"):
            db.add_question(self.conn, "HaiveQueen", faults.FAULT_PLAN, kind, "Agent DUMMY-A-work of project testproj: ended.", "")
        db.add_question(self.conn, "HaiveQueen", faults.FAULT_PLAN, "no_start", "Project testproj: no starts.", "")
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00'")
        self.conn.commit()
        self.agent("DUMMY-A-work", status="done", ended_at="2026-10-29T10:40:00")
        self.check(now=datetime(2026, 10, 29, 10, 50))
        self.check(now=datetime(2026, 10, 29, 10, 51))
        self.assertTrue(all(q["status"] == "answered" for q in self.faults_of()))

    # Unmatched old reports cannot remain hidden after the observation window.
    def test_unmatched_pending_faults_open_without_stealing_other_projects(self):
        """Unknown or deleted project reports open with evidence; known projects keep their own check."""
        self.add_project("other")
        qids = []
        for text in ("unrecognized old report", "Project removed: no starts", "Project other: no starts"):
            qids.append(db.add_question(self.conn, "HaiveQueen", faults.FAULT_PLAN, "no_start", text, "", status="fault_check"))
        for text in ("Agent old of project removed: ended", "Agent old of Apiary other: ended"):
            qids.append(db.add_question(self.conn, "HaiveQueen", faults.FAULT_PLAN, "agent_no_result", text, "", status="fault_check"))
        self.conn.execute("UPDATE questions SET created_at='2026-10-29T10:00:00'")
        self.conn.commit()
        self.check(started=0, now=datetime(2026, 10, 29, 11, 0))
        rows = {q["id"]: q for q in self.faults_of()}
        self.assertEqual([rows[qid]["status"] for qid in qids], ["open", "open", "fault_check", "open", "fault_check"])
        notes = runtime.list_events(self.conn, "HaiveQueen", kind="note")
        for qid in (qids[0], qids[1], qids[3]):
            self.assertTrue(any("Fault question %s remains open" % qid in e["text"] for e in notes))

    # Verify the Queen is directed to exhaust technical alternatives before asking.
    def test_queen_prompt_requires_technical_alternatives(self):
        """Technical owner questions need two approaches and an explanation."""
        from haivequeen import bees
        prompt = bees.queen_instructions("standard", 2, 2, 100)
        self.assertIn("at least two technical", prompt)
        self.assertIn("without owner action or elevation", prompt)
        self.assertIn("why no alternative", prompt)


if __name__ == "__main__":
    unittest.main()
