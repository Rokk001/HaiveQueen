"""Starts and cleanup exclude each other only for matching targets."""
import threading
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, loop, worktrees


class CleanupStartRaces(RoundCase):
    def test_direct_start_waits_without_blocking_unrelated_start(self):
        for kind in ('worktree', 'branch'):
            with self.subTest(kind=kind):
                target = (kind, self.wt if kind == 'worktree' else (self.repo, 'feature'))
                other = (kind, self.base / 'other' if kind == 'worktree' else (self.repo, 'other'))
                waiting, finished = threading.Event(), threading.Event()
                errors = []
                real_wait = worktrees.wait_for_removal

                def wait(*args, **kwargs):
                    waiting.set()
                    return real_wait(*args, **kwargs)

                def started(*args):
                    with worktrees.removal_lock(*target) as mine:
                        self.assertFalse(mine)
                    return 'started'

                def start():
                    try:
                        self.assertEqual(bees.start_process(self.env(), REL, 'P1', 'Bee', 'medium', 'task', [], [], []), 'started')
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        finished.set()

                # No database access from the worker: target resolution and startup
                # are controlled, while both actual file locks run unchanged.
                env = self.env()
                with mock.patch.object(bees, 'start_removal_targets', return_value=[target]), \
                     mock.patch.object(bees, '_start_process_locked', side_effect=started), \
                     mock.patch.object(worktrees, 'wait_for_removal', side_effect=wait), \
                     mock.patch.object(self, 'env', return_value=env):
                    with worktrees.removal_lock(*target) as mine:
                        self.assertTrue(mine)
                        thread = threading.Thread(target=start)
                        thread.start()
                        self.assertTrue(waiting.wait(5))
                        self.assertFalse(finished.is_set())
                        with worktrees.start_with_removal_locks(lambda: [other], timeout=1):
                            pass
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])

    def test_partial_acquisition_is_released_on_contention_and_exception(self):
        first, second = self.wt, self.base / 'second'
        with worktrees.removal_lock('worktree', second):
            with worktrees.removal_locks([('worktree', first), ('worktree', second)]) as busy:
                self.assertEqual(busy, ('worktree', second))
                self.assertFalse(worktrees.removal_busy('worktree', first))
        with self.assertRaisesRegex(RuntimeError, 'start failed'):
            with worktrees.start_with_removal_locks(lambda: [('worktree', first)]):
                raise RuntimeError('start failed')
        self.assertFalse(worktrees.removal_busy('worktree', first))
        with loop.hive_schedule_lock(timeout=1):
            pass

    def test_targets_are_recomputed_when_cleanup_finishes(self):
        old, new = self.wt, self.base / 'new-target'
        resolutions = iter([[('worktree', old)], [('worktree', new)]])
        with worktrees.removal_lock('worktree', old):
            with mock.patch.object(worktrees, 'wait_for_removal') as wait:
                with worktrees.start_with_removal_locks(lambda: next(resolutions)):
                    self.assertTrue(worktrees.removal_busy('worktree', new))
                wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
