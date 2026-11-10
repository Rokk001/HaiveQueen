"""Release proof and fresh failure counting after an owner answer."""
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, db, hives


class ReleaseRetryTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)

    def test_new_file_is_not_end_proof(self):
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'Ada', 'medium', 'claude', queen='old')
        report = self.wt / 'result.txt'
        report.write_text('done', encoding='utf-8')
        self.assertTrue(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)))
        self.assertEqual(len(bees.unconfirmed_embedded(self.conn, 'testproj', REL)), 1)

    def test_end_and_result_are_both_required(self):
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'Ada', 'medium', 'claude', queen='queen')
        self.agent('Ada', phase='bee', package='P1', status='done', started_at=db.now(), ended_at=db.now())
        self.assertTrue(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {'name': 'queen'}))
        report = self.wt / 'result.txt'
        report.write_text('done', encoding='utf-8')
        self.assertEqual(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)), '')

    def test_running_process_cannot_release_but_missing_process_can(self):
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'Ada', 'medium', 'claude')
        self.agent('Ada', phase='bee', package='P1', status='running', started_at=db.now(), pid=123)
        report = self.wt / 'result.txt'
        report.write_text('done', encoding='utf-8')
        with mock.patch('haivequeen.platform.process_alive', return_value=True):
            self.assertTrue(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)))
        with mock.patch('haivequeen.platform.process_alive', return_value=False):
            self.assertEqual(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)), '')

    def test_answer_retries_at_highest_class_and_preserves_history(self):
        self.mock_agent_worktrees()
        env = self.env()
        for n in range(4):
            bees.report_attempt(env, REL, 'P1', 'heavy', 'failed', 'old%d' % n, ['R'])
        q = db.open_questions(self.conn, 'testproj', True)[0]
        db.answer_question(self.conn, q['id'], 'Try the corrected approach.', 'owner')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 4)
        result = bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [])
        self.assertEqual(result['class'], 'heavy')
        # End the embedded test Bee before recording the subsequent attempts.
        bees.close_embedded(self.conn, 'testproj', REL, 'P1')
        bees.report_attempt(env, REL, 'P1', 'heavy', 'failed', 'new0', ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        for n in range(1, 4):
            bees.report_attempt(env, REL, 'P1', 'heavy', 'failed', 'new%d' % n, ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)
        self.assertEqual(len(db.open_questions(self.conn, 'testproj', True)), 1)
        self.assertFalse(bees.start_process(env, REL, 'P1', 'Bea', 'heavy', 'task', ['one'], [], []))
        self.assertIn('no higher class', bees.LAST_REFUSAL)
        second = db.open_questions(self.conn, 'testproj', True)[0]
        db.answer_question(self.conn, second['id'], 'Use the owner-approved approach.', 'coordinator')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_retry_answers').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_reports').fetchone()[0], 8)
        # Replaying a historical report stays deduplicated across answer cycles.
        bees.report_attempt(env, REL, 'P1', 'heavy', 'failed', 'old0', ['R'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 8)
        self.assertEqual(bees.start_process(env, REL, 'P1', 'Bea', 'heavy', 'task', ['one'], [], [])['class'], 'heavy')

    def test_unrelated_or_automatic_answers_do_not_reset_failures(self):
        env = self.env()
        for n in range(4):
            bees.report_attempt(env, REL, 'P1', 'heavy', 'failed', 'old%d' % n, ['R'])
        unrelated = db.add_question(self.conn, 'testproj', REL, 'P1', 'Which approach?', '')
        db.answer_question(self.conn, unrelated, 'Try again.', 'owner')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)
        q = db.open_questions(self.conn, 'testproj', True)[0]
        db.answer_question(self.conn, q['id'], 'Duplicate.', 'auto')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)

    def test_retry_boundary_follows_answer_order_not_question_order(self):
        # Pending questions can be answered out of creation order.
        older = db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
        newer = db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
        for attempt in ('a0', 'a1'):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', attempt, ['R'])
        db.answer_question(self.conn, newer, 'Try this approach.', 'owner')
        for attempt in ('b0', 'b1'):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', attempt, ['R'])
        db.answer_question(self.conn, older, 'Use the revised approach.', 'coordinator')
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'c0', ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 5)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_retry_answers').fetchone()[0], 2)
        self.assertEqual([r[0] for r in self.conn.execute('SELECT previous_counts FROM bee_retry_answers')],
                         ['{"heavy": 1}', '{"heavy": 1}'])

    def test_every_open_bee_needs_proof(self):
        for name in ('Ada', 'Bea'):
            bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', name, 'medium', 'claude')
        self.agent('Ada', phase='bee', package='P1', status='done', started_at=db.now(), ended_at=db.now())
        report = self.wt / 'result.txt'
        report.write_text('done', encoding='utf-8')
        self.assertIn('Bea', bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)))
        self.agent('Bea', phase='bee', package='P2', status='done', started_at=db.now(), ended_at=db.now())
        self.assertTrue(bees.release_refusal(self.conn, 'testproj', REL, 'P1', {}, str(report)))
