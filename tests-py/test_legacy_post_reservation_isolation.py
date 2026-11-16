import tempfile
import unittest
from pathlib import Path
from unittest import mock

from haivequeen import release_activation, version_layout, version_leases


class PostReservationIsolationTests(unittest.TestCase):
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

    def run_activation(self, passed, isolate):
        old = self.root / 'versions' / '1.0.0'
        isolated = self.root / 'versions' / 'isolated-old'
        real_reserve = version_leases._reserve_locked
        real_publish = version_layout._set_active_locked
        obs = {'rename_error': None, 'publication': [], 'first_round': [], 'rollback': []}

        def reserve(root, version, *args, **kwargs):
            token = real_reserve(root, version, *args, **kwargs)
            if isolate and version == '1.0.0':
                try:
                    old.rename(isolated)
                except OSError as exc:
                    obs['rename_error'] = repr(exc)
                    raise
            return token

        def publish(root, version, *args, **kwargs):
            obs['publication'].append((version, old.exists()))
            return real_publish(root, version, *args, **kwargs)

        def first_round():
            obs['first_round'].append((self.active(), old.exists(), version_leases._load(self.root)))
            return passed

        result, error = None, None
        with mock.patch.object(version_leases, '_reserve_locked', side_effect=reserve), \
             mock.patch.object(version_layout, '_set_active_locked', side_effect=publish):
            try:
                result = release_activation.activate(
                    self.root, '2.0.0', first_round,
                    after_rollback=lambda version: obs['rollback'].append(version),
                    target_verifier=lambda: True)
            except Exception as exc:
                error = exc
        obs.update(result=result, error=repr(error), pointer=self.active(),
                   reservations=version_leases._load(self.root), old_exists=old.exists(),
                   isolated_exists=isolated.exists())
        print('observation:', obs)
        return obs, error

    def test_isolated_success_publishes_and_cleans_reservation(self):
        obs, error = self.run_activation(True, True)
        self.assertIsNone(error)
        self.assertIsNone(obs['rename_error'])
        self.assertTrue(obs['result'])
        self.assertEqual(obs['publication'], [('2.0.0', False)])
        self.assertEqual(obs['first_round'][0][:2], ('2.0.0', False))
        self.assertEqual([v['version'] for v in obs['first_round'][0][2].values()], ['1.0.0'])
        self.assertEqual(obs['pointer'], '2.0.0')
        self.assertEqual(obs['reservations'], {})
        self.assertFalse(obs['old_exists'])
        self.assertTrue(obs['isolated_exists'])
        self.assertEqual(obs['rollback'], [])

    def test_isolated_failure_cannot_restore_missing_old_path(self):
        obs, error = self.run_activation(False, True)
        self.assertIsNone(obs['rename_error'])
        self.assertIsInstance(error, FileNotFoundError)
        self.assertEqual(str(error), 'version is not installed: 1.0.0')
        self.assertIsNone(obs['result'])
        self.assertEqual(obs['publication'], [('2.0.0', False), ('1.0.0', False)])
        self.assertEqual(obs['first_round'][0][:2], ('2.0.0', False))
        self.assertEqual([v['version'] for v in obs['first_round'][0][2].values()], ['1.0.0'])
        self.assertEqual(obs['pointer'], '2.0.0')
        self.assertEqual(obs['reservations'], {})
        self.assertEqual(obs['rollback'], [])
        self.assertFalse(obs['old_exists'])
        self.assertTrue(obs['isolated_exists'])

    def test_normal_failed_first_round_control(self):
        obs, error = self.run_activation(False, False)
        self.assertIsNone(error)
        self.assertFalse(obs['result'])
        self.assertEqual(obs['publication'], [('2.0.0', True), ('1.0.0', True)])
        self.assertEqual(obs['first_round'][0][:2], ('2.0.0', True))
        self.assertEqual(obs['pointer'], '1.0.0')
        self.assertEqual(obs['rollback'], ['1.0.0'])
        self.assertEqual(obs['reservations'], {})


if __name__ == '__main__':
    unittest.main()
