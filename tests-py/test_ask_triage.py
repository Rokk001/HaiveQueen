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

    # Verify that a new question waits for the triage instead of reaching the owner.
    def test_new_question_waits_for_triage(self):
        """A new question has the status triage; the owner sees nothing until the triage agent releases it."""
        res = self.ask("N1")
        self.assertEqual(res["status"], "triage")
        self.assertEqual(self.status(res["id"])["status"], "triage")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])
        self.assertEqual([q["id"] for q in triage.questions_to_triage(self.conn, "testproj")], [res["id"]])

    # The owner check is a question for the owner and skips the triage.
    def test_owner_check_skips_the_triage(self):
        """A question with the item owner-check is open at once."""
        self.assertEqual(self.ask("owner-check")["status"], "open")

    # Verify the round starts the Queen and a triage agent for the waiting question.
    def test_round_starts_queen_and_a_triage_agent(self):
        """A waiting question gets its own triage agent next to the Queen."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        qid = self.ask("N2")["id"]
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["status"] == "running"]
        self.assertEqual(sorted(a["phase"] for a in active), ["queen", "triage"])
        self.assertEqual(next(a for a in active if a["phase"] == "triage")["package"], triage.QUESTION_PKG % qid)

    # A question the triage cannot settle goes to the owner.
    def test_stale_question_is_released_to_the_owner(self):
        """After two ended triage agents, or after an hour without any, the question becomes open for the owner."""
        qid = db.add_question(self.conn, "testproj", REL, "S1", "Which?", "A", status="triage")
        self.assertEqual(triage.release_stale(self.conn, "testproj"), [])
        for i in range(2):
            runtime.upsert_agent(self.conn, "testproj", "S1-triage-%d" % i, {"plan": REL, "phase": "triage", "provider": "claude", "status": "done",
                                                                            "startedAt": db.now(), "package": triage.QUESTION_PKG % qid})
        self.assertEqual(triage.release_stale(self.conn, "testproj"), [qid])
        self.assertEqual(self.status(qid)["status"], "open")
        late = db.add_question(self.conn, "testproj", REL, "S2", "Which?", "A", status="triage")
        self.conn.execute("UPDATE questions SET created_at='2000-01-01T00:00:00' WHERE id=?", (late,))
        self.assertEqual(triage.release_stale(self.conn, "testproj"), [late])

    # A technical question is answered by the Queen: answered_by auto, reason and source in the answer; the owner sees nothing.
    def test_technical_question_is_answered_by_auto_with_reason(self):
        """A technical question is answered by the Queen: answered_by auto, reason and source in the answer; the owner sees nothing."""
        qid = db.add_question(self.conn, "testproj", REL, "T1", "Which one?", "A", status="triage")
        triage.answer_asked(self.conn, "testproj", qid, "Use the existing helper.", "the code has it", "haivequeen/triage.py")
        q = self.status(qid)
        self.assertEqual((q["status"], q["answered_by"]), ("answered", "auto"))
        self.assertIn("reason: the code has it", q["answer"])
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])
        with self.assertRaises(CmdError):
            triage.answer_asked(self.conn, "testproj", db.add_question(self.conn, "testproj", REL, "T2", "Which?", "A", status="triage"), "x", "", "")

    # Verify an owner question remains open and is not duplicated.
    def test_game_question_stays_open_for_the_owner(self):
        """A game question waits for the triage once and reaches the owner when the triage agent releases it."""
        qid = self.ask("G1", "How strong is the trap?")["id"]
        self.assertEqual(self.ask("G1", "How strong is the trap?")["id"], qid)
        triage.release_asked(self.conn, "testproj", qid)
        self.assertEqual([q["id"] for q in runtime.list_questions(self.conn, "testproj", "open")], [qid])
        self.assertEqual(self.ask("G1", "How strong is the trap?")["id"], qid)

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

    def test_triage_cannot_be_switched_off(self):
        """The setting triage is a removed setting: a question waits for the triage whatever the machine settings say."""
        machine = dict(self.machine, triage=False)
        with mock.patch("haivequeen.config.load_config", return_value=machine):
            res = self.ask("O1")
        self.assertEqual(res["status"], "triage")
        self.assertEqual([q["id"] for q in triage.questions_to_triage(self.conn, "testproj")], [res["id"]])

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
        self.assertEqual({q["id"] for q in runtime.list_questions(self.conn, "testproj", "triage")}, {waiting, fresh})
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])

    # The triage agent can close a question by the id of a decision that answers it in other words; an unknown id is refused.
    def test_triage_agent_closes_by_reference_to_a_decision(self):
        """The triage agent can close a question by the id of a decision that answers it in other words; an unknown id is refused."""
        did = runtime.add_decision(self.conn, "testproj", "owner", "Traps cost 50 gold.", REL, "TRAPCOST")
        qid = db.add_question(self.conn, "testproj", REL, "OTHER", "What does a trap cost?", "A", status="triage")
        with self.assertRaises(CmdError):
            triage.close_by_reference(self.conn, "testproj", qid, "decision", did + 999, "x")
        triage.close_by_reference(self.conn, "testproj", qid, "decision", did, "same price question")
        self.assertEqual(self.status(qid)["status"], "answered")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", "open"), [])


if __name__ == "__main__":
    unittest.main()
