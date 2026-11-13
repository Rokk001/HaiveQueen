"""Quota readings must be current and keep used/remaining semantics distinct."""
import json
from unittest import mock
from bzztest import RoundCase
from haivequeen import usage, runtime


class LiveUsage(RoundCase):
    def test_live_quota_replaces_older_session_percentage(self):
        provider = {'name': 'test-codex', 'protocol': 'codex', 'command': 'codex'}
        reply = {'rateLimits': {'secondary': {'usedPercent': 45, 'windowDurationMins': 10080, 'resetsAt': 2000000000}}}
        with mock.patch.object(usage, 'read_codex_rate_limits', return_value=reply, create=True) as live, \
                mock.patch.object(usage, 'get_codex_usage', return_value={'five_hour': None, 'seven_day': {'pct': 41, 'at': 2000000000}}), \
                mock.patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('BZZ_DATA_DIR', None)
            os.environ.pop('BZZ_RUNTIME_OVERRIDE', None)
            self.assertTrue(usage.refresh_codex_usage(self.conn, provider=provider, force=True))
        live.assert_called_once()
        value = json.loads(runtime.kv_get(self.conn, '*', 'usage-codex:test-codex'))
        self.assertEqual(value['sevenDay']['utilization'], .45)
        self.assertEqual(value['source'], 'codex:account/rateLimits/read')

    def test_fallback_does_not_renew_observation_time(self):
        folder = self.base / 'sessions'
        folder.mkdir()
        event = {'timestamp': '2026-01-01T00:00:00+00:00', 'payload': {'rate_limits': {
            'secondary': {'used_percent': 41, 'window_minutes': 10080, 'resets_at': 2000000000}}}}
        (folder / 'session.jsonl').write_text(json.dumps(event) + '\n', encoding='utf-8')
        self.assertTrue(usage.refresh_codex_usage(self.conn, codex_dir=folder, force=True, provider={'name':'test-codex'}))
        value = json.loads(runtime.kv_get(self.conn, '*', 'usage-codex:test-codex'))
        self.assertEqual(value['saved'], event['timestamp'])
        self.assertEqual(value['sevenDay']['utilization'], .41)

    def test_isolated_dashboard_never_starts_metadata_cli(self):
        with mock.patch.object(usage.threading, 'Thread') as thread:
            usage.schedule_dashboard_refresh(self.conn, [{'name': 'test', 'limit': 'codex'}])
        thread.assert_not_called()

    def test_null_and_malformed_log_rows_do_not_hide_valid_reading(self):
        folder = self.base / 'sessions'
        folder.mkdir()
        event = {'payload': {'rate_limits': {'secondary': {'used_percent': 93, 'window_minutes': 10080, 'resets_at': 2000000000}}}}
        (folder / 'session.jsonl').write_text(json.dumps(event)+'\n'+json.dumps({'payload': {'rate_limits': None}})+'\n{"rate_limits":invalid}\n', encoding='utf-8')
        self.assertEqual(usage.get_codex_usage(folder)['seven_day']['pct'], 93)
