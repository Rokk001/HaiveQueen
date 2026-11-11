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

    # A living holder without a heartbeat is abandoned too.
    def test_lock_with_expired_heartbeat_is_taken_over(self):
        """An expired heartbeat marks the lock abandoned even when the process lives."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            self._foreign_lock(path, 4242, "scheduling round", loop.HIVE_LOCK_EXPIRY + 5)
            with mock.patch.object(loop.osplat, "process_alive", lambda pid: True):
                with loop.hive_schedule_lock(timeout=1):
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pid"], os.getpid())

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


if __name__ == "__main__":
    unittest.main()
