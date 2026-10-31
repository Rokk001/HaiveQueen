"""Regression tests for safe recovery of the Hive scheduling lock."""
import os
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
                self.assertEqual(path.read_text(encoding="ascii"), str(os.getpid()))
            self.assertFalse(path.exists())

    # Verify that an active same-process holder cannot be mistaken for an abandoned lock.
    def test_active_lock_is_not_reentered(self):
        """A nested lock cannot enter or remove the outer active lock."""
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"BZZ_DATA_DIR": directory}):
            path = Path(directory) / "hive-schedule.lock"
            with loop.hive_schedule_lock():
                with self.assertRaises(TimeoutError):
                    with loop.hive_schedule_lock(timeout=0.01):
                        self.fail("entered an already held lock")
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
