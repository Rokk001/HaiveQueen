import threading
import time
import unittest

from haivequeen import tray


class TrayRefreshTests(unittest.TestCase):
    def test_stale_status_disables_state_dependent_commands(self):
        entries = tray.menu('Running', True, True, stale=True)
        self.assertTrue(entries[0][2])
        self.assertTrue(all(not enabled for _, _, enabled in entries[1:]))

    def wait_finished(self, cache):
        deadline = time.monotonic() + 2
        while cache.snapshot()['refreshing'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertFalse(cache.snapshot()['refreshing'])

    def test_slow_probe_does_not_block_ui_or_spawn_more_workers(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def probe():
            calls.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return {'state': 'Running', 'web': True, 'work': False}

        cache = tray.StatusCache(probe)
        started = time.monotonic()
        cache.request()
        self.assertLess(time.monotonic() - started, .3)
        self.assertTrue(entered.wait(1))
        for _ in range(10):
            cache.request()
            self.assertTrue(cache.snapshot()['stale'])
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], threading.get_ident())
        release.set()
        self.wait_finished(cache)
        self.assertEqual(cache.snapshot()['state'], 'Running')
        self.assertFalse(cache.snapshot()['stale'])

    def test_failed_or_aged_status_is_marked_stale_and_then_recovers(self):
        clock = [1.0]
        fail = [False]

        def probe():
            if fail[0]:
                raise OSError('timeout')
            return {'state': 'Stopped', 'web': True, 'work': False}

        cache = tray.StatusCache(probe, clock=lambda: clock[0])
        cache.request()
        self.wait_finished(cache)
        self.assertFalse(cache.snapshot()['stale'])
        clock[0] = 20
        self.assertTrue(cache.snapshot()['stale'])
        fail[0] = True
        cache.request()
        self.wait_finished(cache)
        self.assertTrue(cache.snapshot()['stale'])
        self.assertTrue(cache.snapshot()['web'])
        self.assertIn('outdated', tray.status_tip(cache.snapshot()).lower())
        fail[0] = False
        cache.request()
        self.wait_finished(cache)
        self.assertFalse(cache.snapshot()['stale'])

    def test_close_does_not_wait_or_publish_a_late_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def probe():
            entered.set()
            release.wait(2)
            return {'state': 'Stopped', 'web': False, 'work': False}

        cache = tray.StatusCache(probe)
        cache.request()
        self.assertTrue(entered.wait(1))
        cache.close()
        release.set()
        self.wait_finished(cache)
        self.assertTrue(cache.snapshot()['stale'])
        self.assertEqual(cache.snapshot()['state'], 'Unknown')
        cache.request()
        self.assertFalse(cache.snapshot()['refreshing'])
