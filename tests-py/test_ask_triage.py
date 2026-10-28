"""Questions that agents ask with `bzz ask` go through the triage before they can reach the owner: a duplicate or an already decided item is closed
with a reference, a technical question is answered by the Queen, a game/balance question stays open for the owner."""
import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import cli, db, triage
from haivequeen.runtime import CmdError


class AskTriageTest(RoundCase):
    """Rounds and commands on the dummy project; no real agent is needed."""

    def ask(self, item, question="Which one?"):
        """Run `bzz ask` in this process and return its JSON output."""
        args = argparse.Namespace(project="testproj", plan=REL, item=item, question=question, recommendation="A")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_ask(args, self.conn)
        return json.loads(buf.getvalue())

    def status(self, qid):
        """The question row."""
        return next(q for q in runtime.list_questions(self.conn, "testproj") if q["id"] == qid)

    def test_new_question_waits_for_triage_not_for_the_owner(self):
        """A new question has status triage; the list of open questions does not show it."""
        res = self.ask("N1")
        self.assertEqual(res["status"], "triage")
        self.assertEqual(self.status(res["id"])["status"], "triage")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])

    def test_round_starts_a_triage_agent_per_question(self):
        """The next round starts one triage agent for the waiting question."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        qid = self.ask("N2")["id"]
        self.round()
        self.assertEqual(self.ag("DUMMY-QUESTION-%d-triage" % qid)["status"], "running")

    def test_technical_question_is_answered_by_auto_with_reason(self):
        """A technical question is answered by the Queen: answered_by auto, reason and source in the answer; the owner sees nothing."""
        qid = self.ask("T1")["id"]
        triage.answer_asked(self.conn, "testproj", qid, "Use the existing helper.", "the code has it", "haivequeen/triage.py")
        q = self.status(qid)
        self.assertEqual((q["status"], q["answered_by"]), ("answered", "auto"))
        self.assertIn("reason: the code has it", q["answer"])
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])
        with self.assertRaises(CmdError):
            triage.answer_asked(self.conn, "testproj", self.ask("T2")["id"], "x", "", "")

    def test_game_question_stays_open_for_the_owner(self):
        """A game/balance question is released and then open for the owner."""
        qid = self.ask("G1", "How strong is the trap?")["id"]
        triage.release_asked(self.conn, "testproj", qid)
        self.assertEqual([q["id"] for q in runtime.list_questions(self.conn, "testproj", "open")], [qid])
        with self.assertRaises(CmdError):
            triage.release_asked(self.conn, "testproj", qid)

    def test_duplicate_of_answered_question_is_closed_with_reference(self):
        """The same item asked again after an answer is closed by auto and names the old question."""
        old = db.add_question(self.conn, "testproj", REL, "D1", "Which colour?", "A")
        db.answer_question(self.conn, old, "Blue.", "owner")
        res = self.ask("D1")
        self.assertEqual((res["status"], res["answered_by"]), ("answered", "auto"))
        self.assertIn("question %d" % old, res["answer"])
        self.assertIn("Blue.", res["answer"])
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])

    def test_existing_decision_answers_the_question_before_it_reaches_the_owner(self):
        """An active decision of the project (also one without a plan) settles the item: the question is closed with the decision id."""
        did = runtime.add_decision(self.conn, "testproj", "owner", "Crystal bowl: yes.", None, "M7")
        res = self.ask("M7")
        self.assertEqual((res["status"], res["answered_by"]), ("answered", "auto"))
        self.assertIn("decision %d" % did, res["answer"])
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])

    def test_triage_off_goes_straight_to_the_owner(self):
        """With the triage switched off a question is open at once and nothing waits for a triage agent."""
        machine = dict(self.machine, triage=False)
        with mock.patch("haivequeen.config.load_config", return_value=machine):
            res = self.ask("O1")
        self.assertEqual(res["status"], "open")
        self.assertEqual(self.status(res["id"])["status"], "open")
        self.assertEqual(triage.questions_to_triage(self.conn, "testproj"), [])

    def test_queen_ask_owner_is_matched_against_decisions_and_questions(self):
        """A question of the Queen for an item that a decision, an answered question or a waiting question settles is not asked; it is told in an event."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        runtime.add_decision(self.conn, "testproj", "owner", "Crystal bowl: yes.", REL, "M7")
        old = db.add_question(self.conn, "testproj", REL, "V9", "Decay?", "A")
        db.answer_question(self.conn, old, "Yes.", "owner")
        waiting = self.ask("W5")["id"]
        ask = lambda item: {"action": "ask_owner", "plan": REL, "item": item, "question": "q?", "recommendation": "r"}  # noqa: E731
        self.agent("Z-ZZ-verify", type="codex", provider="codex", package="P1", phase="verify", status="failed", started=3, ended=2, last_line="x")
        self.round(self.brain_answer(ask("M7"), ask("V9"), ask("W5"), ask("NEW1")))
        qs = runtime.list_questions(self.conn, "testproj")
        self.assertEqual([q["item"] for q in qs if q["id"] != old and q["id"] != waiting], ["NEW1"])
        texts = " | ".join(r[0] for r in self.conn.execute("SELECT text FROM events WHERE text LIKE '%already settled%'"))
        self.assertIn("M7", texts)
        self.assertIn("V9", texts)

    def test_triage_agent_closes_by_reference_to_a_decision(self):
        """The triage agent can close a question by the id of a decision that answers it in other words; an unknown id is refused."""
        did = runtime.add_decision(self.conn, "testproj", "owner", "Traps cost 50 gold.", REL, "TRAPCOST")
        qid = self.ask("OTHER", "What does a trap cost?")["id"]
        with self.assertRaises(CmdError):
            triage.close_by_reference(self.conn, "testproj", qid, "decision", did + 999, "x")
        triage.close_by_reference(self.conn, "testproj", qid, "decision", did, "same price question")
        self.assertEqual(self.status(qid)["status"], "answered")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])


if __name__ == "__main__":
    unittest.main()
