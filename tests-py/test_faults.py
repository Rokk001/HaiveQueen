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

    def faults_of(self, kind=None):
        """Fault questions of the fault project (optionally of one kind)."""
        return runtime.list_questions(self.conn, "HaiveQueen", None, faults.FAULT_PLAN, kind)

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

    def test_runner_vanished_is_filed_for_a_new_row_only(self):
        """An agent row failed with 'runner process vanished' is filed once; rows that existed at the first look are only recorded."""
        self.agent("DUMMY-OLD-work", status="failed", started=60, ended=50, last_line=VANISHED)
        self.check()
        self.assertEqual(self.faults_of(), [])
        self.agent("DUMMY-NEW-work", status="failed", started=5, ended=1, last_line=VANISHED)
        self.check()
        q = self.faults_of("runner_vanished")
        self.assertEqual(len(q), 1)
        self.assertIn("DUMMY-NEW-work", q[0]["question"])

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

    def test_round_runs_the_detection(self):
        """A real round files the fault of a freshly vanished agent."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.seed()
        self.round()
        self.agent("DUMMY-V-work", status="failed", started=5, ended=1, last_line=VANISHED)
        self.round()
        self.assertEqual(len(self.faults_of("runner_vanished")), 1)

    def test_no_triage_agent_for_a_fault_question_of_plan_hq_faults(self):
        """A fault question that waits for triage gets no triage agent, a normal question still does."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        fq = db.add_question(self.conn, "testproj", faults.FAULT_PLAN, "no_start", "[no_start] x", "", status="triage")
        nq = db.add_question(self.conn, "testproj", REL, "N1", "Which?", "A", status="triage")
        self.assertEqual([q["id"] for q in triage.questions_to_triage(self.conn, "testproj")], [nq])
        self.round()
        self.assertIsNone(self.ag("DUMMY-QUESTION-%d-triage" % fq))
        self.assertEqual(self.ag("DUMMY-QUESTION-%d-triage" % nq)["status"], "running")

    def test_no_work_agent_for_an_answered_fault_question(self):
        """An answered fault question starts no work agent."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.seed()
        qid = faults.file_fault(self.conn, "testproj", "no_start", "x")
        db.answer_question(self.conn, qid, "looked at it", "owner")
        before = set(self.names())
        self.round()
        self.assertFalse([n for n in set(self.names()) - before if "NO" in n.upper() and "Q-" in n.upper()])


if __name__ == "__main__":
    unittest.main()
