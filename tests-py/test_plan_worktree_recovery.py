"""Recover the explicit plan worktree from numbered plural branch rules."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import isolation
from haivequeen import bees


# Exercise the Workers plan declaration using only temporary repositories.
class PlanWorktreeRecoveryTests(unittest.TestCase):
    # Create an isolated code repository and a separate plan workspace.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.workspace = self.root / 'plans'
        self.workspace.mkdir()
        self.target = self.root / 'worker-jobs'
        self.git('init', '-b', 'integration/all')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '--allow-empty', '-m', 'initial')
        self.git('remote', 'add', 'origin', str(self.repo))
        self.git('fetch', 'origin', 'integration/all')
        self.env = SimpleNamespace(workspace=self.workspace, code_repo=self.repo,
                                   settings={'integrationBranch': 'integration/all'}, log=lambda text: None)
        self.write_plan('1. Branches:')

    # Run Git only within the temporary code repository.
    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True, timeout=15).stdout.strip()

    # Write the main branch rule before its distinct package sub-branch rule.
    def write_plan(self, prefix):
        (self.workspace / 'plan.md').write_text(
            '# Plan\n%s `feature/worker-jobs` in worktree `%s`, created by S1\n'
            '   from `origin/integration/all`. One sub-branch per package\n'
            '   `feature/worker-jobs-<id>` in worktree `unused`.\n' % (prefix, self.target),
            encoding='utf-8')

    # Resolve the numbered plural declaration without a state file or owner answer.
    def test_plural_branches_resolves_explicit_main_worktree(self):
        self.assertEqual(bees.plan_worktree(self.env, 'plan.md'), ('feature/worker-jobs', self.target))

    # Keep the existing singular declaration working.
    def test_singular_branch_keeps_existing_resolution(self):
        self.write_plan('- Branch')
        self.assertEqual(bees.plan_worktree(self.env, 'plan.md'), ('feature/worker-jobs', self.target))

    # Create an absent named branch from the configured integration branch.
    def test_absent_named_branch_and_worktree_are_created(self):
        self.assertEqual(bees.prepare_queen_worktree(self.env, 'plan.md'), str(self.target))
        result = subprocess.run(['git', '-C', str(self.target), 'branch', '--show-current'],
                                check=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.stdout.strip(), 'feature/worker-jobs')
        self.assertEqual(self.git('branch', '--show-current'), 'integration/all')
        self.assertEqual(self.git('rev-parse', 'feature/worker-jobs'),
                         self.git('rev-parse', 'origin/integration/all'))

    # Reuse the registered branch checkout even when its path differs from the plan.
    def test_existing_branch_worktree_is_reused(self):
        existing = self.root / 'existing-worker-jobs'
        self.git('worktree', 'add', '-b', 'feature/worker-jobs', str(existing), 'origin/integration/all')
        self.assertEqual(bees.prepare_queen_worktree(self.env, 'plan.md'), str(existing))
        self.assertFalse(self.target.exists())
