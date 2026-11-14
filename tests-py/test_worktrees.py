"""Safe worktree cleanup tests using only temporary Git repositories."""
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import isolation
from haivequeen import worktrees


# Verify worktree Git commands do not create a visible console on Windows.
class GitWindowTests(unittest.TestCase):
    """Check the flags used for Git during automatic cleanup."""

    # Cleanup must hide each Git process on Windows.
    @unittest.skipUnless(sys.platform == 'win32', 'Windows only')
    def test_git_uses_no_window_flag(self):
        """Git subprocesses use CREATE_NO_WINDOW."""
        with mock.patch('haivequeen.worktrees.subprocess.run') as run:
            worktrees._git('repo', 'status')
        self.assertEqual(run.call_args.kwargs.get('creationflags'), subprocess.CREATE_NO_WINDOW)

# Check process working-directory protection.
class ProcessDirectoryTests(unittest.TestCase):
    """Check process working-directory protection."""

    # A separate process using a directory prevents its cleanup.
    def test_other_process_directory_is_busy(self):
        """A separate process using a directory prevents its cleanup."""
        import sys
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     cwd=folder, stdin=subprocess.DEVNULL)
            try:
                self.assertTrue(worktrees._process_busy(folder))
            finally:
                child.terminate()
                child.wait(timeout=5)

    # The cleanup takes no scheduling lock; only the one worktree it removes is locked (test_cleanup_removal_lock.py).
    def test_cleanup_does_not_hold_agent_scheduling_lock(self):
        """No scheduling lock is held while a candidate is checked or removed."""
        from haivequeen import loop
        # Assert the scheduler is not locked while cleanup runs.
        def check(*args):
            """Assert the scheduler is not locked while cleanup runs."""
            with loop.hive_schedule_lock(timeout=1):
                pass
            return {}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch('haivequeen.platform.data_dir', return_value=Path(folder)), \
             mock.patch.object(worktrees, '_prune_with_checker', side_effect=check):
            worktrees.prune(None)

    # A process using a directory prevents its cleanup.
    def test_current_process_directory_is_busy(self):
        """A process using a directory prevents its cleanup."""
        self.assertTrue(worktrees._process_busy(str(Path.cwd())))


# Exercise cleanup against a temporary repository and remote.
class CleanupTests(unittest.TestCase):
    """Exercise cleanup against a temporary repository and remote."""

    # Create a linked branch with explicit integrated queue evidence.
    def setUp(self):
        """Create a linked branch and record its exact completed tip."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        data_dir = mock.patch('haivequeen.platform.data_dir', return_value=root)
        data_dir.start()
        self.addCleanup(data_dir.stop)
        self.origin, self.repo, self.linked = root / 'origin.git', root / 'repo', root / 'linked'
        subprocess.run(['git', 'init', '--bare', str(self.origin)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.origin), str(self.repo)], check=True, capture_output=True)
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'file').write_text('test', encoding='utf-8')
        (self.repo / '.gitignore').write_text('ignored/\n', encoding='utf-8')
        self.git(self.repo, 'add', 'file', '.gitignore')
        self.git(self.repo, 'commit', '-m', 'Initial')
        self.git(self.repo, 'branch', '-M', 'main')
        self.git(self.repo, 'push', 'origin', 'main')
        self.git(self.repo, 'worktree', 'add', '-b', 'integrated', str(self.linked))
        self.env = SimpleNamespace(code_repo=self.repo, workspace=self.repo, settings={'integrationBranch': 'main'}, name='test', agents=lambda scope: [], conn=None)
        self.queue = [{'id': 1, 'branch': 'integrated', 'status': 'integrated',
                       'tip': self.git(self.linked, 'rev-parse', 'HEAD').stdout.strip()}]
        patch = mock.patch('haivequeen.worktrees.db.queue_rows', side_effect=lambda *args: self.queue)
        patch.start()
        self.addCleanup(patch.stop)
        self.agents = []
        patch = mock.patch('haivequeen.worktrees.runtime.list_agents', side_effect=lambda *args: self.agents)
        patch.start()
        self.addCleanup(patch.stop)

    # Run Git in one of the temporary folders.
    def git(self, folder, *args):
        """Run Git in one of the temporary folders."""
        return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True)

    # An integrated clean worktree is removed but its branch remains.
    def test_integrated_clean_worktree_is_removed_without_its_branch(self):
        """An integrated clean worktree is removed but its branch remains."""
        result = worktrees.prune(self.env)
        self.assertIn(str(self.linked), result['removed'])
        self.assertFalse(self.linked.exists())
        self.git(self.repo, 'show-ref', '--verify', 'refs/heads/integrated')

    # Untracked and ignored files protect data by default.
    def test_untracked_and_ignored_files_are_kept(self):
        """Untracked and ignored files protect data by default."""
        (self.linked / 'new').write_text('data', encoding='utf-8')
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])
        (self.linked / 'new').unlink()
        (self.linked / 'ignored').mkdir()
        (self.linked / 'ignored' / 'data').write_text('data', encoding='utf-8')
        self.assertNotIn(str(self.linked), worktrees.prune(self.env, dry=True)['removed'])

    # Locks and configured protected paths prevent removal.
    def test_locked_and_protected_worktrees_are_kept(self):
        """Locks and configured protected paths prevent removal."""
        self.git(self.repo, 'worktree', 'lock', str(self.linked))
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])
        self.git(self.repo, 'worktree', 'unlock', str(self.linked))

    # Dry runs list candidates without removing them.
    def test_dry_run_keeps_the_folder(self):
        """Dry runs list candidates without removing them."""
        result = worktrees.prune(self.env, dry=True)
        self.assertIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A commit absent from the fetched integration branch is never removed.
    def test_unintegrated_commit_is_kept(self):
        """A commit absent from the fetched integration branch is never removed."""
        (self.linked / 'new').write_text('unmerged', encoding='utf-8')
        self.git(self.linked, 'add', 'new')
        self.git(self.linked, 'commit', '-m', 'Unmerged')
        self.queue[0]['tip'] = self.git(self.linked, 'rev-parse', 'HEAD').stdout.strip()
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])
        self.assertTrue(self.linked.exists())

    # Every outstanding integration queue state protects its branch.
    def test_queue_states_block_cleanup(self):
        """Every outstanding integration queue state protects its branch."""
        for state in ('ready', 'in_run', 'blocked'):
            with self.subTest(state=state), mock.patch('haivequeen.worktrees.db.queue_rows', return_value=[{'branch': 'integrated', 'status': state}]):
                self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # A Queen or Bee using the worktree protects it.
    def test_running_agent_blocks_cleanup(self):
        """A Queen or Bee using the worktree protects it."""
        for phase in ('queen', 'bee'):
            with self.subTest(phase=phase):
                self.agents = [{'status': 'running', 'phase': phase, 'cwd': str(self.linked), 'name': 'agent'}]
                self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # A detached HEAD cannot prove which completed branch owns the worktree.
    def test_detached_integrated_worktree_is_kept(self):
        """A detached HEAD stays even when its commit belongs to an integrated branch."""
        self.git(self.linked, 'checkout', '--detach')
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # The configured workspace remains even when it is a linked worktree.
    def test_workspace_is_protected(self):
        """The configured workspace remains even when it is a linked worktree."""
        self.env.workspace = self.linked
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # Only explicitly configured ignored output may be discarded.
    def test_allowed_ignored_output_can_be_removed(self):
        """Only explicitly configured ignored output may be discarded."""
        (self.linked / 'ignored').mkdir()
        (self.linked / 'ignored' / 'output').write_text('build', encoding='utf-8')
        self.env.settings['ignoredBuildOutput'] = ['ignored/']
        self.assertIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # A separate process with a working directory inside the worktree protects it.
    def test_process_working_directory_blocks_cleanup(self):
        """A separate process with a working directory inside the worktree protects it."""
        import sys
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], cwd=self.linked, stdin=subprocess.PIPE)
        try:
            self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])
        finally:
            child.stdin.close()
            child.wait(timeout=10)

    # Removing an allowed ignored link leaves its external target complete.
    def test_ignored_link_target_survives_cleanup(self):
        """Removing an allowed ignored link leaves its external target complete."""
        target = self.repo.parent / 'target'
        target.mkdir()
        data = target / 'data'
        data.write_text('keep', encoding='utf-8')
        try:
            (self.linked / 'ignored').symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Creating symlinks is unavailable: %s' % exc)
        self.env.settings['ignoredBuildOutput'] = ['ignored/']
        # Git's directory-only pattern does not ignore a POSIX symlink.
        (self.repo / '.git' / 'info' / 'exclude').write_text('ignored\n', encoding='utf-8')
        self.assertIn(str(self.linked), worktrees.prune(self.env)['removed'])
        self.assertEqual(data.read_text(encoding='utf-8'), 'keep')

    # Git inventory identifies the linked branch and its commit.
    def test_inventory_contains_linked_branch(self):
        """Git inventory identifies the linked branch and its commit."""
        records = worktrees.list_worktrees(self.repo)
        entry = next(record for record in records if Path(record['worktree']) == self.linked)
        self.assertEqual(entry['branch'], 'integrated')
        self.assertEqual(entry['HEAD'], self.git(self.linked, 'rev-parse', 'HEAD').stdout.strip())

    # The repository checks accept a clean integrated worktree.
    def test_candidate_checks_clean_integrated_branch(self):
        """The repository checks accept a clean integrated worktree."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.assertEqual(worktrees._candidate_reason(self.env, entry, False, self.queue), '')

    # A fresh branch at the integration tip is not evidence of finished work.
    def test_fresh_branch_without_completion_evidence_is_kept(self):
        """Keep a clean new branch whose base is already integrated."""
        self.queue = []
        result = worktrees._prune_with_checker(self.env, lambda path: False, True)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # Without any finished branch nothing can be removed, so the cleanup must not touch the network.
    def test_no_network_access_when_nothing_can_be_removed(self):
        """No integrated queue row: no fetch and no ls-remote, and the worktree is still reported as kept."""
        self.queue = []
        with mock.patch.object(worktrees, '_git', wraps=worktrees._git) as git:
            result = worktrees._prune_with_checker(self.env, lambda path: False, True)
        commands = [call.args[1] for call in git.call_args_list if len(call.args) > 1]
        self.assertNotIn('fetch', commands)
        self.assertNotIn('ls-remote', commands)
        self.assertTrue(any(item['path'] == str(self.linked) for item in result['kept']))

    # Worktrees without integrated queue evidence are rejected without asking Git for their branch.
    def test_no_git_branch_lookup_without_queue_evidence(self):
        """A worktree with no integrated queue row is kept before any `symbolic-ref` call; an integrated one still gets its branch verified."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.queue = []
        with mock.patch.object(worktrees, '_git', wraps=worktrees._git) as git:
            self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))
        self.assertNotIn('symbolic-ref', [call.args[1] for call in git.call_args_list if len(call.args) > 1])
        self.queue = [{'id': 1, 'branch': 'integrated', 'status': 'integrated', 'tip': self.git(self.linked, 'rev-parse', 'HEAD').stdout.strip()}]
        with mock.patch.object(worktrees, '_git', wraps=worktrees._git) as git:
            worktrees._candidate_reason(self.env, entry, False, self.queue)
        self.assertIn('symbolic-ref', [call.args[1] for call in git.call_args_list if len(call.args) > 1])

    # A finished, integrated branch still needs the verified remote state.
    def test_network_is_used_when_a_branch_is_integrated(self):
        """An integrated queue row keeps the fetch and ls-remote verification before any removal."""
        with mock.patch.object(worktrees, '_git', wraps=worktrees._git) as git:
            worktrees._prune_with_checker(self.env, lambda path: False, True)
        commands = [call.args[1] for call in git.call_args_list if len(call.args) > 1]
        self.assertIn('fetch', commands)
        self.assertIn('ls-remote', commands)

    # The queue alone decides whether a worktree is a removal candidate.
    def test_may_be_removable_follows_the_newest_queue_row(self):
        """Only a newest 'integrated' row without open rows makes a worktree a candidate."""
        record = {'branch': 'b'}
        row = lambda id, status: {'id': id, 'branch': 'b', 'status': status}
        self.assertTrue(worktrees._may_be_removable(record, [row(1, 'integrated')]))
        self.assertFalse(worktrees._may_be_removable(record, []))
        self.assertFalse(worktrees._may_be_removable({}, [row(1, 'integrated')]))
        self.assertFalse(worktrees._may_be_removable(record, [row(1, 'integrated'), row(2, 'withdrawn')]))
        for state in ('ready', 'in_run', 'blocked'):
            self.assertFalse(worktrees._may_be_removable(record, [row(1, 'integrated'), row(2, state)]))

    # Completion of another branch cannot authorize this branch's removal.
    def test_other_branch_completion_is_not_evidence(self):
        """Require an exact branch match even when another completed tip is identical."""
        self.queue[0]['branch'] = 'other'
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))

    # A branch switch invalidates the inventory's completion evidence at the same HEAD.
    def test_changed_branch_invalidates_inventory_completion_evidence(self):
        """Keep a fresh branch that replaced the inventoried integrated branch."""
        records = worktrees.list_worktrees(self.repo)
        self.git(self.linked, 'switch', '-c', 'fresh')
        result = worktrees._prune_candidates(self.env, records, self.queue, lambda path: False, True)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A superseding queue row invalidates older completion evidence.
    def test_latest_branch_row_must_be_integrated(self):
        """Keep branches with newer withdrawn, ready, running or blocked work."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        for state in ('withdrawn', 'ready', 'in_run', 'blocked'):
            with self.subTest(state=state):
                newer = dict(self.queue[0], id=2, status=state)
                self.assertTrue(worktrees._candidate_reason(self.env, entry, False, [newer, *self.queue]))

    # An old completed tip cannot authorize removal of subsequent integrated work.
    def test_changed_head_invalidates_completion_evidence(self):
        """Keep new commits even after they reach the integration branch."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.git(self.linked, 'commit', '--allow-empty', '-m', 'New unfinished work')
        self.git(self.repo, 'merge', '--ff-only', 'integrated')
        self.git(self.repo, 'push', 'origin', 'main')
        self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))

    # Resolve abbreviated queue tips to full commits instead of comparing prefixes.
    def test_unique_abbreviated_completed_tip_is_accepted(self):
        """A unique Git abbreviation identifies the exact completed HEAD."""
        self.queue[0]['tip'] = self.queue[0]['tip'][:12]
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.assertEqual(worktrees._candidate_reason(self.env, entry, False, self.queue), '')

    # Missing, invalid and nonmatching completion hashes must retain the worktree.
    def test_invalid_or_different_completed_tip_is_kept(self):
        """Reject absent, malformed and unresolved queue tip values."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        for tip in ('', None, 'HEAD', 'a' * 40, self.queue[0]['tip'][:4]):
            with self.subTest(tip=tip):
                self.assertTrue(worktrees._candidate_reason(self.env, entry, False, [dict(self.queue[0], tip=tip)]))

    # A failed fetch cannot authorize removal using a stale remote ref.
    def test_fetch_failure_keeps_candidate(self):
        """A failed fetch cannot authorize removal using a stale remote ref."""
        self.git(self.repo, 'remote', 'set-url', 'origin', str(self.repo.parent / 'missing'))
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])
        self.assertTrue(self.linked.exists())

    # Changes to a tracked file protect the worktree.
    def test_tracked_change_is_kept(self):
        """Changes to a tracked file protect the worktree."""
        (self.linked / 'file').write_text('changed', encoding='utf-8')
        self.assertNotIn(str(self.linked), worktrees.prune(self.env)['removed'])

    # Target verification counts files and their total bytes recursively.
    def test_link_target_fingerprint_counts_files_and_bytes(self):
        """Target verification counts files and their total bytes recursively."""
        target = self.repo.parent / 'target'
        (target / 'nested').mkdir(parents=True)
        (target / 'one').write_bytes(b'123')
        (target / 'nested' / 'two').write_bytes(b'12')
        self.assertEqual(worktrees._target_size(target), (2, 5))

    # Repository checks reject queued branches and untracked data.
    def test_candidate_checks_queue_and_untracked_data(self):
        """Repository checks reject queued branches and untracked data."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        for state in ('ready', 'in_run', 'blocked'):
            self.assertTrue(worktrees._candidate_reason(self.env, entry, False, [{'branch': 'integrated', 'status': state}]))
        (self.linked / 'new').write_text('data', encoding='utf-8')
        self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))

    # An active native Bee's assigned worktree is protected even without an agent row.
    def test_native_bee_assignment_protects_worktree(self):
        """An active native Bee's assigned worktree is protected even without an agent row."""
        import sqlite3
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE bee_assignments(project TEXT, plan_path TEXT, package TEXT)')
        conn.execute('CREATE TABLE bee_worktrees(project TEXT, plan_path TEXT, package TEXT, cwd TEXT)')
        conn.execute('INSERT INTO bee_assignments VALUES (?, ?, ?)', ('test', 'plan', 'P1'))
        conn.execute('INSERT INTO bee_worktrees VALUES (?, ?, ?, ?)', ('test', 'plan', 'P1', str(self.linked)))
        self.env.conn = conn
        self.agents = [{'status': 'running', 'phase': 'queen', 'project': 'test', 'plan_path': 'plan', 'cwd': str(self.repo)}]
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))

    # Both active agent states protect their current worktree.
    def test_starting_and_running_agents_protect_worktree(self):
        """Both active agent states protect their current worktree."""
        entry = next(item for item in worktrees.list_worktrees(self.repo) if Path(item['worktree']) == self.linked)
        for phase in ('queen', 'bee'):
            for status in ('starting', 'running'):
                with self.subTest(phase=phase, status=status):
                    self.agents = [{'status': status, 'phase': phase, 'cwd': str(self.linked)}]
                    self.assertTrue(worktrees._candidate_reason(self.env, entry, False, self.queue))

    # Projects protect all ignored files unless they opt into build output.
    def test_project_cleanup_settings_default_empty(self):
        """Projects protect all ignored files unless they opt into build output."""
        from haivequeen import scheduler
        settings = scheduler.build_settings({}, {})
        self.assertNotIn('ignoredBuildOutput', settings)   # the .gitignore of the repository decides what is build output
        self.assertNotIn('protectedWorktrees', settings)   # the cleanup removes only worktrees of branches it integrated

    # A configured cache directory also covers nested Python cache files.
    def test_nested_python_cache_matches_allowed_output(self):
        """A configured cache directory also covers nested Python cache files."""
        self.git(self.repo, 'config', 'core.excludesFile', str(self.repo.parent / 'global-ignore'))
        (self.repo.parent / 'global-ignore').write_text('__pycache__/\n', encoding='utf-8')
        cache = self.linked / 'module' / '__pycache__'
        cache.mkdir(parents=True)
        (cache / 'file.pyc').write_bytes(b'x')
        self.assertEqual(worktrees._ignored_blocker(self.linked, ['__pycache__/']), '')

    # The cleanup command accepts project selection and a dry run.
    def test_worktrees_prune_command_accepts_project_and_dry_run(self):
        """The cleanup command accepts project selection and a dry run."""
        from haivequeen import cli
        args = cli.build_parser().parse_args(['worktrees', 'prune', '--project', 'test', '--dry-run'])
        self.assertEqual(args.project, 'test')
        self.assertTrue(args.dry_run)


    # Git can remove ignored build output without a force option.
    def test_git_remove_handles_ignored_output_without_force(self):
        """Git can remove ignored build output without a force option."""
        (self.linked / 'ignored').mkdir()
        (self.linked / 'ignored' / 'build').write_text('generated', encoding='utf-8')
        result = worktrees._git(self.repo, 'worktree', 'remove', str(self.linked))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.linked.exists())


    # Once all checks pass, removing a worktree leaves its branch intact.
    def test_candidate_removal_keeps_branch(self):
        """Once all checks pass, removing a worktree leaves its branch intact."""
        result = worktrees._prune_candidates(self.env, worktrees.list_worktrees(self.repo), self.queue, lambda path: False, False)
        self.assertIn(str(self.linked), result['removed'])
        self.git(self.repo, 'show-ref', '--verify', 'refs/heads/integrated')

    # Git removal never receives a force option.
    def test_candidate_removal_does_not_use_force(self):
        """Git removal never receives a force option."""
        real_git = worktrees._git
        with mock.patch.object(worktrees, '_git', wraps=real_git) as git:
            result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertIn(str(self.linked), result['removed'])
        removals = [call.args for call in git.call_args_list if call.args[1:3] == ('worktree', 'remove')]
        self.assertEqual(len(removals), 1)
        self.assertNotIn('--force', removals[0])
        self.assertNotIn('-f', removals[0])

    # Unknown process state must never authorize removal.
    def test_unknown_process_state_keeps_candidate(self):
        """Unknown process state must never authorize removal."""
        result = worktrees._prune_candidates(self.env, worktrees.list_worktrees(self.repo), self.queue, lambda path: None, False)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A failed fetch prevents cleanup even when the local remote ref exists.
    def test_cleanup_fetch_failure_keeps_candidate(self):
        """A failed fetch prevents cleanup even when the local remote ref exists."""
        self.git(self.repo, 'remote', 'set-url', 'origin', str(self.repo.parent / 'missing'))
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A stale tracking ref cannot authorize removal after the remote branch changed.
    def test_cleanup_uses_fetched_tip_when_tracking_ref_is_stale(self):
        """A stale tracking ref cannot authorize removal after the remote branch changed."""
        base = self.git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        (self.linked / 'new').write_text('not integrated', encoding='utf-8')
        self.git(self.linked, 'add', 'new')
        self.git(self.linked, 'commit', '-m', 'Not integrated')
        tip = self.git(self.linked, 'rev-parse', 'HEAD').stdout.strip()
        self.queue[0]['tip'] = tip
        self.git(self.repo, 'update-ref', 'refs/remotes/origin/main', tip)
        self.git(self.repo, 'config', 'remote.origin.fetch', '+refs/heads/other:refs/remotes/origin/other')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/heads/main').stdout.strip(), base)
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A different remote tip after fetching prevents removal.
    def test_cleanup_keeps_candidates_if_remote_tip_changes_during_fetch(self):
        """A different remote tip after fetching prevents removal."""
        real_git = worktrees._git
        # Simulate a concurrent integration-branch update.
        def changed_remote(repo, *args):
            """Simulate a concurrent integration-branch update."""
            if args[:1] == ('ls-remote',):
                return subprocess.CompletedProcess(args, 0, '0' * 40 + '\trefs/heads/main\n', '')
            return real_git(repo, *args)
        with mock.patch.object(worktrees, '_git', side_effect=changed_remote):
            result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertNotIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # The public cleanup runs the verified candidate checks.
    def test_public_cleanup_uses_process_checker(self):
        """The public cleanup runs the verified candidate checks."""
        with mock.patch.object(worktrees, '_process_busy', create=True, return_value=False):
            result = worktrees.prune(self.env)
        self.assertIn(str(self.linked), result['removed'])

    # The CLI prints all cleanup lists without removing the candidate in a dry run.
    def test_command_dry_run_prints_three_lists(self):
        """The CLI prints all cleanup lists without removing the candidate in a dry run."""
        import contextlib
        import io
        import json
        from haivequeen import cli, config, scheduler
        args = cli.build_parser().parse_args(['worktrees', 'prune', '--project', 'test', '--dry-run'])
        output = io.StringIO()
        with mock.patch.object(config, 'load_config', return_value={}), \
             mock.patch.object(scheduler, 'load_projects', return_value=[self.env]), \
             mock.patch.object(worktrees, '_process_busy', return_value=False), contextlib.redirect_stdout(output):
            self.assertEqual(args.fn(args, self.env.conn), 0)
        result = json.loads(output.getvalue())['test']
        self.assertEqual(set(result), {'removed', 'kept', 'notRemovable', 'dryRun'})
        self.assertTrue(result['dryRun'])
        self.assertIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # An integrated candidate is removed after fetch and process verification.
    def test_cleanup_with_verified_process_state_removes_candidate(self):
        """An integrated candidate is removed after fetch and process verification."""
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertIn(str(self.linked), result['removed'])
        self.assertFalse(self.linked.exists())

    # A dry run lists an eligible worktree without removing it.
    def test_verified_dry_run_keeps_worktree(self):
        """A dry run lists an eligible worktree without removing it."""
        result = worktrees._prune_with_checker(self.env, lambda path: False, True)
        self.assertIn(str(self.linked), result['removed'])
        self.assertTrue(self.linked.exists())

    # A detached worktree lacks exact branch completion evidence.
    def test_verified_detached_head_is_kept(self):
        """Keep detached worktrees even when their commit is already integrated."""
        self.git(self.linked, 'checkout', '--detach')
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertNotIn(str(self.linked), result['removed'])

    # Explicitly allowed ignored output does not protect the worktree.
    def test_verified_ignored_build_output_can_be_removed(self):
        """Explicitly allowed ignored output does not protect the worktree."""
        (self.linked / 'ignored').mkdir()
        (self.linked / 'ignored' / 'output').write_text('build', encoding='utf-8')
        self.env.settings['ignoredBuildOutput'] = ['ignored/']
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertIn(str(self.linked), result['removed'])

    # Files the repository's .gitignore leaves out are build output and never keep a worktree.
    def test_verified_ignored_data_is_preserved(self):
        """Ignored user data is protected unless explicitly classified as build output."""
        (self.linked / 'ignored').mkdir()
        (self.linked / 'ignored' / 'data').write_text('output', encoding='utf-8')
        result = worktrees._prune_with_checker(self.env, lambda path: False, True)
        self.assertNotIn(str(self.linked), result['removed'])

    # Tracked changes and Git locks independently prevent cleanup.
    def test_verified_dirty_and_locked_candidates_are_kept(self):
        """Tracked changes and Git locks independently prevent cleanup."""
        (self.linked / 'file').write_text('changed', encoding='utf-8')
        self.assertNotIn(str(self.linked), worktrees._prune_with_checker(self.env, lambda path: False, False)['removed'])
        self.git(self.linked, 'checkout', '--', 'file')
        self.git(self.repo, 'worktree', 'lock', str(self.linked))
        self.assertNotIn(str(self.linked), worktrees._prune_with_checker(self.env, lambda path: False, False)['removed'])

    # Ready, running and blocked queue rows independently prevent cleanup.
    def test_verified_queue_states_are_kept(self):
        """Ready, running and blocked queue rows independently prevent cleanup."""
        for state in ('ready', 'in_run', 'blocked'):
            with self.subTest(state=state), mock.patch('haivequeen.worktrees.db.queue_rows', return_value=[{'branch': 'integrated', 'status': state}]):
                self.assertNotIn(str(self.linked), worktrees._prune_with_checker(self.env, lambda path: False, False)['removed'])

    # Configured release worktrees and the workspace cannot be removed.
    def test_verified_protected_worktree_and_workspace_are_kept(self):
        """Configured release worktrees and the workspace cannot be removed."""
        self.env.workspace = self.linked
        self.assertNotIn(str(self.linked), worktrees._prune_with_checker(self.env, lambda path: False, False)['removed'])

    # The Git main worktree and configured code checkout are protected.
    def test_git_main_worktree_and_code_repo_are_kept(self):
        """The Git main worktree and configured code checkout are protected."""
        records = worktrees.list_worktrees(self.repo)
        self.assertTrue(worktrees._candidate_reason(self.env, records[0], True, []))
        self.env.code_repo = self.linked
        self.assertTrue(worktrees._candidate_reason(self.env, records[1], False, []))

    # An ignored junction must be removed as a link without changing its target.
    def test_junction_removal_preserves_external_target(self):
        """An ignored junction must be removed as a link without changing its target."""
        import os
        if os.name != 'nt':
            self.skipTest('Windows junction test')
        target = self.repo.parent / 'target'
        target.mkdir()
        (target / 'marker').write_text('keep', encoding='utf-8')
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.linked / 'ignored'), str(target)],
                                 capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if created.returncode:
            self.skipTest('Cannot create a junction')
        self.env.settings['ignoredBuildOutput'] = ['ignored/']
        result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertIn(str(self.linked), result['removed'])
        self.assertEqual((target / 'marker').read_text(encoding='utf-8'), 'keep')

    # Link checks work without pathlib's Python 3.12 junction method.
    def test_link_checks_do_not_require_python_312_path_api(self):
        """Link checks work without pathlib's Python 3.12 junction method."""
        with mock.patch.object(Path, 'is_junction', create=True, side_effect=AssertionError('Python 3.11 has no is_junction')):
            self.assertEqual(worktrees._link_targets(self.linked), [])

    # A failed Git removal leaves the junction and its target in place.
    def test_failed_removal_restores_junction(self):
        """A failed Git removal leaves the junction and its target in place."""
        import os
        if os.name != 'nt':
            self.skipTest('Windows junction test')
        target = self.repo.parent / 'target'
        target.mkdir()
        (target / 'marker').write_text('keep', encoding='utf-8')
        link = self.linked / 'ignored'
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                 capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if created.returncode:
            self.skipTest('Cannot create a junction')
        self.env.settings['ignoredBuildOutput'] = ['ignored/']
        real_git = worktrees._git
        # Simulate a failure only for the removal command.
        def fail_remove(repo, *args):
            """Simulate a failure only for the removal command."""
            if args[:2] == ('worktree', 'remove'):
                return subprocess.CompletedProcess(args, 1, '', 'injected failure')
            return real_git(repo, *args)
        with mock.patch.object(worktrees, '_git', side_effect=fail_remove):
            result = worktrees._prune_with_checker(self.env, lambda path: False, False)
        self.assertTrue(result['notRemovable'])
        self.assertTrue(link.is_dir())
        self.assertEqual((target / 'marker').read_text(encoding='utf-8'), 'keep')


# Check automatic cleanup at the round boundary.
class CleanupSchedulingTests(unittest.TestCase):
    """Check automatic cleanup at the round boundary."""

    # A normal round checks old worktrees for every project.
    def test_round_calls_cleanup_for_each_project(self):
        """A normal round checks old worktrees for every project."""
        from haivequeen import hives, loop
        import os
        import sqlite3
        from haivequeen import scheduler
        with tempfile.TemporaryDirectory() as folder:
            conn = sqlite3.connect(os.path.join(folder, 'test.db'))
            env = SimpleNamespace(name='test', settings={}, log=mock.Mock(), conn=conn, workspace=None, colour=None, priority=0, plan_globs=[], plan_exclude=[])
            with mock.patch.object(hives, 'schedule_round', return_value={}), \
                 mock.patch.object(scheduler, 'ProjectEnv', lambda *args: env), \
                 mock.patch.object(worktrees, 'prune', create=True, return_value={'removed': [], 'kept': [], 'notRemovable': []}) as prune:
                loop.run_round(None, {}, envs=[env])
                worktrees.wait_cleanups(30)   # the cleanup runs apart from the round
            conn.close()
        prune.assert_called_once()   # on a copy of the environment with a database connection of the cleanup thread
        self.assertEqual(prune.call_args.args[0].name, 'test')

    # Releasing the integration place triggers a cleanup afterwards.
    def test_release_runs_cleanup_after_releasing_place(self):
        """Releasing the integration place triggers a cleanup afterwards."""
        import os
        from haivequeen import cli, config, hives, scheduler
        env = SimpleNamespace(name='test', settings={}, log=mock.Mock())
        args = SimpleNamespace(action='release', project='test', plan='plan')
        order = []
        with mock.patch.dict(os.environ, {'BZZ_AGENT': ''}), \
             mock.patch.object(hives, 'get', return_value=None), \
             mock.patch.object(hives, 'release_integration', side_effect=lambda *args: order.append('release')), \
             mock.patch.object(config, 'load_config', return_value={}), \
             mock.patch.object(scheduler, 'load_projects', return_value=[env]), \
             mock.patch.object(worktrees, 'prune', create=True, side_effect=lambda *args: order.append('cleanup') or {}), \
             mock.patch('builtins.print'):
            self.assertEqual(cli.cmd_integration(args, None), 0)
        self.assertEqual(order, ['release', 'cleanup'])

    def test_disabled_automatic_cleanup_is_skipped_in_round(self):
        from haivequeen import hives, loop
        env = SimpleNamespace(name='test', settings={'deleteFinishedWorktrees': False}, log=mock.Mock())
        with mock.patch.object(hives, 'schedule_round', return_value={}), \
             mock.patch.object(worktrees, 'prune') as prune:
            loop.run_round(None, {}, envs=[env])
        prune.assert_not_called()



    # A missing link target cannot be treated as verified complete.
    def test_target_fingerprint_rejects_missing_target(self):
        """A missing link target cannot be treated as verified complete."""
        with self.assertRaises(OSError):
            worktrees._target_size(Path(tempfile.gettempdir()) / 'definitely-missing-worktree-target-17ef8')
