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

    def test_scheduler_waits_outside_lock_and_starts_in_same_call(self):
        for kind in ('worktree', 'branch'):
            with self.subTest(kind=kind):
                rel = 'docs/plans/other/OTHER-PLAN.md'
                self.plan('READY', [{'id': 'P1'}], rel=rel, branch='blocked', worktree=str(self.base / 'blocked'))
                self.plan('READY', [{'id': 'P1'}], branch='free', worktree=str(self.base / 'free'))
                env = self.env()
                target = ('worktree', self.base / 'blocked') if kind == 'worktree' else ('branch', (env.code_repo, 'blocked'))
                seen = []
                real_start = loop._do_start

                holder = worktrees.removal_lock(*target)
                self.assertTrue(holder.__enter__())
                released = False
                def wait(kind, key, timeout):
                    nonlocal released
                    self.assertEqual((kind, key), target)
                    # Another caller can start while this round waits.
                    with worktrees.start_with_removal_locks(lambda: [('worktree', self.base / 'unrelated')], timeout=1):
                        pass
                    holder.__exit__(None, None, None)
                    released = True
                def start(ctx, action, **kwargs):
                    seen.append(action['plan'])
                    branch, folder = bees.target_worktree(ctx['env'], action['plan'])
                    for item in [('worktree', folder), ('branch', (env.code_repo, branch))]:
                        with worktrees.removal_lock(*item) as mine:
                            self.assertFalse(mine)
                    return real_start(ctx, action, **kwargs)
                try:
                    with mock.patch.object(worktrees, 'cleanup'), \
                         mock.patch.object(worktrees, 'wait_for_removal', side_effect=wait) as waiting, \
                         mock.patch.object(loop, '_do_start', side_effect=start), \
                         mock.patch.object(bees, 'worktree_conflict', return_value=''):
                        result = self.round()
                    waiting.assert_called_once()
                    self.assertEqual(result['projects']['testproj']['started'], 2)
                finally:
                    if not released:
                        holder.__exit__(None, None, None)
                self.assertEqual(set(seen), {REL, rel})
                # Permit the second subtest to start a fresh Queen.
                for agent in runtime.list_agents(self.conn, 'testproj', 'all'):
                    runtime.upsert_agent(self.conn, 'testproj', agent['name'], {'status': 'done', 'pid': None, 'exit_code': 0})
                self.conn.execute('DELETE FROM hives')


if __name__ == '__main__':
    unittest.main()
