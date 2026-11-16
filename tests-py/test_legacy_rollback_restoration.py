import os
import threading
import unittest
from unittest import mock

from haivequeen import release_activation, version_layout, version_leases
from test_legacy_post_reservation_isolation import PostReservationIsolationTests


class RollbackRestorationTests(PostReservationIsolationTests):
    def test_restoration_and_queued_reservation_order(self):
        old = self.root / 'versions' / '1.0.0'
        isolated = self.root / 'versions' / 'isolated-old'
        real_reserve = version_leases._reserve_locked
        real_publish = version_layout._set_active_locked
        attempted = threading.Event()
        admitted = threading.Event()
        events = []
        caller_result = {}

        def reserve(root, version, *args, **kwargs):
            token = real_reserve(root, version, *args, **kwargs)
            if threading.current_thread() is threading.main_thread() and version == '1.0.0':
                events.append('activation reservation')
                old.rename(isolated)
            elif threading.current_thread() is not threading.main_thread():
                events.append('admission')
                admitted.set()
            return token

        def caller():
            attempted.set()
            try:
                caller_result['token'] = version_leases.reserve(self.root, '1.0.0')
            except Exception as exc:
                caller_result['error'] = repr(exc)

        thread = None
        def publish(root, version, *args, **kwargs):
            nonlocal thread
            if version == '1.0.0':
                events.append('rollback lock held')
                if not os.environ.get('HQ_TEST_NO_RESTORE'):
                    try:
                        isolated.rename(old)
                        events.append('restored')
                    except OSError as exc:
                        events.append(('restoration error', repr(exc)))
                        raise
                thread = threading.Thread(target=caller)
                thread.start()
                self.assertTrue(attempted.wait(5), 'caller did not start')
                self.assertFalse(admitted.wait(0.1), 'caller admitted under rollback lock')
                result = real_publish(root, version, *args, **kwargs)
                events.append('old pointer published')
                return result
            result = real_publish(root, version, *args, **kwargs)
            events.append('new pointer published')
            return result

        result = error = None
        try:
            with mock.patch.object(version_leases, '_reserve_locked', side_effect=reserve), \
                 mock.patch.object(version_layout, '_set_active_locked', side_effect=publish):
                try:
                    result = release_activation.activate(
                        self.root, '2.0.0', lambda: False,
                        after_rollback=lambda version: events.append(('callback', version)),
                        target_verifier=lambda: True)
                except Exception as exc:
                    error = repr(exc)
                if thread:
                    thread.join(5)
                    self.assertFalse(thread.is_alive(), 'caller did not finish')
            leases = version_leases._load(self.root)
            print('rollback restoration observation:', dict(events=events, result=result, error=error,
                  pointer=self.active(), caller=caller_result, reservations=leases,
                  old_exists=old.exists(), isolated_exists=isolated.exists()))
            self.assertIsNone(error)
            self.assertFalse(result)
            self.assertEqual(self.active(), '1.0.0')
            self.assertIn(('callback', '1.0.0'), events)
            self.assertEqual(events[:4], ['activation reservation', 'new pointer published',
                                          'rollback lock held', 'restored'])
            self.assertLess(events.index('restored'), events.index('old pointer published'))
            self.assertLess(events.index('old pointer published'), events.index('admission'))
            self.assertNotIn('error', caller_result)
            self.assertEqual(list(leases), [caller_result['token']])
            self.assertEqual(leases[caller_result['token']]['version'], '1.0.0')
            self.assertTrue(old.exists())
            self.assertFalse(isolated.exists())
        finally:
            if thread and thread.is_alive():
                thread.join(5)
            if 'token' in caller_result:
                version_leases.release(self.root, caller_result['token'])


if __name__ == '__main__':
    unittest.main()
