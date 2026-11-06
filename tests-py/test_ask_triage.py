"""Questions that agents ask with `bzz ask` reach the owner at once; a duplicate or an already decided item is closed with a reference."""
import argparse
import contextlib
import io
import json
import unittest

from bzztest import REL, RoundCase, runtime
from haivequeen import cli, db


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

    # Verify that questions reach the owner directly without a triage agent.
    def test_new_question_reaches_owner_directly(self):
        """A new question is open without starting a separate agent."""
        res = self.ask("N1")
        self.assertEqual(res["status"], "open")
        self.assertEqual(self.status(res["id"])["status"], "open")
        self.assertEqual([q["id"] for q in runtime.list_questions(self.conn, "testproj", "open")], [res["id"]])

    # Verify the plan starts a Queen, not a question triage agent.
    def test_round_starts_queen_without_triage_agent(self):
        """A question does not start a separate triage agent."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.ask("N2")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["status"] == "running"]
        self.assertEqual([a["phase"] for a in active], ["queen"])

    # Verify an owner question remains open and is not duplicated.
    def test_game_question_stays_open_for_the_owner(self):
        """A game question reaches the owner once."""
        qid = self.ask("G1", "How strong is the trap?")["id"]
        self.assertEqual(self.ask("G1", "How strong is the trap?")["id"], qid)
        self.assertEqual([q["id"] for q in runtime.list_questions(self.conn, "testproj", "open")], [qid])

    def test_followup_text_is_appended_to_open_question(self):
        qid = self.ask("N3", "First question?")["id"]
        self.assertEqual(self.ask("N3", "Additional detail?")["id"], qid)
        self.assertEqual(self.status(qid)["question"], "First question?\n\nAdditional detail?")

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

    def test_owner_checks_remain_open_after_prior_answers_and_decisions(self):
        """Each owner check requires a fresh owner answer, even for a previously settled item."""
        from haivequeen import hives
        for item, source in (("owner-check", "answer"), ("owner-check-build17", "decision"), ("owner-check-answered", "answer")):
            with self.subTest(item=item, source=source):
                if source == "answer":
                    old = db.add_question(self.conn, "testproj", REL, item, "Earlier check?", "")
                    db.answer_question(self.conn, old, "ok", "owner")
                else:
                    runtime.add_decision(self.conn, "testproj", "owner", "Already checked.", REL, item)
                res = self.ask(item, "Check the new result.")
                self.assertEqual(res["status"], "open")
                self.assertEqual(self.status(res["id"])["status"], "open")
                self.assertEqual(self.ask(item)["id"], res["id"])
                if item != "owner-check-answered":
                    self.assertIn(res["id"], hives.owner_check_step(self.conn, "testproj", REL)[1])
                db.answer_question(self.conn, res["id"], "ok", "owner")

    def test_owner_check_prefix_without_hyphen_still_deduplicates(self):
        old = db.add_question(self.conn, "testproj", REL, "owner-checklist", "Earlier?", "")
        db.answer_question(self.conn, old, "yes", "owner")
        self.assertEqual(self.ask("owner-checklist")["answered_by"], "auto")

    # Verify the direct question path reuses existing answers and open questions.
    def test_queen_ask_owner_is_matched_against_decisions_and_questions(self):
        """Known answers do not create new open owner questions."""
        runtime.add_decision(self.conn, "testproj", "owner", "A decision.", REL, "M7")
        old = db.add_question(self.conn, "testproj", REL, "V9", "Which?", "A")
        db.answer_question(self.conn, old, "Yes.", "owner")
        waiting = self.ask("W5")["id"]
        self.assertEqual(self.ask("M7")["status"], "answered")
        self.assertEqual(self.ask("V9")["status"], "answered")
        self.assertEqual(self.ask("W5")["id"], waiting)
        fresh = self.ask("NEW1")["id"]
        self.assertEqual({q["id"] for q in runtime.list_questions(self.conn, "testproj", "open")}, {waiting, fresh})


if __name__ == "__main__":
    unittest.main()
