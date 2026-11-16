"""Offline updates survive other writers and stale recovery probes."""
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from test_f1_rework import StateBase
from haivequeen import db, offline


class OfflineConcurrency(StateBase):
    def test_mark_and_clear_lock_before_reading(self):
        original = offline.read
        def locked_read(conn):
            self.assertTrue(conn.in_transaction, "Offline mutation read outside write transaction")
            return original(conn)
        with mock.patch.object(offline, "read", side_effect=locked_read):
            offline.mark(self.conn, "a", "failure")
            offline.clear(self.conn, "a")

    def test_recheck_preserves_other_provider_updates(self):
        other = db.connect(Path(self.tmp.name) / "o.db")
        self.addCleanup(other.close)
        now = datetime(2026, 1, 2, 10)
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                offline.clear(self.conn, "a")
                offline.mark(self.conn, "a", "failure", now=now)
                offline.mark(self.conn, "removed", "old", now=now)
                def probe(provider):
                    self.assertFalse(self.conn.in_transaction)
                    offline.mark(other, "b", "new failure", now=now)
                    offline.clear(other, "removed")
                    return recovered
                with mock.patch.object(offline, "probe", side_effect=probe):
                    back = offline.recheck(self.conn, [{"name": "a"}], 5, now=now + timedelta(minutes=6))
                state = offline.read(self.conn)
                self.assertIn("b", state)
                self.assertNotIn("removed", state)
                self.assertEqual(back, ["a"] if recovered else [])
                self.assertEqual("a" in state, not recovered)

    def test_new_failure_invalidates_success_or_failure_probe_even_if_identical(self):
        other = db.connect(Path(self.tmp.name) / "o.db")
        self.addCleanup(other.close)
        now = datetime(2026, 1, 2, 10)
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                offline.clear(self.conn, "a")
                offline.mark(self.conn, "a", "same failure", now=now)
                newest = {}
                def probe(provider):
                    offline.mark(other, "a", "same failure", now=now)
                    newest.update(offline.read(other)["a"])
                    return recovered
                with mock.patch.object(offline, "probe", side_effect=probe):
                    back = offline.recheck(self.conn, [{"name": "a"}], 5, now=now + timedelta(minutes=6))
                self.assertEqual(back, [])
                self.assertEqual(offline.read(self.conn)["a"], newest)

    def test_explicit_clear_during_failed_probe_is_not_undone(self):
        now = datetime(2026, 1, 2, 10)
        offline.mark(self.conn, "a", "failure", now=now)
        def probe(provider):
            offline.clear(self.conn, "a")
            return False
        with mock.patch.object(offline, "probe", side_effect=probe):
            self.assertEqual(offline.recheck(self.conn, [{"name": "a"}], 5, now=now + timedelta(minutes=6)), [])
        self.assertNotIn("a", offline.read(self.conn))

    def test_unchanged_legacy_entry_can_recover(self):
        now = datetime(2026, 1, 2, 10)
        offline._write(self.conn, {"a": {"since": now.isoformat(), "checked": now.isoformat(), "reason": "old"}})
        with mock.patch.object(offline, "probe", return_value=True):
            self.assertEqual(offline.recheck(self.conn, [{"name": "a"}], 5, now=now + timedelta(minutes=6)), ["a"])
        self.assertEqual(offline.read(self.conn), {})
