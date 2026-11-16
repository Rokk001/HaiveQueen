import tempfile
import unittest
from pathlib import Path

from haivequeen import release_activation, version_layout, version_leases


class ReleaseActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for version in ('1.0.0', '2.0.0'):
            folder = self.root / 'versions' / version / 'haivequeen'
            folder.mkdir(parents=True)
            (folder / 'cli.py').write_text('pass', encoding='utf-8')
        version_layout.set_active(self.root, '1.0.0')

    def active(self):
        return (self.root / 'active-version').read_text(encoding='utf-8').strip()

    def test_success_retains_new_pointer_and_old_is_reserved_during_check(self):
        def first_round():
            self.assertEqual(self.active(), '2.0.0')
            self.assertTrue(any(r['version'] == '1.0.0' for r in version_leases._load(self.root).values()))
            return True
        self.assertTrue(release_activation.activate(self.root, '2.0.0', first_round, target_verifier=lambda: True))
        self.assertEqual(self.active(), '2.0.0')
        self.assertEqual(version_leases._load(self.root), {})

    def test_mutated_candidate_is_rejected_at_publication(self):
        candidate = self.root / 'versions' / '2.0.0' / 'haivequeen' / 'cli.py'
        expected = candidate.read_bytes()
        self.assertEqual(candidate.read_bytes(), expected)  # initial verification
        candidate.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'target verification failed'):
            release_activation.activate(self.root, '2.0.0', lambda: self.fail('first round ran'),
                                        target_verifier=lambda: candidate.read_bytes() == expected)
        self.assertEqual(self.active(), '1.0.0')
        self.assertEqual(version_leases._load(self.root), {})

    def test_missing_or_raising_verifier_fails_closed(self):
        with self.assertRaises(RuntimeError):
            release_activation.activate(self.root, '2.0.0', lambda: True)
        def fail_verification():
            raise ValueError('verification failed')
        with self.assertRaises(ValueError):
            release_activation.activate(self.root, '2.0.0', lambda: True, target_verifier=fail_verification)
        self.assertEqual(self.active(), '1.0.0')
        self.assertEqual(version_leases._load(self.root), {})

    def test_failed_round_restores_old_pointer(self):
        self.assertFalse(release_activation.activate(self.root, '2.0.0', lambda: False, target_verifier=lambda: True))
        self.assertEqual(self.active(), '1.0.0')
        self.assertEqual(version_leases._load(self.root), {})

    def test_exception_restores_old_pointer(self):
        def fail():
            raise RuntimeError('round failed')
        with self.assertRaisesRegex(RuntimeError, 'round failed'):
            release_activation.activate(self.root, '2.0.0', fail, target_verifier=lambda: True)
        self.assertEqual(self.active(), '1.0.0')


if __name__ == '__main__':
    unittest.main()
