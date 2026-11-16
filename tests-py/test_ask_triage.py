"""Questions that agents ask with `bzz ask` receive evidence review; a duplicate or an already decided item is closed with a reference."""
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

    def test_new_question_waits_for_evidence_review(self):
        res = self.ask('N1', 'Resolve the remaining comment conflict?')
        self.assertEqual(res['status'], 'triage')
        self.assertEqual(self.status(res['id'])['status'], 'triage')
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', 'open'), [])

    def test_round_starts_review_with_configured_class(self):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'first'}])
        self.machine['diagnosisClass'] = 'light'
        qid = self.ask('N2')['id']
        from haivequeen import triage
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        state = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
        worker = next(a for a in runtime.list_agents(self.conn, 'testproj') if a['name'] == state['agent'])
        self.assertEqual(worker['class'], 'light')
        self.assertEqual(self.status(qid)['status'], 'triage')

    def reviewed(self, kind):
        from haivequeen import triage
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'first'}])
        qid = self.ask('G1', 'Which conflict wording?')['id']
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        state = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
        report = {**state['scope'], 'kind': kind, 'reviewed_decisions': [],
                  'evidence': ['RenderManager.cpp getBrighterMaterial: both conflict arms are comments'],
                  'analysis': 'The implementation is identical.',
                  'next_action': 'Combine the comment explanations, then verify the affected function.',
                  'why_new': 'The remaining conflict was inspected directly.',
                  'question': 'Which user-visible behavior is required?',
                  'why_owner': 'Two mutually exclusive behaviors remain unspecified.',
                  'recommendation': 'Keep the documented behavior.'}
        (self.env().run_dir(state['agent']) / 'question-diagnosis.json').write_text(json.dumps(report), encoding='utf-8')
        self.finish(state['agent'])
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        return self.status(qid), report

    def test_comment_conflict_is_answered_without_owner_and_not_marked_done(self):
        question, report = self.reviewed('technical')
        self.assertEqual(question['status'], 'answered')
        self.assertEqual(question['answered_by'], 'coordinator')
        self.assertIn(report['next_action'], question['answer'])
        self.assertNotEqual(self.conn.execute('SELECT status FROM plans WHERE path=?', (REL,)).fetchone()[0], 'DONE')
        self.assertFalse(runtime.list_questions(self.conn, 'testproj', 'open'))

    def test_product_question_reaches_owner_with_evidence(self):
        question, report = self.reviewed('product')
        self.assertEqual(question['status'], 'open')
        self.assertIn(report['why_owner'], question['question'])
        self.assertIn(report['evidence'][0], question['question'])

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

    def test_new_decision_supersedes_old_answer_and_auto_duplicates(self):
        old = db.add_question(self.conn, "testproj", REL, "revision", "Which?", "")
        db.answer_question(self.conn, old, "Old instruction.", "owner")
        self.conn.execute("UPDATE questions SET answered_at='2026-01-01T10:00:00' WHERE id=?", (old,))
        self.conn.execute("UPDATE decisions SET ts='2026-01-01T10:00:00' WHERE item='revision'")
        self.ask("revision")  # Automatic references must not become new authority.
        did = runtime.add_decision(self.conn, "testproj", "owner", "Corrected instruction.", REL, "revision")
        self.conn.execute("UPDATE decisions SET ts='2026-01-02T10:00:00' WHERE id=?", (did,))
        result = self.ask("revision")
        self.assertIn("decision %d" % did, result["answer"])
        self.assertIn("Corrected instruction.", result["answer"])

    def test_independent_decision_wins_same_second_and_superseded_is_ignored(self):
        old = db.add_question(self.conn, "testproj", REL, "same-second", "Which?", "")
        db.answer_question(self.conn, old, "Old answer.", "owner")
        stamp = self.status(old)["answered_at"]
        did = runtime.add_decision(self.conn, "testproj", "owner", "New instruction.", REL, "same-second")
        self.conn.execute("UPDATE decisions SET ts=? WHERE id=?", (stamp, did))
        self.assertIn("decision %d" % did, self.ask("same-second")["answer"])
        runtime.update_decision(self.conn, did, status="superseded")
        self.assertIn("question %d" % old, self.ask("same-second")["answer"])

    def test_new_owner_answer_can_correct_older_decision(self):
        did = runtime.add_decision(self.conn, "testproj", "owner", "Old instruction.", REL, "revision")
        self.conn.execute("UPDATE decisions SET ts='2026-01-01T10:00:00' WHERE id=?", (did,))
        old = db.add_question(self.conn, "testproj", REL, "revision", "Which?", "")
        db.answer_question(self.conn, old, "New answer.", "owner")
        self.conn.execute("UPDATE questions SET answered_at='2026-01-02T10:00:00' WHERE id=?", (old,))
        self.conn.execute("UPDATE decisions SET ts='2026-01-02T10:00:00' WHERE item='revision' AND id!=?", (did,))
        result = self.ask("revision")
        self.assertIn("question %d" % old, result["answer"])
        self.assertIn("New answer.", result["answer"])

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
        self.assertEqual({q["id"] for q in runtime.list_questions(self.conn, "testproj", "triage")}, {waiting, fresh})

    def test_paused_cancelled_and_memory_wait_never_start_review(self):
        from haivequeen import triage
        from unittest import mock
        qid = self.ask('P1')['id']
        for status, ram in [('PAUSED', 8192), ('CANCELLED', 8192), ('DONE', 8192), ('RUNNING', 0)]:
            self.plan(status, [{'id': 'P1'}])
            with mock.patch('haivequeen.loop._do_start') as launch:
                triage.advance(self.env(), ram, alive=lambda pid: False)
            launch.assert_not_called()
            self.assertEqual(self.status(qid)['status'], 'triage')

    def test_review_restarts_on_changed_question_without_applying_stale_answer(self):
        from haivequeen import triage
        self.plan('RUNNING', [{'id': 'P1'}])
        qid = self.ask('P1')['id']
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        state = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
        self.ask('P1', 'New requirements change the decision.')
        self.finish(state['agent'])
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        self.assertEqual(self.status(qid)['status'], 'triage')
        new = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
        self.assertNotEqual(new['scope'], state['scope'])
        self.assertNotEqual(new['agent'], state['agent'])

    def test_provider_recovery_does_not_answer_the_original_technical_question(self):
        from haivequeen import triage
        self.plan('RUNNING', [{'id': 'P1'}])
        qid = self.ask('P1', 'How should the comment conflict be resolved?')['id']
        for _ in range(2):
            triage.advance(self.env(), 8192, alive=lambda pid: False)
            state = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
            self.finish(state['agent'])  # Provider produced no structured answer.
            triage.advance(self.env(), 8192, alive=lambda pid: False)
        self.assertEqual(self.status(qid)['status'], 'triage')
        external = runtime.list_questions(self.conn, 'testproj', 'open')
        self.assertEqual(len(external), 1)
        self.assertNotEqual(external[0]['id'], qid)
        db.answer_question(self.conn, external[0]['id'], 'Provider configuration restored.', 'owner')
        triage.advance(self.env(), 8192, alive=lambda pid: False)
        state = json.loads(runtime.kv_get(self.conn, 'testproj', 'question-review:' + str(qid)))
        self.assertTrue(state.get('agent'))
        self.assertEqual(self.status(qid)['status'], 'triage')
        self.assertFalse(self.status(qid)['answer'])


if __name__ == "__main__":
    unittest.main()
