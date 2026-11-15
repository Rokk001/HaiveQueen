import json
import os
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from haivequeen import scheduler, usage


class QuotaStartFreshness(unittest.TestCase):
    def reserve(self, snapshot, source='claude'):
        provider = {'name': 'test', 'limit': source}
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(usage.runtime, 'kv_get', return_value=json.dumps(snapshot)):
            return scheduler.provider_reserve(provider, conn=object())

    def snapshot(self, age=0):
        return {'saved': datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat(),
                'sevenDay': {'utilization': .2, 'resetsAt': int(time.time()) + 36000}}

    def test_missing_quota_blocks_new_work(self):
        for source in ('claude', 'codex'):
            with self.subTest(source=source):
                self.assertEqual(self.reserve({}, source), 0)

    def test_old_reading_blocks_until_refreshed(self):
        for source in ('claude', 'codex'):
            with self.subTest(source=source):
                self.assertEqual(self.reserve(self.snapshot(usage.USAGE_INTERVAL + 1), source), 0)
                self.assertGreater(self.reserve(self.snapshot(), source), 0)

    def test_failed_refresh_does_not_authorize_from_history(self):
        snapshot = self.snapshot()
        snapshot['refresh_error'] = 'unavailable'
        self.assertEqual(self.reserve(snapshot), 0)

    def test_unlimited_provider_still_starts(self):
        self.assertIsNone(scheduler.provider_reserve({'name': 'local', 'limit': 'none'}))

    def test_expired_window_requires_a_new_observation(self):
        snapshot = self.snapshot()
        snapshot['sevenDay']['resetsAt'] = int(time.time()) - 1
        for source in ('claude', 'codex'):
            self.assertEqual(self.reserve(snapshot, source), 0)

    def test_verified_five_hour_only_account_can_start(self):
        snapshot = self.snapshot()
        snapshot['fiveHour'] = snapshot.pop('sevenDay')
        self.assertGreater(self.reserve(snapshot, 'codex'), 0)
