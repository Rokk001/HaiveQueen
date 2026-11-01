"""Lifecycle recovery regressions against an isolated temporary runtime."""
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import db, hives, plans, runtime


# German state text of the Hive as the dashboard shows it without a running Queen.
def hive_text(conn):
    """"Queen: <state>" of the Hive of the dummy plan: the state label, or "keine aktive Queen" when it has none."""
    data = next(iter(hives.hive_data(conn, 'testproj', runtime.list_agents(conn, 'testproj', 'active'), REL)), None) or {"queen": None, "bees": [], **(hives.get(conn, 'testproj', REL) or {})}
    return "Queen: " + (hives.state_label(data) or "keine aktive Queen")


# Exercise completion evidence and preserve unresolved owner decisions.
class HiveLifecycleRecoveryTests(RoundCase):
    """Reconcile stale Hive rows without inventing completion evidence."""

    # Seed a stale Hive and an integrated row whose ready link was lost.
    def integrated(self):
        """Create recoverable integration evidence and return its queue row."""
        self.plan('IN_PROGRESS')
        with (self.repo / REL).open('a', encoding='utf-8') as stream:
            stream.write('\nFeature-Branch: `feature/recovery`\n')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running'")
        return db.add_ready(self.conn, 'testproj', 'feature/recovery', 'a2b2a43', REL, '')

    # Supply only the branch read used to verify a missing ready link.
    def recover_round(self):
        """Run one round with a verified local branch tip and no real agents."""
        with mock.patch('haivequeen.hives.subprocess.run', return_value=mock.Mock(returncode=0, stdout='a2b2a43' + '0' * 33)):
            self.round(alive=lambda pid: False)

    # DONE source status must retire its stale Hive without starting another Queen.
    def test_done_plan_retires_stale_hive_and_reservation(self):
        """A completed plan cannot retain a running Hive or integration place."""
        self.plan('DONE')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running'")
        hives.take_integration(self.conn, 'testproj', REL)
        genuine = db.add_question(self.conn, 'testproj', REL, 'design', 'Keep this decision?', '')
        automatic = db.add_question(self.conn, 'testproj', REL, 'queen-ended', 'Old automatic end', '')
        self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'done')
        self.assertFalse(self.names())
        self.assertFalse(self.conn.execute('SELECT 1 FROM hive_integration').fetchone())
        text = hive_text(self.conn)
        self.assertIn('abgeschlossen', text)
        questions = {q['id']: q for q in runtime.list_questions(self.conn, 'testproj')}
        self.assertEqual(questions[genuine]['status'], 'open')
        self.assertEqual(questions[automatic]['answered_by'], 'auto')

    # Accepted owner checks permit completion even when ready_id was lost.
    def test_integrated_tip_and_accepted_check_recover_missing_link(self):
        """Queue evidence closes the plan and its obsolete automatic question."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check it', '')
        db.answer_question(self.conn, qid, 'ok', 'owner')
        automatic = db.add_question(self.conn, 'testproj', REL, 'queen-ended', 'Old automatic end', '')
        self.recover_round()
        self.assertEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding='utf-8')).status, 'DONE')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'done')
        self.assertEqual(next(q for q in runtime.list_questions(self.conn, 'testproj') if q['id'] == automatic)['answered_by'], 'auto')
        self.assertFalse(self.names())

    # An unanswered owner check must remain open even after integration.
    def test_missing_ready_link_preserves_pending_owner_check(self):
        """Recovery parks the Hive at the existing owner question."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check it', '')
        self.recover_round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'owner_check')
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open')[0]['id'], qid)
        self.assertFalse(self.names())

    # A rejection must invalidate the old integrated row across future rounds.
    def test_rejected_check_does_not_reclose_from_old_queue_evidence(self):
        """Only a subsequent integration may close a reopened Hive."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check it', '')
        db.answer_question(self.conn, qid, 'Still broken', 'owner')
        self.recover_round()
        self.recover_round()
        self.assertNotEqual(hives.get(self.conn, 'testproj', REL)['state'], 'done')
        self.assertNotEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding='utf-8')).status, 'DONE')

    # Missing, mismatched, or superseded evidence cannot establish completion.
    def test_unverified_queue_evidence_is_rejected(self):
        """An unrelated branch, changed tip, or newer ready row stays unresolved."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        env = self.env()
        hive = hives.get(self.conn, 'testproj', REL)
        with mock.patch('haivequeen.hives.subprocess.run', return_value=mock.Mock(returncode=0, stdout='different')):
            self.assertIsNone(hives.integrated_ready(self.conn, env, hive))
        self.conn.execute("UPDATE queue SET branch='other'")
        self.assertIsNone(hives.integrated_ready(self.conn, env, hive))
        self.conn.execute("UPDATE queue SET branch=?,status='ready'", ('feature/recovery',))
        self.assertIsNone(hives.integrated_ready(self.conn, env, hive))

    # Stale running state must not be rendered as a queued start.
    def test_pane_reports_ended_queen_and_pending_questions(self):
        """Without a live Queen the pane displays the state and question numbers."""
        self.integrated()
        self.agent('ended', phase='queen', status='done', ended=1)
        text = hive_text(self.conn)
        self.assertIn('keine aktive Queen', text)
        self.assertNotIn('wartet auf Start', text)
        qid = db.add_question(self.conn, 'testproj', REL, 'design', 'Real question', '')
        hives.wait_for_answers(self.conn, 'testproj', REL, [qid])
        text = hive_text(self.conn)
        self.assertIn('wartet auf Antwort (#%s)' % qid, text)

    # Scheduler reasons must reach the pane without creating a running Queen.
    def test_pane_displays_recorded_blocker(self):
        """The actual failed start reason replaces a generic start label."""
        self.integrated()
        self.conn.execute("UPDATE hives SET state='waiting'")
        runtime.kv_set(self.conn, 'testproj', 'hive-blocker:' + REL, 'no provider with a free slot')
        text = hive_text(self.conn)
        self.assertIn('Kein freier Anbieter', text)

    # Source completion must not retire a Hive whose agents still work.
    def test_done_plan_with_active_agent_is_retained(self):
        """Both Queens and Bees keep their completed-plan Hive visible until exit."""
        for phase in ('queen', 'bee'):
            with self.subTest(phase=phase):
                self.plan('DONE')
                hives.create(self.conn, 'testproj', REL, 'claude', '123456')
                self.conn.execute("UPDATE hives SET state='running'")
                self.agent('active-' + phase, phase=phase, status='running', pid=123)
                with mock.patch('haivequeen.agent_process.pid_reused', return_value=False):
                    hives.reconcile_completed(self.conn, self.env(), alive=lambda pid: True)
                self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'running')
                self.agent('active-' + phase, phase=phase, status='done', pid=None)

    # A live agent also prevents inferred integration completion.
    def test_integrated_plan_with_active_agent_is_retained(self):
        """An integrated row never closes work that an agent is still performing."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        self.agent('active', phase='queen', status='starting', pid=None)
        hives.reconcile_completed(self.conn, self.env(), alive=lambda pid: False)
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'running')
        self.assertNotEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding='utf-8')).status, 'DONE')

    # Genuine owner decisions remain blockers even when an integration is accepted.
    def test_real_owner_question_blocks_inferred_completion(self):
        """Only obsolete automatic end questions may be bypassed."""
        ready = self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'design', 'A real decision', '')
        automatic = db.add_question(self.conn, 'testproj', REL, 'queen-ended', 'Automatic end', '')
        self.recover_round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')
        self.assertNotEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding='utf-8')).status, 'DONE')
        self.assertEqual({q['id'] for q in runtime.list_questions(self.conn, 'testproj', status='open')}, {qid, automatic})

    # Completion maintenance must run while Queen starts are paused.
    def test_done_hive_reconciles_before_pause_gate(self):
        """Paused scheduling still retires an idle completed Hive."""
        self.plan('DONE')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running'")
        with mock.patch('haivequeen.runstate.starts_allowed', return_value=False):
            self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'done')
        self.assertFalse(self.names())

    # Historical rejection semantics must survive a missing ready link.
    def test_historical_negative_owner_check_is_not_reclosed(self):
        """Previously reopened work cannot reuse its rejected integration."""
        self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check-answered', 'Check it', '')
        db.answer_question(self.conn, qid, 'Still broken', 'owner')
        hive = hives.get(self.conn, 'testproj', REL)
        self.assertIsNone(hives.integrated_ready(self.conn, self.env(), hive))

    # The actual historical acceptance must not be mistaken for a rejection.
    def test_historical_ist_ok_acceptance_recovers_completion(self):
        """The stored owner-check-answered acceptance permits verified completion."""
        self.integrated()
        runtime.queue_upsert(self.conn, 'testproj', 'feature/recovery', status='integrated')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check-answered', 'Check it', '')
        db.answer_question(self.conn, qid, 'ist ok', 'owner')
        self.recover_round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'done')

    # Global resource gates must replace older per-provider blockers.
    def test_low_ram_replaces_stale_provider_blocker(self):
        """The pane reports current RAM exhaustion after a prior provider wait."""
        self.plan('IN_PROGRESS')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        runtime.kv_set(self.conn, 'testproj', 'hive-blocker:' + REL, 'no provider with a free slot')
        self.round(free_ram=lambda: 0.0)
        data = hives.hive_data(self.conn, 'testproj', [], REL)[0]
        self.assertTrue(data['blocker'].startswith('free RAM '))
        self.assertIn('Zu wenig freier Arbeitsspeicher', hives.state_label(data))
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')

    # Stop and pause gates must replace a prior Hive-limit blocker.
    def test_global_stop_states_replace_stale_hive_limit(self):
        """Each global gate has its actual German label without changing Hive state."""
        self.plan('IN_PROGRESS')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        for state, label in (('STOP', 'HaiveQueen gestoppt'), ('PAUSED', 'HaiveQueen pausiert')):
            with self.subTest(state=state):
                runtime.kv_set(self.conn, 'testproj', 'hive-blocker:' + REL, 'Hive limit 2 reached')
                with mock.patch('haivequeen.loop.stop_flag') as flag, \
                     mock.patch('haivequeen.runstate.starts_allowed', return_value=False), \
                     mock.patch('haivequeen.runstate.get_state', return_value={'state': state}):
                    flag.return_value.exists.return_value = state == 'STOP'
                    self.round()
                data = hives.hive_data(self.conn, 'testproj', [], REL)[0]
                self.assertEqual(data['blocker'], state)
                self.assertEqual(hives.state_label(data), label)
                self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')

    # A blocker written while HaiveQueen was paused or stopped disappears once the gates are open.
    def test_stale_global_blocker_is_cleared_when_the_gates_are_open(self):
        """PAUSED and STOP blockers are removed by the next round; another blocker text is left alone."""
        self.plan('IN_PROGRESS')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        for stale in ('PAUSED', 'STOP'):
            runtime.kv_set(self.conn, 'testproj', 'hive-blocker:' + REL, stale)
            self.round()
            self.assertNotIn(runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + REL), ('PAUSED', 'STOP'), stale)

    # Global gate presentation must not override owner questions or active agents.
    def test_global_gate_preserves_owner_priority_and_active_agent(self):
        """Question IDs remain visible and active Hive blockers are untouched."""
        self.plan('IN_PROGRESS')
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        qid = db.add_question(self.conn, 'testproj', REL, 'design', 'Real decision', '')
        hives.wait_for_answers(self.conn, 'testproj', REL, [qid])
        with mock.patch('haivequeen.runstate.starts_allowed', return_value=False), \
             mock.patch('haivequeen.runstate.get_state', return_value={'state': 'PAUSED'}):
            self.round()
        data = hives.hive_data(self.conn, 'testproj', [], REL)[0]
        self.assertEqual(hives.state_label(data), 'wartet auf Antwort (#%s)' % qid)
        self.conn.execute("UPDATE hives SET state='running'")
        self.agent('active', phase='queen', status='starting', pid=None)
        runtime.kv_set(self.conn, 'testproj', 'hive-blocker:' + REL, 'existing')
        with mock.patch('haivequeen.runstate.starts_allowed', return_value=False), \
             mock.patch('haivequeen.runstate.get_state', return_value={'state': 'PAUSED'}):
            self.round()
        self.assertEqual(runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + REL), 'existing')
        self.assertEqual(self.ag('active')['status'], 'starting')
