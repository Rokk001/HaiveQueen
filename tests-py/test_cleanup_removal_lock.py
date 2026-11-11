"""The cleanup locks only the worktree or branch it removes: starts of everything else run at once (L-1, L-2)."""
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest import mock

import isolation
from bzztest import REL, RoundCase
from haivequeen import bees, hives, loop, runtime, worktrees
from test_worktrees import CleanupTests


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

    setUp = CleanupTests.setUp
    git = CleanupTests.git

    # Branch locks and reservations belong to one repository.
    def test_branch_lock_and_reservation_belong_to_one_repository(self):
        """The same branch name in another repository is neither busy nor reserved."""
        other = self.repo / 'other-repo'
        with worktrees.removal_lock('branch', (self.repo, 'feature-x')) as mine:
            self.assertTrue(mine)
            self.assertTrue(worktrees.removal_busy('branch', (self.repo, 'feature-x')))
            self.assertFalse(worktrees.removal_busy('branch', (other, 'feature-x')))
            with worktrees.removal_lock('branch', (other, 'feature-x')) as second:
                self.assertTrue(second)
        with worktrees.start_reservation([('branch', (self.repo, 'feature-x'))]):
            self.assertTrue(worktrees.start_reserved('branch', (self.repo, 'feature-x')))
            self.assertFalse(worktrees.start_reserved('branch', (other, 'feature-x')))

    # The slow eligibility checks run before the per-item lock.
    def test_worktree_eligibility_is_checked_before_the_lock(self):
        """While the checks of a worktree run, its removal lock is not held."""
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
        self.assertEqual(seen, [None] * len(seen))

    def test_branch_eligibility_is_checked_before_the_lock(self):
        """The Git checks of a branch run without its removal lock."""
        env = mock.Mock(code_repo=self.repo, settings={})
        env.name = 'p'
        env.conn.execute.return_value.fetchone.return_value = None
        seen = []
        # Record the lock at each Git call before the deletion.
        def git(repo, *args):
            """Look at the lock."""
            if args[:2] != ('branch', '-D'):
                seen.append(worktrees.removal_holder('branch', (self.repo, 'feature-x')))
            return subprocess.CompletedProcess(args, 0, '', '')
        result = {'deleted': [], 'kept': []}
        with mock.patch.object(worktrees, '_git', git), mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            worktrees._delete_plan_branches(env, 'plan', {'branch': 'feature-x', 'tip': 'a'}, 'origin', {}, {'feature-x': 'x'}, set(), set(), result)
        self.assertEqual(result['deleted'], ['local feature-x'])
        self.assertTrue(seen)
        self.assertEqual(seen, [None] * len(seen))

    # Without a database file the round does not run the cleanup.
    def test_no_database_file_skips_the_cleanup(self):
        """An in-memory database: nothing runs in the round and no thread starts."""
        env = mock.Mock()
        env.conn.execute.return_value = [(0, 'main', '')]
        with mock.patch.object(worktrees, 'cleanup') as cleanup:
            self.assertIsNone(worktrees.cleanup_beside_round([env]))
        cleanup.assert_not_called()
        env.log.assert_not_called()

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
        row = {'branch': 'feature-x', 'tip': 'a'}
        # Delete the branch of one plan.
        def delete():
            """Delete feature-x (the branch exists in no repository: Git answers with a failure after the hold)."""
            result = {'deleted': [], 'kept': []}
            worktrees._delete_plan_branches(env, 'plan', row, 'origin', {}, {'feature-x': 'x'}, set(), set(), result)
            return result
        with mock.patch.object(worktrees, '_git', slow_git), mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            with mock.patch.object(worktrees, '_git', lambda repo, *a: slow_git(repo, *a) if a[:2] == ('branch', '-D') else subprocess.CompletedProcess(a, 0, '', '')):
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

    # A start that reserved the worktree before the cleanup took its lock keeps it.
    def test_cleanup_keeps_a_worktree_a_start_reserved(self):
        """The start passed the check, the cleanup re-checks under its lock and keeps the worktree."""
        with worktrees.start_reservation([('worktree', self.linked)]) as ok:
            self.assertTrue(ok)
            self.assertTrue(worktrees.start_reserved('worktree', self.linked))
            result = worktrees.prune(self.env)
        self.assertEqual(result['removed'], [])
        self.assertTrue(self.linked.exists())
        self.assertIn(str(self.linked), [k['path'] for k in result['kept']])
        self.assertFalse(worktrees.start_reserved('worktree', self.linked))
        self.assertIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # A start that meets a running removal gives its reservation back while it waits.
    def test_waiting_start_leaves_no_reservation(self):
        """On timeout nothing stays reserved; a start without wait gets False."""
        self.slow_removal()
        with self.assertRaises(TimeoutError):
            with worktrees.start_reservation([('worktree', self.linked)], timeout=0.2):
                pass
        self.assertFalse(worktrees.start_reserved('worktree', self.linked))
        with worktrees.start_reservation([('worktree', self.linked)], wait=False) as ok:
            self.assertFalse(ok)
        self.assertFalse(worktrees.start_reserved('worktree', self.linked))

    # The reservation of a dead start blocks nothing.
    def test_reservation_of_a_dead_process_is_ignored(self):
        """A start that died leaves a file; the cleanup is not blocked by it."""
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(30)
        folder = worktrees._reservation_dir('worktree', self.linked)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ('%d-x' % child.pid)).write_text('', encoding='ascii')
        self.assertFalse(worktrees.start_reserved('worktree', self.linked))

    # An Agent start holds the reservation while it starts.
    def test_agent_start_holds_the_reservation(self):
        """From the check until the start ends the worktree is reserved."""
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE bee_worktrees(project, plan_path, package, cwd)')
        env = mock.Mock(conn=conn)
        env.name = 'p'
        seen = []
        # Look from inside the start.
        def locked(**kwargs):
            """Record the reservation, then refuse the start."""
            seen.append(worktrees.start_reserved('worktree', self.linked))
            raise RuntimeError('stop')
        with mock.patch.object(bees, 'plan_worktree', return_value=None), mock.patch.object(loop, 'hive_schedule_lock', locked):
            with self.assertRaises(RuntimeError):
                bees.start_process(env, 'plan.md', 'P1', 'N', 'medium', 'task', [], [], [], cwd=str(self.linked))
        self.assertEqual(seen, [True])
        self.assertFalse(worktrees.start_reserved('worktree', self.linked))

    # The deletion of a branch keeps a branch that a start reserved.
    def test_branch_deletion_keeps_a_reserved_branch(self):
        """A reserved branch is not deleted."""
        env = mock.Mock(code_repo=self.repo, settings={})
        env.name = 'p'
        env.conn.execute.return_value.fetchone.return_value = None
        result = {'deleted': [], 'kept': []}
        with mock.patch.object(worktrees, '_git', lambda repo, *a: subprocess.CompletedProcess(a, 0, '', '')), mock.patch.object(worktrees, '_open_plan_branches', return_value=set()):
            with worktrees.start_reservation([('branch', (self.repo, 'feature-x'))]):
                worktrees._delete_plan_branches(env, 'plan', {'branch': 'feature-x', 'tip': 'a'}, 'origin', {}, {'feature-x': 'x'}, set(), set(), result)
        self.assertEqual(result['deleted'], [])
        self.assertEqual(result['kept'][0]['reason'], 'a start is using it')

    # The cleanup steps never take the scheduling lock.
    def test_cleanup_steps_do_not_take_the_scheduling_lock(self):
        """prune and delete_integrated_branches never ask for the scheduling lock."""
        with mock.patch.object(loop, 'hive_schedule_lock', side_effect=AssertionError('scheduling lock taken')):
            worktrees.prune(self.env)
            with mock.patch.object(worktrees, '_delete_branches_locked', return_value={}):
                worktrees.delete_integrated_branches(self.env)


# The round never waits for the cleanup.
class RoundCleanupTests(RoundCase):
    """A round neither waits for a cleanup nor starts into the worktree being removed."""

    # The cleanup runs beside the round.
    def test_round_does_not_wait_for_the_cleanup(self):
        """The round returns while the cleanup of a project still runs."""
        entered, release = threading.Event(), threading.Event()
        # Block the cleanup.
        def slow(env):
            """Wait until released."""
            entered.set()
            release.wait(30)
        self.addCleanup(release.set)
        self.plan("READY", [{"id": "P1", "title": "First"}], branch="feature", worktree=str(self.wt))
        with mock.patch.object(worktrees, 'cleanup', slow):
            self.round()
            self.assertTrue(entered.wait(30))
            release.set()
            worktrees._background['thread'].join(30)

    # A start for exactly the branch being removed goes to the next round.
    def test_start_for_the_removed_branch_is_left_to_the_next_round(self):
        """With the branch of the plan being removed no Queen starts; the next round starts her."""
        self.plan("READY", [{"id": "P1", "title": "First"}], branch="feature", worktree=str(self.wt))
        branch = bees.plan_worktree(self.env(), REL)[0]
        with mock.patch.object(worktrees, 'cleanup_beside_round'), worktrees.removal_lock('branch', (self.env().code_repo, branch)) as mine:
            self.assertTrue(mine)
            self.round()
            self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "waiting")
            self.assertFalse(any(a["phase"] == "queen" for a in runtime.list_agents(self.conn, "testproj", "all")))
            self.assertIn("being removed by a cleanup", self.log())
        with mock.patch.object(worktrees, 'cleanup_beside_round'), mock.patch.object(bees, 'worktree_conflict', return_value=''), mock.patch.object(bees, 'prepare_queen_worktree', return_value=str(self.wt)), mock.patch('haivequeen.loop._do_start', return_value=1) as start:
            self.round()
        self.assertTrue(start.called)

    # A Queen start holds the reservation until her start ends.
    def test_queen_start_reserves_the_branch(self):
        """Between the check and the start the plan branch is reserved, so a cleanup keeps it; afterwards it is free."""
        self.plan("READY", [{"id": "P1", "title": "First"}], branch="feature", worktree=str(self.wt))
        branch = bees.plan_worktree(self.env(), REL)[0]
        seen = []
        # Look from inside the start.
        def starting(ctx, action, forced_pick=None):
            """Record the reservation."""
            seen.append(worktrees.start_reserved('branch', (self.env().code_repo, branch)))
            return 1
        with mock.patch.object(worktrees, 'cleanup_beside_round'), mock.patch.object(bees, 'worktree_conflict', return_value=''), mock.patch.object(bees, 'prepare_queen_worktree', return_value=str(self.wt)), mock.patch('haivequeen.loop._do_start', starting):
            self.round()
        self.assertEqual(seen, [True])
        self.assertFalse(worktrees.start_reserved('branch', (self.env().code_repo, branch)))


if __name__ == '__main__':
    unittest.main()
