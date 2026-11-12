"""The worktree cleanup runs apart from the round and never holds it (C-1); a round start needing the worktree or branch being removed moves to a later round (C-2)."""
import threading
import time
import unittest
from unittest import mock

from bzztest import REL, RoundCase, db, runtime
from haivequeen import bees, hives, inbox, loop, worktrees
import test_worktrees


class LongCleanupTests(RoundCase):
    """A cleanup that takes very long (simulated) does not stop the rest."""

    def long_cleanup(self):
        """Replace the cleanup by one that runs until `self.release` is set."""
        self.calls, self.entered, self.release = [], threading.Event(), threading.Event()
        def slow(env):
            self.calls.append(env.name)
            self.entered.set()
            self.release.wait(30)
        patch = mock.patch.object(worktrees, 'cleanup', slow)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(worktrees.wait_cleanups, 10)
        self.addCleanup(self.release.set)

    def test_rounds_inbox_queen_start_and_agent_start_go_on(self):
        self.long_cleanup()
        self.plan('READY', [{'id': 'P1'}])
        question = db.add_question(self.conn, 'testproj', REL, 'P1', 'Which value?', '5')
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True)
        (box / 'a.toml').write_text('[[answer]]\nquestion_id = %d\ntext = "seven"\nby = "owner"\n' % question, encoding='utf-8')
        result = self.round()
        self.assertTrue(self.entered.wait(5))
        self.assertTrue(worktrees.cleanup_running('testproj'))
        self.assertFalse(self.release.is_set())
        self.assertEqual(result['projects']['testproj']['started'], 1)   # the Queen got her start and orders during the cleanup
        self.assertFalse((box / 'a.toml').exists())   # the inbox file was processed
        # A Queen's Agent start for another worktree goes through at once.
        with worktrees.start_with_removal_locks(lambda: [('worktree', self.base / 'unrelated')], timeout=1):
            pass
        # A second round ends too, and no second cleanup of the project starts.
        self.round()
        self.assertEqual(self.calls, ['testproj'])
        self.release.set()
        worktrees.wait_cleanups(10)
        self.assertFalse(worktrees.cleanup_running('testproj'))
        self.round()
        worktrees.wait_cleanups(10)
        self.assertEqual(self.calls, ['testproj', 'testproj'])

    def test_cleanup_error_is_logged_and_not_fatal(self):
        with mock.patch.object(worktrees, 'cleanup', side_effect=RuntimeError('cleanup broke')):
            self.round()
            worktrees.wait_cleanups(10)
        self.assertIn('cleanup broke', self.log())
        self.assertFalse(worktrees.cleanup_running('testproj'))
        self.round()   # the next round starts a new cleanup

    def test_cleanup_thread_start_failure_does_not_stop_round(self):
        self.plan('READY', [{'id': 'P1'}])
        original_start = threading.Thread.start
        def start(thread):
            if thread.name.startswith('worktree-cleanup-'):
                raise RuntimeError('thread unavailable')
            return original_start(thread)
        with mock.patch.object(threading.Thread, 'start', start):
            result = self.round()
        self.assertEqual(result['projects']['testproj']['started'], 1)
        self.assertIn('thread unavailable', self.log())

    def test_queen_own_start_waits_but_the_round_does_not(self):
        target = ('worktree', self.base / 'target')
        env = self.env()
        finished, errors = threading.Event(), []
        def start():
            try:
                self.assertEqual(bees.start_process(env, REL, 'P1', 'Bee', 'medium', 'task', [], [], []), 'started')
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()
        self.plan('READY', [{'id': 'P1'}])
        with mock.patch.object(bees, 'start_removal_targets', return_value=[target]), \
             mock.patch.object(bees, '_start_process_locked', return_value='started'), \
             mock.patch.object(worktrees, 'cleanup'):
            with worktrees.removal_lock(*target) as mine:
                self.assertTrue(mine)
                thread = threading.Thread(target=start)
                thread.start()
                time.sleep(0.5)
                self.assertFalse(finished.is_set())   # the Queen's own start waits as before
                self.assertEqual(self.round()['projects']['testproj']['started'], 1)   # the round does not wait for it
                self.assertFalse(finished.is_set())
            thread.join(10)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])


class DeferredStartTests(RoundCase):
    """A round start needing exactly the worktree or branch being removed waits for a later round."""

    def test_start_is_deferred_beyond_30_seconds_without_error_or_wait(self):
        for kind in ('worktree', 'branch'):
            with self.subTest(kind=kind):
                rel = 'docs/plans/other/OTHER-PLAN.md'
                self.plan('READY', [{'id': 'P1'}], rel=rel, branch='blocked', worktree=str(self.base / 'blocked'))
                self.plan('READY', [{'id': 'P1'}], branch='free', worktree=str(self.base / 'free'))
                env = self.env()
                target = ('worktree', self.base / 'blocked') if kind == 'worktree' else ('branch', (env.code_repo, 'blocked'))
                seen = []
                real_start = loop._do_start
                def start(ctx, action, **kwargs):
                    seen.append(action['plan'])
                    return real_start(ctx, action, **kwargs)
                holder = worktrees.removal_lock(*target)
                self.assertTrue(holder.__enter__())
                released = False
                try:
                    with mock.patch.object(worktrees, 'cleanup'), \
                         mock.patch.object(worktrees, 'wait_for_removal', side_effect=AssertionError('the round must not wait')), \
                         mock.patch.object(loop, '_do_start', side_effect=start), \
                         mock.patch.object(bees, 'worktree_conflict', return_value=''):
                        first = self.round()
                        # The removal runs for longer than the former 30 s: the round does not care.
                        real_monotonic = time.monotonic
                        with mock.patch.object(time, 'monotonic', lambda: real_monotonic() + 3600):
                            self.round()
                        self.assertEqual(first['projects']['testproj']['started'], 1)
                        self.assertEqual(seen, [REL])
                        self.assertNotIn('ROUND ERROR', self.log())
                        self.assertIn('being removed', runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + rel))
                        holder.__exit__(None, None, None)
                        released = True
                        self.assertEqual(self.round()['projects']['testproj']['started'], 1)
                    self.assertEqual(seen, [REL, rel])
                finally:
                    if not released:
                        holder.__exit__(None, None, None)
                for agent in runtime.list_agents(self.conn, 'testproj', 'all'):
                    runtime.upsert_agent(self.conn, 'testproj', agent['name'], {'status': 'done', 'pid': None, 'exit_code': 0})
                self.conn.execute('DELETE FROM hives')

    def test_round_wakes_when_the_removal_of_a_deferred_start_ends(self):
        rel = 'docs/plans/other/OTHER-PLAN.md'
        self.plan('READY', [{'id': 'P1'}], rel=rel, branch='blocked', worktree=str(self.base / 'blocked'))
        target = ('worktree', self.base / 'blocked')
        with worktrees.removal_lock(*target) as mine:
            self.assertTrue(mine)
            with mock.patch.object(worktrees, 'cleanup'):
                self.round()
            before = hives.wake_marker(self.conn, self.envs())
        self.assertNotEqual(before, hives.wake_marker(self.conn, self.envs()))


class BackgroundCleanupResultTests(unittest.TestCase):
    """The background cleanup removes and keeps what the cleanup in the round did."""

    def setUp(self):
        test_worktrees.CleanupTests.setUp(self)
        self.logged = []
        self.env.log = self.logged.append

    git = test_worktrees.CleanupTests.git

    def test_same_worktrees_removed_and_kept(self):
        kept = self.repo.parent / 'kept'
        self.git(self.repo, 'worktree', 'add', '-b', 'open-work', str(kept))
        (kept / 'new').write_text('data', encoding='utf-8')
        with mock.patch.object(worktrees, '_process_busy', return_value=False):
            self.assertTrue(worktrees.cleanup_in_background(self.env))
            worktrees.wait_cleanups(30)
        self.assertFalse(self.linked.exists())
        self.assertTrue(kept.exists())
        self.git(self.repo, 'show-ref', '--verify', 'refs/heads/integrated')
        self.assertIn('Worktree cleanup: ', self.logged[0])


if __name__ == '__main__':
    unittest.main()
