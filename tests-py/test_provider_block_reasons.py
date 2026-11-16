import json
import os
from datetime import datetime, timezone
from unittest import mock
from bzztest import RoundCase, REL
from haivequeen import bees, hives, runtime, scheduler, usage


class ProviderReasons(RoundCase):
    def provider(self):
        return dict(name='test', slots=14, login=True, protocol='codex', use='preferred',
                    classes={'medium': {'model': 'test-model'}}, capabilities=['Coding'], limit='none')

    def test_selection_explains_each_actual_gate_and_retains_free_capacity(self):
        base = self.provider()
        cases = [({'slots': 0}, 0, 'no slots configured'), ({}, 0, 'all 14 slots occupied'),
                 ({'login': False, 'notReady': 'login missing'}, 13, 'login missing'),
                 ({'unavailable': 'offline'}, 13, 'offline'),
                 ({'enabled': False}, 13, 'disabled'),
                 ({'classes': {}}, 13, 'no model configured'),
                 ({'capabilities': []}, 13, 'missing capability')]
        for changes, free, reason in cases:
            with self.subTest(reason=reason):
                rejected = {}
                self.assertIsNone(scheduler.select_provider([{**base, **changes}], 'medium', [], {'test': free}, conn=self.conn, rejected=rejected))
                self.assertIn(reason, rejected['test'])
        self.assertIsNotNone(scheduler.select_provider([base], 'medium', [], {'test':13}, conn=self.conn))

    def test_unknown_failed_stale_and_fresh_quota_use_the_same_gate(self):
        p = {**self.provider(), 'limit': 'codex'}
        now = 2000000000
        good = {'saved': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                'sevenDay': {'utilization': .4, 'resetsAt': now + 100000}}
        cases = [({}, 'no successful observation'),
                 ({**good, 'refresh_error': 'private diagnostic'}, 'last refresh failed'),
                 ({**good, 'saved': datetime.fromtimestamp(now-61, timezone.utc).isoformat()}, '61.0 s old'),
                 ({**good, 'sevenDay': None}, 'no valid usage window'), (good, '')]
        for snapshot, reason in cases:
            runtime.kv_set(self.conn, '*', 'usage-codex:test', json.dumps(snapshot))
            with self.subTest(reason=reason), mock.patch.dict(os.environ, {}, clear=True):
                detail = usage.quota_block_reason(self.conn, p, now)
                self.assertEqual(usage.quota_verified(self.conn, p, now), not bool(reason))
                self.assertIn(reason, detail)
                self.assertNotIn('private diagnostic', detail)
                if reason:
                    self.assertIn(reason, scheduler.provider_block_reason(p, 'medium', [], {'test':13}, self.conn, now=now))

    def test_threshold_and_exhaustion_are_not_slot_shortage(self):
        p = {**self.provider(), 'limit': 'codex', 'capabilityThresholds': {'Coding': 70}}
        limits = {'test': {'usedPercent': 80, 'resetHours': 24}}
        detail = scheduler.provider_block_reason(p, 'medium', [], {'test':13}, self.conn, limits=limits)
        self.assertIn('threshold (70%)', detail)
        with mock.patch.object(scheduler, 'threshold_reached', return_value=False), mock.patch.object(scheduler, 'provider_reserve', return_value=0):
            self.assertEqual(scheduler.provider_block_reason(self.provider(), 'medium', [], {'test':13}, self.conn), 'quota exhausted')

    def test_start_returns_and_logs_provider_specific_refusal(self):
        self.plan('IN_PROGRESS', [{'id':'P1','class':'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        for p in env.providers:
            p['slots'] = 0
        with mock.patch('haivequeen.agent_process.counts', return_value=True), mock.patch.object(env, 'log') as log:
            result = bees.start_process(env, REL, 'P1', 'worker', 'medium', 'task', ['step'], [], [])
        self.assertFalse(result)
        self.assertIn('Waiting for provider (class medium)', bees.LAST_REFUSAL)
        self.assertIn('claude: no slots configured (0)', bees.LAST_REFUSAL)
        self.assertIn('free slots 0/0', bees.LAST_REFUSAL)
        self.assertIn(bees.LAST_REFUSAL, log.call_args[0][0])

    def test_limited_provider_reason_and_excluded_provider_are_preserved(self):
        p = self.provider()
        reasons = {}
        with mock.patch.object(scheduler, 'provider_block_reason', return_value='quota stale: observation 61 s old'):
            self.assertIsNone(bees.select_route([p], {'test':13}, 'test', 'medium', [], [], False, ['test'], self.conn, reasons))
        self.assertIn('quota stale', reasons['test'])
        reasons = {}
        self.assertIsNone(bees.select_route([p], {'test':13}, 'test', 'medium', [], ['test'], False, conn=self.conn, rejected=reasons))
        self.assertEqual(reasons['test'], 'excluded by settings')
