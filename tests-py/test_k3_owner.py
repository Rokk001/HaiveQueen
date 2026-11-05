import unittest
from haivequeen import readygate, runtime
from bzztest import REL

class OwnerVerificationTests(unittest.TestCase):
    def plan(self, packages):
        return '+++\n' + ''.join('[[packages]]\nid = "P%s"\nverify = "%s"\nrequirements = ["R-1"]\n' % (i, v) for i, v in enumerate(packages)) + '+++\n# Plan\n## Requirements\n- R-1: owner check result\n'

    def test_only_all_owner_packages_exempt(self):
        for verifies, exempt in [(['owner'], True), (['owner', 'owner'], True), (['owner', 'code'], False), ([], False)]:
            result = readygate.evaluate(self.plan(verifies), '## Completeness\n- R-1: owner-check\n', [])
            self.assertEqual(result.owner == ['R-1'], exempt, verifies)
            self.assertEqual(bool(result.blocking), not exempt, verifies)

class CutSnapshotTests(__import__('test_plan_submit').SubmittedPlanTests):
    def test_snapshot_survives_header_changes_and_rechecks(self):
        from haivequeen import plans
        self.queen_of('SUBMITTED', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Second', 'verify': 'code', 'requirements': ['R-2']}])
        self.assertEqual(self.check()[0], 0)
        original = runtime.cut_assignments(self.conn, 'testproj', REL)
        self.assertEqual(original, [['R-1', 'owner'], ['R-2', 'code']])
        self.plan('READY', [{'id': 'P1', 'title': 'Changed', 'verify': 'code', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Changed', 'verify': 'owner', 'requirements': ['R-2']}])
        self.check()
        self.assertEqual(runtime.cut_assignments(self.conn, 'testproj', REL), original)

    def test_existing_plan_without_snapshot_is_conservative(self):
        self.queen_of('READY', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}])
        self.assertIsNone(runtime.cut_assignments(self.conn, 'testproj', REL))
        result = readygate.evaluate(OwnerVerificationTests().plan(['owner']), '## Completeness\n- R-1: owner-check\n', [], [])
        self.assertEqual(result.owner, [])
        self.assertTrue(result.blocking)


    def test_mixed_cut_coverage_does_not_exempt_after_header_edit(self):
        self.queen_of('SUBMITTED', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Second', 'verify': 'code', 'requirements': ['R-1']}])
        self.assertEqual(self.check()[0], 0)
        assignments = runtime.cut_assignments(self.conn, 'testproj', REL)
        result = readygate.evaluate(OwnerVerificationTests().plan(['owner']), '## Completeness\n- R-1: owner-check\n', [], assignments)
        self.assertEqual(result.owner, [])
        self.assertTrue(result.blocking)
