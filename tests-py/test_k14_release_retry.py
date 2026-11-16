"""Release proof and compatibility with historical owner retry answers."""
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
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'old%d' % n, ['R'])
        db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
        q = db.open_questions(self.conn, 'testproj', True)[0]
        db.answer_question(self.conn, q['id'], 'Try the corrected approach.', 'owner')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 4)
        result = bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [])
        self.assertEqual(result['class'], 'heavy')
        # End the embedded test Bee before recording the subsequent attempts.
        bees.close_embedded(self.conn, 'testproj', REL, 'P1')
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'new0', ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        for n in range(1, 4):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'new%d' % n, ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)
        db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
        self.assertEqual(len(db.open_questions(self.conn, 'testproj', True)), 1)
        self.assertFalse(bees.start_process(env, REL, 'P1', 'Bea', 'heavy', 'task', ['one'], [], []))
        self.assertIn('diagnosis', bees.LAST_REFUSAL)
        second = db.open_questions(self.conn, 'testproj', True)[0]
        db.answer_question(self.conn, second['id'], 'Use the owner-approved approach.', 'coordinator')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_retry_answers').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_reports').fetchone()[0], 8)
        # Replaying a historical report stays deduplicated across answer cycles.
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'old0', ['R'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 8)
        self.assertFalse(bees.start_process(env, REL, 'P1', 'Bea', 'heavy', 'task', ['one'], [], []))
        self.assertIn('diagnosis', bees.LAST_REFUSAL)

    def test_detailed_failure_answer_preserves_history_and_retry_limit(self):
        from haivequeen.i18n import tr
        self.mock_agent_worktrees()
        env = self.env()
        for language, author in [('English', 'owner'), ('German', 'coordinator')]:
            for n in range(4):
                bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', language + str(n), ['R'])
            db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
            question = next(q for q in db.open_questions(self.conn, 'testproj', True)
                            if q['item'] == 'P1')
            detail = tr(bees.FAILURE_QUESTION, language=language) + '\n\nAdditional diagnostic details.'
            self.conn.execute('UPDATE questions SET question=? WHERE id=?', (detail, question['id']))
            before = self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0]
            db.answer_question(self.conn, question['id'], 'Continue with the corrected approach.', author)
            self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 0)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], before)
            self.assertEqual(bees.start_process(env, REL, 'P1', 'Worker', 'medium', 'task', ['one'], [], [])['class'], 'heavy')
            bees.close_embedded(self.conn, 'testproj', REL, 'P1')
        for n in range(4):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'again' + str(n), ['R'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)
        self.assertFalse(bees.start_process(env, REL, 'P1', 'Worker', 'heavy', 'task', ['one'], [], []))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_retry_answers').fetchone()[0], 2)

    def test_historical_approval_is_applied_once_without_erasing_later_failures(self):
        for n in range(4):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'past%d' % n, ['R'])
            self.agent('past%d' % n, phase='bee', package='P1', status='done', ended_at='2026-01-01T10:00:00')
        qid = db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION + '\n\nDetails', '')
        # Model an answer ignored by the previous release, not a fresh answer.
        self.conn.execute("UPDATE questions SET status='answered', answered_by='owner', answered_at='2026-01-01T11:00:00', answer='Continue' WHERE id=?", (qid,))
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'later', ['R'])
        self.agent('later', phase='bee', package='P1', status='done', ended_at='2026-01-01T12:00:00')
        bees.reconcile_failure_answers(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_attempt_signals').fetchone()[0], 5)
        bees.reconcile_failure_answers(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM bee_retry_answers').fetchone()[0], 1)

    def test_historical_approval_with_unknown_attempt_time_stays_blocked(self):
        for n in range(4):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'unknown%d' % n, ['R'])
        qid = db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION + '\nDetails', '')
        self.conn.execute("UPDATE questions SET status='answered', answered_by='owner', answered_at='2026-01-01T11:00:00' WHERE id=?", (qid,))
        bees.reconcile_failure_answers(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)

    def test_historical_approval_with_invalid_time_stays_blocked(self):
        for n in range(4):
            name = 'invalid%d' % n
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', name, ['R'])
            self.agent(name, phase='bee', package='P1', status='done', ended_at='0000-invalid')
        qid = db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION + '\nDetails', '')
        self.conn.execute("UPDATE questions SET status='answered', answered_by='owner', answered_at='2026-01-01T11:00:00' WHERE id=?", (qid,))
        bees.reconcile_failure_answers(self.conn, 'testproj', REL, 'P1')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)

    def test_failure_heading_is_not_a_substring_match(self):
        for text in ('Other question: ' + bees.FAILURE_QUESTION,
                     bees.FAILURE_QUESTION + ' Not a diagnostic paragraph.', '', None):
            self.assertFalse(bees.is_failure_question(text))
        self.assertTrue(bees.is_failure_question(bees.FAILURE_QUESTION + '\r\nDetails'))

    def test_unrelated_or_automatic_answers_do_not_reset_failures(self):
        env = self.env()
        for n in range(4):
            bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'heavy', 'failed', 'old%d' % n, ['R'])
        unrelated = db.add_question(self.conn, 'testproj', REL, 'P1', 'Which approach?', '')
        db.answer_question(self.conn, unrelated, 'Try again.', 'owner')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'heavy'), 3)
        db.add_question(self.conn, 'testproj', REL, 'P1', bees.FAILURE_QUESTION, '')
        q = db.open_questions(self.conn, 'testproj', True)[0]
        self.conn.execute('UPDATE questions SET question=? WHERE id=?', (bees.FAILURE_QUESTION + '\n\nDetails', q['id']))
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
