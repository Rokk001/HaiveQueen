import unittest
from bzztest import REL, RoundCase
from haivequeen import bees

class DefaultPlanBranchTest(RoundCase):
    def test_branch_defaults_to_lowercase_plan_filename(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.assertEqual(bees.plan_worktree(self.env(), REL)[0], 'dummy')
