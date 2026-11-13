"""Shared question answers, owner-check notes and Project decisions."""
import time
import unittest
from unittest import mock

from bzztest import REL, RoundCase
from pathlib import Path

from haivequeen import bees, db, question_service, runtime, triage


class AnswerTest(RoundCase):
    """question_service.answer stores the owner's answer and marks the rejected steps."""

    # A plan with three steps and an owner check question.
    def setUp(self):
        """Plan with packages P1 to P3 and one open owner check question."""
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2'}, {'id': 'P3'}])
        self.qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Please check the menu.', 'Check it in the game.')

    # The answer is stored like an inbox answer, and the ticked steps become rejected.
    def test_answer_marks_rejected_steps(self):
        """The question is answered by the owner, a decision is logged, and exactly the ticked steps are rejected."""
        question_service.answer(self.conn, self.qid, 'Menu and mana ring are wrong.', ['P2', 'P3'])
        row = self.conn.execute('SELECT status, answer, answered_by FROM questions WHERE id=?', (self.qid,)).fetchone()
        self.assertEqual((row['status'], row['answered_by']), ('open', 'owner'))
        self.assertEqual(row['answer'], 'Menu and mana ring are wrong.')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions WHERE text LIKE '%Menu and mana ring%'").fetchone()[0], 0)
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])

    # Wrong input changes nothing.
    def test_refusals_change_nothing(self):
        """An empty answer, ok together with rejected steps, and a second answer are refused and mark nothing."""
        with self.assertRaises(ValueError):
            question_service.answer(self.conn, self.qid, '   ', [])
        question_service.answer(self.conn, self.qid, 'ok', ['P1'])
        question_service.answer(self.conn, self.qid, 'ok', [])
        question_service.answer(self.conn, self.qid, 'No, wrong.', ['P1'])
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])


class ApiaryDecisionTest(RoundCase):
    """The checkbox stores the answer as a decision of the whole Project."""

    def test_apiary_answer_is_found_again_in_another_plan(self):
        """Checked: decision without plan, found for the same item in another plan (worded differently); not seen in another Project."""
        qid = db.add_question(self.conn, 'testproj', REL, 'colour', 'Which colour?', 'Red.')
        question_service.answer(self.conn, qid, 'Blue.', apiary=True)
        decisions = runtime.list_decisions(self.conn, 'testproj')
        self.assertEqual([d['plan_path'] for d in decisions], [None])
        known = triage._known_answer(self.conn, 'testproj', 'docs/plans/other/OTHER.md', 'colour')
        self.assertEqual(known[0], 'decision')
        self.assertIn('Blue.', known[2])
        self.assertIsNone(triage._known_answer(self.conn, 'otherproj', 'docs/plans/other/OTHER.md', 'colour'))
        self.assertEqual(runtime.list_decisions(self.conn, 'otherproj'), [])

    def test_without_checkbox_only_the_plan_decision(self):
        """Not checked: the decision keeps its plan, another plan of the Project is not answered."""
        qid = db.add_question(self.conn, 'testproj', REL, 'colour', 'Which colour?', 'Red.')
        question_service.answer(self.conn, qid, 'Blue.')
        self.assertEqual([d['plan_path'] for d in runtime.list_decisions(self.conn, 'testproj')], [REL])
        self.assertIsNone(triage._known_answer(self.conn, 'testproj', 'docs/plans/other/OTHER.md', 'colour'))

    def test_queen_instructions_name_both_rules(self):
        """roles/queen.md tells the Queen to check Project decisions first and to ask once on contradicting settings and rules."""
        text = (Path(__file__).resolve().parent.parent / 'roles' / 'queen.md').read_text(encoding='utf-8')
        self.assertIn('check the decisions of your Project (bzz decisions list)', text)
        self.assertIn('name the decision', text)
        self.assertIn('settings of the Project and its project rules contradict each other', text)
        self.assertIn('his answer decides', text)
        self.assertIn('asked again in another plan', text)


if __name__ == "__main__":
    unittest.main()
