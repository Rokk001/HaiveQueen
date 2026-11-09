"""F-10: worktree cleanup also without an integrated queue row."""
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import isolation
from haivequeen import worktrees


# Cleanup of integrated worktrees that never got a queue row.
class UnqueuedCleanupTests(unittest.TestCase):
    """A prefixed worktree in the worktree location whose HEAD is in the integration branch is removed."""

    # Create origin, repository and a prefixed worktree beside it.
    def setUp(self):
        """Create origin, repository and the worktree wt-done beside the repository."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        patch = mock.patch('haivequeen.platform.data_dir', return_value=root)
        patch.start()
        self.addCleanup(patch.stop)
        origin, self.repo = root / 'origin.git', root / 'repo'
        subprocess.run(['git', 'init', '--bare', str(origin)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(origin), str(self.repo)], check=True, capture_output=True)
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.git(self.repo, 'config', 'user.name', 'Test')
        (self.repo / 'file').write_text('test', encoding='utf-8')
        (self.repo / '.gitignore').write_text('ignored/\n', encoding='utf-8')
        self.git(self.repo, 'add', 'file', '.gitignore')
        self.git(self.repo, 'commit', '-m', 'Initial')
        self.git(self.repo, 'branch', '-M', 'main')
        self.git(self.repo, 'push', 'origin', 'main')
        self.root = root
        self.wt = root / 'wt-done'
        self.git(self.repo, 'worktree', 'add', '-b', 'sub-branch', str(self.wt))
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.env = SimpleNamespace(code_repo=self.repo, workspace=self.repo, name='test', conn=self.conn,
                                   settings={'integrationBranch': 'main', 'worktreePrefix': 'wt', 'deleteFinishedWorktrees': True},
                                   log=mock.Mock())
        self.queue = []
        self.agents = []
        self.plans = []
        for target, effect in (('haivequeen.worktrees.db.queue_rows', lambda *a: self.queue),
                               ('haivequeen.worktrees.runtime.list_agents', lambda *a: self.agents),
                               ('haivequeen.worktrees.runtime.list_plans', lambda *a: self.plans)):
            started = mock.patch(target, side_effect=effect)
            started.start()
            self.addCleanup(started.stop)
        started = mock.patch('haivequeen.bees.plan_worktree', return_value=None)
        started.start()
        self.addCleanup(started.stop)

    # Run Git in a temporary folder.
    def git(self, folder, *args):
        """Run Git in a temporary folder."""
        return subprocess.run(['git', '-C', str(folder), *args], check=True, capture_output=True, text=True)

    # Report whether the worktree would be removed.
    def removed(self, path=None):
        """Dry run; True when the cleanup lists the path as removable."""
        return str(path or self.wt) in worktrees.prune(self.env, dry=True)['removed']

    # Commit a file in a worktree.
    def commit(self, folder):
        """Commit one new file in the worktree and return the new HEAD."""
        (Path(folder) / 'extra').write_text('x', encoding='utf-8')
        self.git(folder, 'add', 'extra')
        self.git(folder, 'commit', '-m', 'Extra')

    # The basic case.
    def test_integrated_prefixed_worktree_without_queue_row_is_removed_and_branch_stays(self):
        """Removed for real; the branch stays."""
        result = worktrees.prune(self.env)
        self.assertIn(str(self.wt), result['removed'])
        self.assertFalse(self.wt.exists())
        self.git(self.repo, 'show-ref', '--verify', 'refs/heads/sub-branch')

    # File the .gitignore leaves out does not keep the worktree.
    def test_ignored_file_does_not_keep_it(self):
        """Ignored build output is no blocker."""
        (self.wt / 'ignored').mkdir()
        (self.wt / 'ignored' / 'x').write_text('x', encoding='utf-8')
        self.assertTrue(self.removed())

    # Name and location rule.
    def test_other_name_or_location_is_kept(self):
        """Without the prefix, outside the location or inside the repository the worktree stays."""
        other = self.root / 'plain'
        self.git(self.repo, 'worktree', 'add', '-b', 'b2', str(other))
        inside = self.repo / 'build' / 'wt-inner'
        self.git(self.repo, 'worktree', 'add', '-b', 'b3', str(inside))
        elsewhere = self.root / 'sub'
        elsewhere.mkdir()
        far = elsewhere / 'wt-far'
        self.git(self.repo, 'worktree', 'add', '-b', 'b4', str(far))
        self.assertFalse(self.removed(other))
        self.assertFalse(self.removed(inside))
        self.assertFalse(self.removed(far))
        self.assertTrue(self.removed())
        self.env.settings['worktreeLocation'] = str(elsewhere)
        self.assertTrue(self.removed(far))
        self.assertFalse(self.removed())

    # Prefix needs the dash.
    def test_prefix_needs_the_separator(self):
        """A folder wtx-a does not carry the prefix wt-."""
        odd = self.root / 'wtx-a'
        self.git(self.repo, 'worktree', 'add', '-b', 'b5', str(odd))
        self.assertFalse(self.removed(odd))

    # Content blockers.
    def test_head_not_contained_is_kept_with_reason(self):
        """A commit that is not in the integration branch keeps the worktree."""
        self.commit(self.wt)
        result = worktrees.prune(self.env, dry=True)
        self.assertEqual([i['reason'] for i in result['kept'] if i['path'] == str(self.wt)], ['HEAD is not in the integration branch'])

    # Changes keep it.
    def test_tracked_change_and_untracked_file_are_kept(self):
        """A tracked change or an untracked file that is not ignored keeps the worktree."""
        (self.wt / 'file').write_text('changed', encoding='utf-8')
        self.assertFalse(self.removed())
        self.git(self.wt, 'checkout', 'file')
        (self.wt / 'new').write_text('n', encoding='utf-8')
        self.assertFalse(self.removed())

    # Existing protections.
    def test_protections_hold(self):
        """Open queue row, running agent, lock and main checkout keep the worktree."""
        self.queue = [{'id': 1, 'branch': 'sub-branch', 'status': 'ready'}]
        self.assertFalse(self.removed())
        self.queue = []
        self.agents = [{'status': 'running', 'phase': 'bee', 'cwd': str(self.wt), 'name': 'a', 'plan_path': None}]
        self.assertFalse(self.removed())
        self.agents = []
        self.git(self.repo, 'worktree', 'lock', str(self.wt))
        self.assertFalse(self.removed())
        self.git(self.repo, 'worktree', 'unlock', str(self.wt))
        self.assertTrue(self.removed())
        self.assertNotIn(str(self.repo), worktrees.prune(self.env, dry=True)['removed'])

    # A link inside.
    def test_link_inside_keeps_it(self):
        """A link inside the worktree keeps it."""
        target = self.root / 'target'
        target.mkdir()
        (target / 'f').write_text('f', encoding='utf-8')
        link = self.wt / 'lnk'
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            self.skipTest('no symlink right')
        self.assertFalse(self.removed())

    # Plan use.
    def test_worktree_of_an_open_plan_stays(self):
        """An agent of a plan that is not DONE or CANCELLED used the worktree: it stays; of a finished plan: removed."""
        self.plans = [{'path': 'p.md', 'status': 'ACTIVE'}]
        self.agents = [{'status': 'done', 'phase': 'bee', 'cwd': str(self.wt), 'name': 'a', 'plan_path': 'p.md'}]
        self.assertFalse(self.removed())
        for status in ('DONE', 'CANCELLED'):
            self.plans = [{'path': 'p.md', 'status': status}]
            self.assertTrue(self.removed(), status)

    # Assignment table and plan worktree.
    def test_assigned_worktree_of_an_open_plan_stays(self):
        """A bee_worktrees row or the plan's own worktree of an open plan keeps the worktree."""
        self.plans = [{'path': 'p.md', 'status': 'ACTIVE'}]
        self.conn.execute('CREATE TABLE bee_worktrees (project TEXT, plan_path TEXT, package TEXT, cwd TEXT)')
        self.conn.execute('INSERT INTO bee_worktrees VALUES (?,?,?,?)', ('test', 'p.md', 'F1', str(self.wt)))
        self.assertFalse(self.removed())
        self.conn.execute('DELETE FROM bee_worktrees')
        self.assertTrue(self.removed())
        with mock.patch('haivequeen.bees.plan_worktree', return_value=('x', self.wt)):
            self.assertFalse(self.removed())

    # Switch.
    def test_switch_off_removes_nothing(self):
        """With the switch off the round cleanup does nothing and prune itself keeps the worktree."""
        self.env.settings['deleteFinishedWorktrees'] = False
        with mock.patch.object(worktrees, 'prune') as prune:
            worktrees.cleanup(self.env)
        prune.assert_not_called()
        self.assertFalse(self.removed())
        self.assertTrue(self.wt.exists())

    # Network is used for the new candidates.
    def test_candidate_without_queue_row_triggers_the_fetch(self):
        """The integration branch is fetched in the same round."""
        with mock.patch.object(worktrees, '_git', wraps=worktrees._git) as git:
            worktrees._prune_with_checker(self.env, lambda path: False, True)
        self.assertIn('fetch', [c.args[1] for c in git.call_args_list if len(c.args) > 1])


if __name__ == '__main__':
    unittest.main()
