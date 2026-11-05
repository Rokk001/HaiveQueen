import unittest
from datetime import datetime, timedelta
from haivequeen.update_service import UpdateService


class Drain:
    def __init__(self, events):
        self.events = events
    def begin(self):
        self.events.append("barrier")
    def wait(self):
        self.events.append("drained")
    def end(self):
        self.events.append("release")


class UpdateServiceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.release = {"version": "2.0.1"}
        self.service = UpdateService(lambda: self.release, Drain(self.events),
                                     lambda r: self.events.append(("install", r)),
                                     lambda: self.events.append("restart"))
        self.now = datetime(2026, 1, 1)

    def test_scheduled_and_manual_checks(self):
        self.assertTrue(self.service.check_due(self.now))
        self.assertFalse(self.service.check_due(self.now + timedelta(hours=23)))
        self.assertEqual(self.service.check_now(self.now + timedelta(hours=23)), self.release)
        self.assertFalse(self.service.check_due(self.now + timedelta(hours=24)))
        self.assertTrue(self.service.check_due(self.now + timedelta(hours=47)))

    def test_failed_check_is_throttled_and_manual_can_retry(self):
        def fail():
            raise OSError("offline")
        self.service.check = fail
        with self.assertRaises(OSError):
            self.service.check_due(self.now)
        self.assertFalse(self.service.check_due(self.now + timedelta(hours=1)))
        self.service.check = lambda: self.release
        self.assertEqual(self.service.check_now(self.now + timedelta(hours=1)), self.release)

    def test_install_requires_offered_release_and_drains_before_install(self):
        with self.assertRaises(ValueError):
            self.service.install_confirmed(self.release)
        self.service.check_now(self.now)
        self.service.install_confirmed(self.release)
        self.assertEqual(self.events, ["barrier", "drained", ("install", self.release), "restart", "release"])
        self.assertIsNone(self.service.available)

    def test_failure_releases_barrier_and_does_not_restart(self):
        self.service.check_now(self.now)
        def fail(_):
            raise OSError("install failed")
        self.service.install = fail
        with self.assertRaises(OSError):
            self.service.install_confirmed(self.release)
        self.assertEqual(self.events, ["barrier", "drained", "release"])
        self.assertEqual(self.service.available, self.release)

    def test_invalid_interval(self):
        with self.assertRaises(ValueError):
            UpdateService(None, None, None, None, timedelta(0))
