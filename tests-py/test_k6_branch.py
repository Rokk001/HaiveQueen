import unittest
from bzztest import REL, RoundCase
from haivequeen import bees

class DefaultPlanBranchTest(RoundCase):
    def test_cut_default_is_the_lowercase_plan_filename(self):
        self.plan('SENT', [{'id': 'P1', 'title': 'First'}])
        self.assertEqual(bees.cut_default(self.env(), REL)[0], 'dummy')
        self.assertIsNone(bees.plan_worktree(self.env(), REL))   # the header is the only source: no fallback to the file name

    def test_first_round_writes_the_derived_branch_into_the_header_of_a_running_plan(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        bees.migrate_headers(self.env())
        self.assertEqual(bees.plan_worktree(self.env(), REL)[0], 'dummy')
