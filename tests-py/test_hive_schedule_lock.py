"""Regression tests for safe recovery of the Hive scheduling lock."""
import json
import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from haivequeen import loop


# Verify that a failed Windows lock-file release cannot wedge scheduling permanently.
class HiveScheduleLockTests(unittest.TestCase):
    """Exercise scheduling lock ownership and failed release recovery."""

    # Simulate the release sharing violation and prove the next attempt recovers.
    def test_release_failure_is_recovered_by_next_acquisition(self):
        """A failed release leaves the lock held until the next safe retry."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            original_unlink = Path.unlink
            # Raise a sharing violation for the scheduling lock only.
            def fail_once(target, *args, **kwargs):
                if target == path:
                    raise PermissionError("simulated sharing violation")
                return original_unlink(target, *args, **kwargs)
            with mock.patch.object(Path, "unlink", fail_once):
                with self.assertRaises(PermissionError):
                    with loop.hive_schedule_lock():
                        self.assertTrue(path.exists())
            self.assertTrue(path.exists())
            with loop.hive_schedule_lock(timeout=1):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getpid())
            self.assertFalse(path.exists())

    # Verify that an active same-process holder cannot be mistaken for an abandoned lock.
    def test_active_lock_is_not_reentered(self):
        """A nested lock cannot enter or remove the outer active lock."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            with loop.hive_schedule_lock(reason="scheduling round"):
                with self.assertRaisesRegex(TimeoutError, "scheduling round"):
                    with loop.hive_schedule_lock(timeout=0.01):
                        self.fail("entered an already held lock")
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    # Write a lock file as a foreign holder would and give it a heartbeat of the given age.
    def _foreign_lock(self, path, pid, reason, age):
        path.write_text(json.dumps({"pid": pid, "reason": reason}), encoding="utf-8")
        stamp = time.time() - age
        os.utime(str(path), (stamp, stamp))

    # The lock names the holder and keeps a heartbeat while it is held.
    def test_lock_holds_pid_reason_and_heartbeat(self):
        """The lock file holds the process id and the reason; the heartbeat is refreshed."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}),                 mock.patch.object(loop, "HIVE_LOCK_HEARTBEAT", 0.05):
            path = Path(directory) / "hive-schedule.lock"
            with loop.hive_schedule_lock(reason="cleaning a worktree"):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual((data["pid"], data["reason"]), (os.getpid(), "cleaning a worktree"))
                old = time.time() - 30
                os.utime(str(path), (old, old))
                time.sleep(0.5)
                self.assertLess(time.time() - path.stat().st_mtime, 10)

    # A holder that is gone makes the lock abandoned.
    def test_lock_of_dead_holder_is_taken_over(self):
        """A fresh heartbeat does not save the lock of a dead process."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            self._foreign_lock(path, 4242, "scheduling round", 0)
            with mock.patch.object(loop.osplat, "process_alive", lambda pid: pid != 4242):
                with loop.hive_schedule_lock(timeout=1):
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getpid())

    def test_live_holder_with_expired_heartbeat_is_not_taken_over(self):
        """A stalled heartbeat must not permit a second live scheduler."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            self._foreign_lock(path, 4242, "scheduling round", loop.HIVE_LOCK_EXPIRY + 5)
            with mock.patch.object(loop.osplat, "process_alive", lambda pid: True):
                with self.assertRaises(TimeoutError):
                    with loop.hive_schedule_lock(timeout=0.01):
                        self.fail("entered the live holder's scheduling section")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], 4242)

    def test_takeover_rechecks_holder_liveness(self):
        """A newly live holder must survive the second recovery read."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hive-schedule.lock'
            self._foreign_lock(path, 4242, 'round', loop.HIVE_LOCK_EXPIRY + 5)
            with mock.patch.object(loop.osplat, 'process_alive', side_effect=[False, True, True, True]):
                self.assertEqual(loop._try_hive_lock(path, 'new round'), (4242, 'round'))
            self.assertEqual(json.loads(path.read_text())['pid'], 4242)

    def test_os_guard_prevents_concurrent_recovery_before_metadata_exists(self):
        """A second contender cannot enter during lock metadata publication."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}):
            guard = os.open(str(Path(directory) / 'hive-schedule.lock.guard'), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(guard, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(TimeoutError):
                    with loop.hive_schedule_lock(timeout=0.01):
                        self.fail('entered while another contender owns the OS guard')
            finally:
                os.close(guard)
            with loop.hive_schedule_lock(timeout=0.1):
                pass

    def test_guard_is_held_through_body_and_released_on_exception(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}):
            path = Path(directory) / 'hive-schedule.lock'
            with self.assertRaisesRegex(ValueError, 'body failed'):
                with loop.hive_schedule_lock():
                    with self.assertRaises(TimeoutError):
                        loop._acquire_hive_guard(path, time.monotonic())
                    raise ValueError('body failed')
            with loop.hive_schedule_lock(timeout=0.1):
                pass

    def test_unreadable_stale_metadata_is_not_permission_to_take_over(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hive-schedule.lock'
            path.write_text('{unfinished')
            old = time.time() - loop.HIVE_LOCK_EXPIRY - 10
            os.utime(path, (old, old))
            self.assertEqual(loop._try_hive_lock(path, 'new round'), (-1, ''))
            self.assertEqual(path.read_text(), '{unfinished')

    # Replay of questions 608/612: a start waits for a busy lock, gives up and names the holder.
    def test_timeout_names_holder_and_reason(self):
        """A live holder with a live heartbeat is not taken over; the timeout reports its process id and reason."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            self._foreign_lock(path, 4242, "removing integrated worktrees", 1)
            with mock.patch.object(loop.osplat, "process_alive", lambda pid: True):
                with self.assertRaises(TimeoutError) as caught:
                    with loop.hive_schedule_lock(timeout=0.3):
                        self.fail("entered a lock with a live heartbeat")
            self.assertIn("4242", str(caught.exception))
            self.assertIn("removing integrated worktrees", str(caught.exception))
            self.assertTrue(path.exists())

    def test_zero_filled_lock_from_before_boot_is_archived_and_recovered(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}):
            path = Path(directory) / 'hive-schedule.lock'
            damaged = bytes(44)
            path.write_bytes(damaged)
            old = time.time() - 120
            os.utime(path, (old, old))
            with mock.patch.object(loop, 'system_boot_time', return_value=datetime.fromtimestamp(old + 30)):
                with loop.hive_schedule_lock(timeout=0, reason='regression'):
                    self.assertEqual(loop._read_hive_lock(path)[:2], (os.getpid(), 'regression'))
                with loop.hive_schedule_lock(timeout=0):
                    pass
            backups = list(Path(directory).glob('hive-schedule.lock.recovered-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), damaged)
            self.assertFalse(path.exists())

    def test_corrupt_metadata_without_reboot_proof_is_preserved(self):
        from datetime import datetime
        for boot in (None, datetime.fromtimestamp(time.time() - 300)):
            with self.subTest(boot=boot), tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}), mock.patch.object(loop, 'system_boot_time', return_value=boot):
                path = Path(directory) / 'hive-schedule.lock'
                path.write_bytes(bytes(44))
                with self.assertRaises(TimeoutError):
                    with loop.hive_schedule_lock(timeout=0):
                        self.fail('Corrupt metadata alone must not permit takeover')
                self.assertEqual(path.read_bytes(), bytes(44))
                self.assertEqual(list(Path(directory).glob('*.recovered-*')), [])

    def test_preboot_corruption_does_not_bypass_active_os_guard(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}):
            path = Path(directory) / 'hive-schedule.lock'
            path.write_bytes(bytes(44))
            old = time.time() - 120
            os.utime(path, (old, old))
            guard = loop._acquire_hive_guard(path, time.monotonic())
            try:
                with mock.patch.object(loop, 'system_boot_time', return_value=datetime.fromtimestamp(old + 30)):
                    with self.assertRaises(TimeoutError):
                        with loop.hive_schedule_lock(timeout=0):
                            self.fail('An active OS guard must prevent recovery')
                self.assertEqual(path.read_bytes(), bytes(44))
            finally:
                os.close(guard)

    def test_valid_live_holder_predating_boot_is_not_recovered(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'BZZ_DATA_DIR': directory}):
            path = Path(directory) / 'hive-schedule.lock'
            self._foreign_lock(path, 4242, 'live holder', 120)
            with mock.patch.object(loop, 'system_boot_time', return_value=datetime.fromtimestamp(time.time() - 30)), mock.patch.object(loop.osplat, 'process_alive', return_value=True):
                with self.assertRaises(TimeoutError):
                    with loop.hive_schedule_lock(timeout=0):
                        self.fail('Valid live ownership takes precedence over timestamps')
            self.assertEqual(loop._read_hive_lock(path)[0], 4242)

    def test_changed_corrupt_metadata_is_not_archived(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'hive-schedule.lock'
            path.write_bytes(bytes(44))
            old = time.time() - 120
            os.utime(path, (old, old))
            def concurrent_write(_path):
                path.write_bytes(b'new incomplete write')
                return None
            with mock.patch.object(loop, '_read_hive_lock', side_effect=concurrent_write):
                self.assertFalse(loop._recover_preboot_hive_lock(path, datetime.fromtimestamp(old + 30)))
            self.assertEqual(path.read_bytes(), b'new incomplete write')
            self.assertEqual(list(Path(directory).glob('*.recovered-*')), [])


if __name__ == "__main__":
    unittest.main()
