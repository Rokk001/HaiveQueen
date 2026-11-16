import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_release_compatibility_switch as fixtures
from haivequeen import release_activation, version_leases


class VerifiedLaunchTests(unittest.TestCase):
    def interleaving(self, rollback):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.install()
        root = fixture.root
        from haivequeen import release_contract
        authority = release_contract.prepare(root, 'example/project', fetch=fixture.fetch)
        child = []
        observed = []
        code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                "from haivequeen import version_leases;"
                "print('ready',flush=True);"
                "token=version_leases.reserve(sys.argv[2],'1.0.0');"
                "print(json.dumps({'token':token,'module':version_leases.__file__}),flush=True)")
        def verify():
            decision = release_contract.assess(root, '2.0.0', '1.0.0', authority)
            if not decision['compatible']:
                return False
            process = subprocess.Popen([sys.executable, '-I', '-c', code,
                                        str(root / 'versions/1.0.0'), str(root)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child.append(process)
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertIsNone(process.poll())
            return True
        def first_round():
            self.assertEqual((root / 'active-version').read_text().strip(), '2.0.0')
            out, err = child[0].communicate(timeout=15)
            self.assertEqual(child[0].returncode, 0, err)
            result = json.loads(out)
            self.assertEqual(Path(result['module']), root / 'versions/1.0.0/haivequeen/version_leases.py')
            self.assertEqual(version_leases._load(root)[result['token']]['version'], '1.0.0')
            observed.append(result['token'])
            return not rollback
        try:
            result = release_activation.activate(root, '2.0.0', first_round, target_verifier=verify)
            self.assertEqual(result, not rollback)
            self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0' if rollback else '2.0.0')
            self.assertEqual(set(version_leases._load(root)), set(observed) | {
                token for token, record in version_leases._load(root).items() if record.get('installation')})
        finally:
            for process in child:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=10)

    def test_supported_old_launch_queued_at_publication(self):
        self.interleaving(False)

    def test_supported_old_launch_survives_rollback(self):
        self.interleaving(True)
