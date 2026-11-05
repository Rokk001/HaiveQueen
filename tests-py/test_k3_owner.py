import unittest
from haivequeen import readygate

class OwnerVerificationTests(unittest.TestCase):
    def plan(self, packages):
        return '+++\n' + ''.join('[[packages]]\nid = "P%s"\nverify = "%s"\nrequirements = ["R-1"]\n' % (i, v) for i, v in enumerate(packages)) + '+++\n# Plan\n## Requirements\n- R-1: owner check result\n'

    def test_only_all_owner_packages_exempt(self):
        for verifies, exempt in [(['owner'], True), (['owner', 'owner'], True), (['owner', 'code'], False), ([], False)]:
            result = readygate.evaluate(self.plan(verifies), '## Completeness\n- R-1: owner-check\n', [])
            self.assertEqual(result.owner == ['R-1'], exempt, verifies)
            self.assertEqual(bool(result.blocking), not exempt, verifies)
