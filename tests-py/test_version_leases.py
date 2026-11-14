import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config, version_leases


class VersionLeasesTests(unittest.TestCase):
    def test_collect_retains_active_live_and_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            for name in ('current', 'live', 'pending', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('current', encoding='utf-8')
            with patch.object(config, 'config_dir', return_value=base / 'data'), patch.object(version_leases.platform, 'process_identity', return_value='identity') as identity:
                token = version_leases.reserve(root, 'live')
                pending = version_leases.reserve(root, 'pending')
                version_leases.publish(root, token, 123)
                self.assertEqual(version_leases.collect(root), ['old'])
                version_leases.release(root, pending)
                self.assertEqual(version_leases.collect(root), ['pending'])
                identity.return_value = 'reused'
                self.assertEqual(version_leases.collect(root), ['live'])
                self.assertTrue((root / 'versions' / 'current').is_dir())

    def test_unknown_identity_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            for name in ('active', 'unknown'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(config, 'config_dir', return_value=base / 'data'), patch.object(version_leases.platform, 'process_identity', return_value=None):
                token = version_leases.reserve(root, 'unknown')
                version_leases.publish(root, token, 100)
                self.assertEqual(version_leases.collect(root), [])

    def test_launch_reserves_before_spawn_and_binds_process(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            version = root / 'versions' / 'old'
            version.mkdir(parents=True)
            (root / 'active-version').write_text('new', encoding='utf-8')
            (root / 'versions' / 'new').mkdir()
            class Process:
                pid = 123
            def spawn():
                self.assertEqual(version_leases.collect(root), [])
                return Process()
            with patch.object(config, 'config_dir', return_value=base / 'data'), patch.object(version_leases.platform, 'process_identity', return_value='identity'):
                process = version_leases.launch(version, spawn)
                self.assertEqual(process.pid, 123)
                self.assertEqual(version_leases.collect(root), [])
                record = next(iter(version_leases._load(root).values()))
                self.assertEqual(record['pid'], 123)

    def test_launch_failure_releases_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            version = root / 'versions' / 'old'
            version.mkdir(parents=True)
            (root / 'active-version').write_text('old', encoding='utf-8')
            def fail():
                raise OSError('start failed')
            with patch.object(config, 'config_dir', return_value=base / 'data'):
                with self.assertRaises(OSError):
                    version_leases.launch(version, fail)
                self.assertEqual(version_leases._load(root), {})


if __name__ == '__main__':
    unittest.main()
