"""An answer and all package decisions commit together or not at all."""
from unittest import mock
from bzztest import REL, RoundCase
from haivequeen import bees, db, question_service


class AnswerAtomicTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2'}])
        self.qid = db.add_question(self.conn, 'testproj', REL, 'scope', 'Which packages?', '')

    def assert_unchanged(self):
        row = self.conn.execute('SELECT status, answer FROM questions WHERE id=?', (self.qid,)).fetchone()
        self.assertEqual(row['status'], 'open')
        self.assertFalse(row['answer'])
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)

    def test_unknown_package_does_not_answer_question(self):
        with self.assertRaises(ValueError):
            question_service.answer(self.conn, self.qid, 'Reject these.', ['P1', 'missing'])
        self.assert_unchanged()

    def test_invalid_package_types_change_nothing(self):
        for rejected in ('P1', ['P1', None], ['P1', {}]):
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                question_service.answer(self.conn, self.qid, 'Reject these.', rejected)
            self.assert_unchanged()

    def test_valid_answer_and_rejections_commit_together(self):
        question_service.answer(self.conn, self.qid, 'Reject these.', ['P1', 'P2'])
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), ['P1', 'P2'])
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?',
                                          (self.qid,)).fetchone()[0], 'answered')
        self.assertFalse(self.conn.in_transaction)

    def test_later_write_failure_rolls_back_answer_and_earlier_rejection(self):
        original = bees.set_package_state

        def fail_second(conn, project, plan, package, *args):
            if package == 'P2':
                raise OSError('simulated storage error')
            return original(conn, project, plan, package, *args)

        with mock.patch.object(bees, 'set_package_state', side_effect=fail_second):
            with self.assertRaises(OSError):
                question_service.answer(self.conn, self.qid, 'Reject these.', ['P1', 'P2'])
        self.assert_unchanged()
