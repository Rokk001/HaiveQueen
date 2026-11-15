from unittest import TestCase, mock
from bzztest import REL, RoundCase
from haivequeen import bees, dashboard_data, hives, runtime


class WaitTextTests(TestCase):
    def test_no_reason_does_not_claim_provider_is_busy(self):
        for state in ('codex_wait', 'provider_wait'):
            self.assertEqual(dashboard_data.status_text({'state':state}), 'Waiting for scheduler reassessment')

    def test_stopped_scheduler_overrides_old_provider_blocker(self):
        for state, label in [('STOPPED','Orchestrator stopped'),('STOPPING','Orchestrator stopping'),('PAUSED','Orchestrator paused')]:
            row={'state':'codex_wait','scheduler_state':state,'blocker':'waiting for provider codex: no free slot'}
            self.assertEqual(dashboard_data.status_text(row),label)
        row['pending_questions']=[42];row['state']='answer'
        self.assertEqual(dashboard_data.status_text(row),'Waiting for answer (#42)')

    def test_live_scheduler_reports_actual_reason(self):
        for reason in ('quota used up','no free slot','usage unknown, no recent reading (check failed)'):
            label=dashboard_data.status_text({'state':'codex_wait','scheduler_state':'RUNNING','blocker':'waiting for provider codex: '+reason})
            self.assertIn(reason,label)


class WaitRecoveryTests(RoundCase):
    def seed(self):
        self.plan('IN_PROGRESS', [{'id':'P1','title':'First'}])
        hives.create(self.conn,'testproj',REL,'codex','123456')
        bees.wait_for_provider(self.conn,'testproj',REL,'codex')
        runtime.kv_set(self.conn,'testproj','hive-blocker:'+REL,'waiting for provider codex: no free slot')

    def test_available_provider_clears_stale_reason(self):
        self.seed()
        self.assertTrue(bees.provider_available(self.conn,'testproj',REL,'codex',1,False))
        self.assertEqual(hives.get(self.conn,'testproj',REL)['state'],'waiting')
        self.assertFalse(runtime.kv_get(self.conn,'testproj','hive-blocker:'+REL))

    def test_real_limits_and_used_slots_remain_protected(self):
        self.seed()
        for free,limited in [(0,False),(1,True)]:
            self.assertFalse(bees.provider_available(self.conn,'testproj',REL,'codex',free,limited))
            self.assertEqual(hives.get(self.conn,'testproj',REL)['state'],'codex_wait')

    def test_orphan_wait_is_reassessed_by_normal_scheduler(self):
        self.seed()
        self.conn.execute('DELETE FROM bee_provider_wait')
        self.round()
        self.assertNotEqual(hives.get(self.conn,'testproj',REL)['state'],'codex_wait')
        self.assertNotIn('provider unknown',runtime.kv_get(self.conn,'testproj','hive-blocker:'+REL) or '')

    def test_missing_wait_table_is_recoverable(self):
        self.seed()
        self.conn.execute('DROP TABLE bee_provider_wait')
        self.round()
        self.assertNotEqual(hives.get(self.conn,'testproj',REL)['state'],'codex_wait')

    def test_valid_wait_wakes_when_provider_is_available(self):
        self.seed()
        self.round()
        self.assertNotEqual(hives.get(self.conn,'testproj',REL)['state'],'codex_wait')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM bee_provider_wait').fetchone())

    def test_recovered_wait_still_obeys_ram_gate(self):
        self.seed()
        self.conn.execute('DELETE FROM bee_provider_wait')
        self.round(free_ram=lambda:0.0)
        self.assertFalse(runtime.list_agents(self.conn,'testproj','active'))
        self.assertIn('free RAM',runtime.kv_get(self.conn,'testproj','hive-blocker:'+REL))
