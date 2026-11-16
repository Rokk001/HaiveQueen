import tempfile
import unittest
from pathlib import Path

from haivequeen import release_activation, version_layout, version_leases


class IsolatedActivationTests(unittest.TestCase):
    """Measure activation only; neither case proves general launch exclusion."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for version in ('1.0.0', '2.0.0'):
            folder = self.root / 'versions' / version / 'haivequeen'
            folder.mkdir(parents=True)
            (folder / 'cli.py').write_text(version, encoding='utf-8')
        version_layout.set_active(self.root, '1.0.0')

    def active(self):
        return (self.root / 'active-version').read_text(encoding='utf-8').strip()

    def test_normal_failed_first_round_rolls_back(self):
        observations = []
        def first_round():
            observations.append((self.active(), version_leases._load(self.root)))
            return False
        result = release_activation.activate(self.root, '2.0.0', first_round,
                                             target_verifier=lambda: True)
        print('normal:', result, 'first_round:', observations, 'pointer:', self.active(),
              'reservations:', version_leases._load(self.root))
        self.assertFalse(result)
        self.assertEqual(observations[0][0], '2.0.0')
        self.assertEqual([v['version'] for v in observations[0][1].values()], ['1.0.0'])
        self.assertEqual(self.active(), '1.0.0')
        self.assertEqual(version_leases._load(self.root), {})

    def test_isolated_old_stops_before_publication(self):
        old = self.root / 'versions' / '1.0.0'
        isolated = self.root / 'versions' / 'isolated-old'
        candidate = self.root / 'versions' / '2.0.0' / 'haivequeen' / 'cli.py'
        expected = candidate.read_bytes()
        observations = {'first_round': False, 'rename_error': None}
        def verify_and_isolate():
            self.assertEqual(candidate.read_bytes(), expected)
            try:
                old.rename(isolated)
            except OSError as exc:
                observations['rename_error'] = repr(exc)
                raise
            return True
        def first_round():
            observations['first_round'] = True
            return False
        error = None
        result = None
        try:
            result = release_activation.activate(self.root, '2.0.0', first_round,
                                                 target_verifier=verify_and_isolate)
        except Exception as exc:
            error = exc
        print('isolated: rename_error:', observations['rename_error'], 'exception:', repr(error),
              'return:', result, 'pointer:', self.active(), 'reservations:',
              version_leases._load(self.root), 'first_round:', observations['first_round'])
        self.assertIsNone(observations['rename_error'])
        self.assertFalse(old.exists())
        self.assertTrue(isolated.is_dir())
        self.assertFalse(observations['first_round'])  # Isolation prevents publication.
        self.assertIsInstance(error, FileNotFoundError)
        self.assertEqual(str(error), '1.0.0')
        self.assertIsNone(result)
        self.assertEqual(self.active(), '1.0.0')
        self.assertEqual(version_leases._load(self.root), {})


if __name__ == '__main__':
    unittest.main()


