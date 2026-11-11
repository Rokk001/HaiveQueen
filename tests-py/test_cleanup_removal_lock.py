"""The cleanup locks only the worktree or branch it removes: starts of everything else run at once (L-1, L-2)."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest import mock

import isolation
from bzztest import REL, RoundCase
from haivequeen import bees, hives, loop, runtime, worktrees
import test_worktrees


# Run a function in a thread and remember its result or error.
def in_thread(function, *args):
    """Start `function(*args)` in a thread; the thread object carries `.result` and `.error`."""
    # Run and remember.
    def run():
        """Call the function."""
        try:
            thread.result = function(*args)
        except BaseException as exc:
            thread.error = exc
    thread = threading.Thread(target=run)
    thread.result = thread.error = None
    thread.start()
    return thread


# A temporary repository with an integrated worktree and a removal that can be held open.
class RemovalLockTests(unittest.TestCase):
    """While a removal takes long, no start waits except one for exactly that worktree."""

    def setUp(self):
        test_worktrees.CleanupTests.setUp(self)
        patch = mock.patch.object(worktrees, '_process_busy', return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    git = test_worktrees.CleanupTests.git

    # Branch locks belong to one repository.
    def test_branch_locks_belong_to_one_repository(self):
        """The same branch name in another repository is not busy."""
        other = self.repo / 'other-repo'
        with worktrees.removal_lock('branch', (self.repo, 'feature-x')) as mine:
            self.assertTrue(mine)
            self.assertTrue(worktrees.removal_busy('branch', (self.repo, 'feature-x')))
            self.assertFalse(worktrees.removal_busy('branch', (other, 'feature-x')))
            with worktrees.removal_lock('branch', (other, 'feature-x')) as second:
                self.assertTrue(second)
    # An early check is followed by fresh validation under the target lock.
    def test_worktree_eligibility_is_rechecked_under_the_lock(self):
        """Check once without blocking starts, then revalidate before removal."""
        seen = []
        real_reason, real_busy = worktrees._candidate_reason, worktrees._process_busy
        # Look at the lock during the checks.
        def reason(*args, **kwargs):
            """Record the holder."""
            seen.append(worktrees.removal_holder('worktree', self.linked))
            return real_reason(*args, **kwargs)
        def busy(path):
            """Record the holder."""
            seen.append(worktrees.removal_holder('worktree', path))
            return real_busy(path)
        with mock.patch.object(worktrees, '_candidate_reason', reason), mock.patch.object(worktrees, '_process_busy', busy):
            result = worktrees.prune(self.env)
        self.assertEqual(result['removed'], [os.path.normpath(str(self.linked))])
        self.assertTrue(seen)
        self.assertIn(None, seen)
        self.assertIn(os.getpid(), seen)

    def test_branch_eligibility_is_rechecked_under_the_lock(self):
        """Branch eligibility and its tip are rechecked before deletion."""
        env = mock.Mock(code_repo=self.repo, settings={})
        env.name = 'p'
        env.conn.execute.return_value.fetchone.return_value = None
        env.conn.execute.return_value.fetchall.return_value = []
        seen = []
        # Record the lock at each Git call before the deletion.
        def git(repo, *args):
            """Look at the lock."""
            if args[:2] != ('branch', '-D'):
                seen.append(worktrees.removal_holder('branch', (self.repo, 'feature-x')))
            return subprocess.CompletedProcess(args, 0, 'x' if args[0] == 'rev-parse' else '', '')
        result = {'deleted': [], 'kept': []}
        with mock.patch.object(worktrees, '_git', git), mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            worktrees._delete_plan_branches(env, 'plan', {'branch': 'feature-x', 'tip': 'a'}, 'origin', {}, {'feature-x': 'x'}, set(), set(), result)
        self.assertEqual(result['deleted'], ['local feature-x'])
        self.assertTrue(seen)
        self.assertIn(None, seen)
        self.assertIn(os.getpid(), seen)

    def test_agent_starting_after_initial_check_prevents_worktree_removal(self):
        real = worktrees._candidate_reason
        def reason(*args, **kwargs):
            result = real(*args, **kwargs)
            if worktrees.removal_holder('worktree', self.linked) is None:
                self.agents.append({'cwd': str(self.linked), 'status': 'starting', 'phase': 'bee'})
            return result
        with mock.patch.object(worktrees, '_candidate_reason', side_effect=reason):
            result = worktrees.prune(self.env)
        self.assertEqual(result['removed'], [])
        self.assertTrue(self.linked.exists())
        self.assertIn('used by active agent', str(result['kept']))

    def test_branch_tip_changed_after_initial_check_is_kept(self):
        env = mock.Mock(code_repo=self.repo, settings={})
        env.name = 'p'
        env.conn.execute.return_value.fetchone.return_value = None
        result = {'deleted': [], 'kept': []}
        def git(repo, *args):
            return subprocess.CompletedProcess(args, 0, 'new-tip' if args[0] == 'rev-parse' else '', '')
        with mock.patch.object(worktrees, '_git', side_effect=git) as calls, \
             mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            worktrees._delete_plan_branches(env, 'plan', {'branch': 'feature-x', 'tip': 'a'}, 'origin', {}, {'feature-x': 'old-tip'}, set(), set(), result)
        self.assertEqual(result['deleted'], [])
        self.assertIn('branch changed during cleanup', str(result['kept']))
        self.assertFalse(any(c.args[1:3] == ('branch', '-D') for c in calls.call_args_list))

    # Start a removal that stays inside the git call until released.
    def slow_removal(self):
        """Run prune in a thread whose worktree removal waits for `self.release`; returns once the removal runs."""
        self.entered, self.release = threading.Event(), threading.Event()
        real = worktrees._remove_with_links
        # Hold the removal open.
        def slow(*args):
            """Wait, then remove."""
            self.entered.set()
            self.release.wait(30)
            return real(*args)
        patch = mock.patch.object(worktrees, '_remove_with_links', slow)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.release.set)
        thread = in_thread(worktrees.prune, self.env)
        def finish():
            self.release.set()
            thread.join(30)
        self.addCleanup(finish)
        self.assertTrue(self.entered.wait(30))
        return thread

    # Other plans and projects start at once during a long removal.
    def test_other_starts_run_during_a_long_removal(self):
        """The scheduling lock is free while the removal runs; only the removed worktree is busy."""
        thread = self.slow_removal()
        with loop.hive_schedule_lock(timeout=1):
            pass
        self.assertTrue(worktrees.removal_busy('worktree', self.linked))
        self.assertFalse(worktrees.removal_busy('worktree', self.repo / 'other'))
        worktrees.wait_for_removal('worktree', self.repo / 'other', timeout=0.2)
        self.release.set()
        thread.join(30)
        self.assertIn(str(self.linked), thread.result['removed'])
        self.assertFalse(worktrees.removal_busy('worktree', self.linked))

    # A start for the removed worktree waits and then continues.
    def test_start_for_the_removed_worktree_waits_then_continues(self):
        """The wait ends with the removal; after that the worktree is gone."""
        thread = self.slow_removal()
        waiter = in_thread(worktrees.wait_for_removal, 'worktree', self.linked, 30)
        waiter.join(0.5)
        self.assertTrue(waiter.is_alive())
        self.release.set()
        waiter.join(30)
        thread.join(30)
        self.assertIsNone(waiter.error)
        self.assertFalse(self.linked.exists())

    # The wait is bounded and the message names the removal.
    def test_wait_times_out_naming_the_removal(self):
        """After the wait the start fails with a message that names the worktree."""
        self.slow_removal()
        with self.assertRaises(TimeoutError) as caught:
            worktrees.wait_for_removal('worktree', self.linked, timeout=0.3)
        self.assertIn('being removed', str(caught.exception))
        self.assertIn(str(self.linked), str(caught.exception))

    # An Agent start waits outside the scheduling lock and is refused with the removal named.
    def test_agent_start_waits_outside_the_scheduling_lock_and_is_refused_on_timeout(self):
        """start_process waits for the removal before it takes the scheduling lock."""
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE bee_worktrees(project, plan_path, package, cwd)')
        env = mock.Mock(conn=conn)
        env.name = 'p'
        seen = []
        # Time out on the busy removal.
        def waiting(kind, key, timeout=30.0):
            """Record that the scheduling lock is free during the wait, then time out."""
            with loop.hive_schedule_lock(timeout=1):
                pass
            seen.append((kind, str(key)))
            raise worktrees.RemovalTimeout('the %s %s is being removed by a cleanup (process id 1) that is still running' % (kind, key))
        self.slow_removal()
        with mock.patch.object(bees, 'plan_worktree', return_value=None), mock.patch.object(worktrees, 'wait_for_removal', waiting):
            result = bees.start_process(env, 'plan.md', 'P1', 'N', 'medium', 'task', [], [], [], cwd=str(self.linked))
        self.assertIs(result, False)
        self.assertEqual(seen, [('worktree', str(self.linked))])
        self.assertIn('is being removed', bees.LAST_REFUSAL)

    # A kept worktree is used as before.
    def test_kept_worktree_is_reused(self):
        """A worktree that the check keeps is not locked after the cleanup."""
        with mock.patch.object(worktrees, '_process_busy', return_value=True):
            result = worktrees.prune(self.env)
        self.assertEqual(result['removed'], [])
        self.assertTrue(self.linked.exists())
        self.assertFalse(worktrees.removal_busy('worktree', self.linked))

    # Checking blocks no start.
    def test_check_blocks_no_start(self):
        """While the safety check runs, neither the scheduling lock nor the worktree is locked."""
        entered, release = threading.Event(), threading.Event()
        # Block inside the check.
        def checking(path):
            """Wait while the check runs."""
            entered.set()
            release.wait(30)
            return True
        self.addCleanup(release.set)
        thread = in_thread(worktrees._prune_with_checker, self.env, checking, False)
        self.assertTrue(entered.wait(30))
        with loop.hive_schedule_lock(timeout=1):
            pass
        self.assertFalse(worktrees.removal_busy('worktree', self.linked))
        worktrees.wait_for_removal('worktree', self.linked, timeout=0.2)
        release.set()
        thread.join(30)

    # Two cleanups never remove the same worktree.
    def test_no_double_removal(self):
        """A second cleanup keeps the worktree that the first one removes."""
        first = self.slow_removal()
        second = worktrees.prune(self.env)
        self.assertEqual(second['removed'], [])
        self.assertIn({'path': str(self.linked), 'reason': 'another cleanup is removing it'}, second['kept'])
        self.release.set()
        first.join(30)
        self.assertEqual(first.result['removed'], [str(self.linked)])

    # The lock of a dead holder is taken over.
    def test_dead_holder_lock_is_taken_over(self):
        """A removal lock whose process has ended blocks nobody."""
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(30)
        path = worktrees._removal_lock_path('worktree', self.linked)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(child.pid), encoding='ascii')
        self.assertFalse(worktrees.removal_busy('worktree', self.linked))
        self.assertIn(str(self.linked), worktrees.prune(self.env)['removed'])

    def test_process_exit_releases_target_guard_and_stale_pid_is_recovered(self):
        """A separate process excludes starts until it dies without releasing its PID."""
        script = '''
import os, sys
from pathlib import Path
from haivequeen import platform, worktrees
platform.data_dir = lambda: Path(sys.argv[1])
with worktrees.removal_lock('worktree', sys.argv[2]) as mine:
    print('held' if mine else 'busy', flush=True)
    sys.stdin.readline()
    os._exit(0)
'''
        root = worktrees._removal_lock_path('worktree', self.linked).parent.parent
        child = subprocess.Popen([sys.executable, '-c', script, str(root), str(self.linked)],
                                 cwd=Path(__file__).resolve().parent.parent,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'held')
            with worktrees.removal_lock('worktree', self.linked) as mine:
                self.assertFalse(mine)
            child.stdin.write('\n')
            child.stdin.flush()
            child.wait(10)
            with worktrees.removal_lock('worktree', self.linked) as mine:
                self.assertTrue(mine)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(10)
            child.stdin.close()
            child.stdout.close()

    # The deletion of integrated branches locks one branch only.
    def test_branch_deletion_locks_only_its_branch(self):
        """A branch being deleted is busy; the scheduling lock and other branches are not; no double deletion."""
        entered, release = threading.Event(), threading.Event()
        real = worktrees._git
        # Hold the deletion of one branch open.
        def slow_git(repo, *args):
            """Wait inside `git branch -D`."""
            if args[:2] == ('branch', '-D'):
                entered.set()
                release.wait(30)
            return real(repo, *args)
        self.addCleanup(release.set)
        env = mock.Mock(code_repo=self.repo, settings={})
        env.name = 'p'
        env.conn.execute.return_value.fetchone.return_value = None
        env.conn.execute.return_value.fetchall.return_value = []
        row = {'branch': 'feature-x', 'tip': 'a'}
        # Delete the branch of one plan.
        def delete():
            """Delete feature-x (the branch exists in no repository: Git answers with a failure after the hold)."""
            result = {'deleted': [], 'kept': []}
            worktrees._delete_plan_branches(env, 'plan', row, 'origin', {}, {'feature-x': 'x'}, set(), set(), result)
            return result
        with mock.patch.object(worktrees, '_git', slow_git), mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            with mock.patch.object(worktrees, '_git', lambda repo, *a: slow_git(repo, *a) if a[:2] == ('branch', '-D') else subprocess.CompletedProcess(a, 0, 'x' if a[0] == 'rev-parse' else '', '')):
                thread = in_thread(delete)
                self.assertTrue(entered.wait(30))
                with loop.hive_schedule_lock(timeout=1):
                    pass
                self.assertTrue(worktrees.removal_busy('branch', (self.repo, 'feature-x')))
                self.assertFalse(worktrees.removal_busy('branch', (self.repo, 'other')))
                worktrees.wait_for_removal('branch', (self.repo, 'other'), timeout=0.2)
                second = delete()
                self.assertEqual(second['kept'][0]['reason'], 'another cleanup is deleting it')
                release.set()
                thread.join(30)
        self.assertFalse(worktrees.removal_busy('branch', (self.repo, 'feature-x')))

    # The timeout message names the process id of the holder.
    def test_timeout_message_names_the_holder_process_id(self):
        """The refusal tells which process holds the removal."""
        self.slow_removal()
        with self.assertRaises(TimeoutError) as caught:
            worktrees.wait_for_removal('worktree', self.linked, timeout=0.2)
        self.assertIn(str(os.getpid()), str(caught.exception))
        self.assertIn('process id', str(caught.exception))

    # The cleanup steps never take the scheduling lock.
    def test_cleanup_steps_do_not_take_the_scheduling_lock(self):
        """prune and delete_integrated_branches never ask for the scheduling lock."""
        with mock.patch.object(loop, 'hive_schedule_lock', side_effect=AssertionError('scheduling lock taken')):
            worktrees.prune(self.env)
            with mock.patch.object(worktrees, '_delete_branches_locked', return_value={}):
                worktrees.delete_integrated_branches(self.env)


if __name__ == '__main__':
    unittest.main()
