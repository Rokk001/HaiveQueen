from unittest.mock import patch

from bzztest import RoundCase, REL
from haivequeen import db, hives, runtime, runstate, dashboard_data


class AnswerWaitRecoveryTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('RUNNING')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.qid = db.add_question(self.conn, 'testproj', REL, 'queen-ended', 'Continue?', '')
        hives.wait_for_answers(self.conn, 'testproj', REL, [self.qid])

    def test_answer_clears_wait_immediately_even_when_paused(self):
        runstate.pause(self.conn)
        db.answer_question(self.conn, self.qid, 'Continue with the existing decision', 'owner')
        row = hives.get(self.conn, 'testproj', REL)
        self.assertEqual(row['state'], 'waiting')
        self.assertIsNone(row['questions_json'])
        self.assertFalse(runstate.starts_allowed(self.conn))
        self.assertEqual(runtime.list_agents(self.conn, 'testproj'), [])

    def test_round_repairs_historical_wait_before_global_start_gate(self):
        self.conn.execute("UPDATE questions SET status='answered' WHERE id=?", (self.qid,))
        runstate.pause(self.conn)
        with patch('haivequeen.loop._do_start') as start:
            self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')
        start.assert_not_called()
        self.assertFalse(runstate.starts_allowed(self.conn))

    def test_unanswered_question_remains_blocking(self):
        runstate.pause(self.conn)
        self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')

    def test_owner_acceptance_note_does_not_release_wait(self):
        self.conn.execute("UPDATE questions SET item='owner-check' WHERE id=?", (self.qid,))
        db.answer_question(self.conn, self.qid, 'A note, not acceptance', 'owner')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')

    def test_stale_dashboard_state_uses_actual_blocker(self):
        text = dashboard_data.status_text({'state': 'answer', 'pending_questions': [], 'blocker': 'STOP'})
        self.assertEqual(text, dashboard_data.blocker_text('STOP'))

    def test_answer_does_not_resume_paused_plan_or_close_other_question(self):
        other = db.add_question(self.conn, 'testproj', REL, 'design', 'Choose a design', '')
        hives.wait_for_answers(self.conn, 'testproj', REL, [self.qid, other])
        runtime.set_plan_status(self.conn, 'testproj', REL, 'PAUSED', str(self.repo))
        db.answer_question(self.conn, self.qid, 'Continue', 'owner')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')
        self.assertEqual(self.conn.execute('SELECT status FROM plans WHERE project=? AND path=?', ('testproj', REL)).fetchone()[0], 'PAUSED')
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (other,)).fetchone()[0], 'open')

    def test_answer_and_wait_cleanup_roll_back_together(self):
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                db.answer_question(self.conn, self.qid, 'Continue', 'owner')
                raise RuntimeError('rollback')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (self.qid,)).fetchone()[0], 'open')

    def test_deleted_reference_without_pending_questions_is_repaired(self):
        self.conn.execute('DELETE FROM questions WHERE id=?', (self.qid,))
        hives.reconcile_answer_waits(self.conn, 'testproj')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')
