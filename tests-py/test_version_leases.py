import tempfile
import os
import errno
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config, version_leases, version_layout


class VersionLeasesTests(unittest.TestCase):
    def test_interrupted_install_reservation_requires_definite_death_and_no_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'versions' / 'active').mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            records = {
                'pending': {'version': 'pending', 'installation': True,
                            'installer_pid': 123, 'installer_identity': 'birth'},
                'complete': {'version': 'complete', 'installation': True,
                             'installer_pid': 123, 'installer_identity': 'birth'},
                'legacy': {'version': 'legacy', 'installation': True},
            }
            (root / 'versions' / 'complete').mkdir()
            version_leases._save(root, records)
            with patch.object(version_leases.platform, 'process_identity', return_value=None), \
                 patch.object(version_leases.platform, 'process_absent', return_value=False) as absent:
                version_leases.collect(root)
                self.assertEqual(set(version_leases._load(root)), set(records))
                absent.return_value = True
                version_leases.collect(root)
                self.assertEqual(set(version_leases._load(root)), {'complete', 'legacy'})
                self.assertTrue((root / 'versions' / 'complete').exists())

    def test_absent_probe_requires_definitive_os_error(self):
        platform = version_leases.platform
        with patch.object(platform, 'is_windows', return_value=False), patch.object(platform, 'is_linux', return_value=True), patch.object(platform.os, 'kill') as kill:
            kill.side_effect = PermissionError(errno.EPERM, 'denied')
            self.assertFalse(platform.process_absent(123))
            kill.side_effect = ProcessLookupError(errno.ESRCH, 'missing')
            self.assertTrue(platform.process_absent(123))
            kill.side_effect = OSError(errno.EIO, 'unknown')
            self.assertFalse(platform.process_absent(123))

    def test_installation_survives_collection_until_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            source = base / 'source'
            cli = source / 'haivequeen' / 'cli.py'
            cli.parent.mkdir(parents=True)
            cli.touch()
            old = root / 'versions' / 'old' / 'haivequeen' / 'cli.py'
            old.parent.mkdir(parents=True)
            old.touch()
            (root / 'active-version').write_text('old', encoding='utf-8')
            with patch.object(config, 'config_dir', return_value=base / 'data'):
                version_layout.install_version(root, 'new', source)
                record = next(iter(version_leases._load(root).values()))
                self.assertEqual(record['installer_pid'], os.getpid())
                self.assertEqual(record['installer_identity'], version_leases.platform.process_identity(os.getpid()))
                self.assertEqual(version_leases.collect(root), [])
                self.assertTrue((root / 'versions' / 'new').exists())
                version_layout.set_active(root, 'new')
                self.assertEqual(version_leases._load(root), {})
                self.assertEqual(version_leases.collect(root), ['old'])

    def test_activation_and_collection_are_serialized(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            for name in ('old', 'new'):
                cli = root / 'versions' / name / 'haivequeen' / 'cli.py'
                cli.parent.mkdir(parents=True)
                cli.touch()
            (root / 'active-version').write_text('old', encoding='utf-8')
            entered, proceed, collected = threading.Event(), threading.Event(), threading.Event()
            original = version_layout.os.replace
            def replace(source, target):
                if Path(target).name == 'active-version':
                    entered.set()
                    self.assertTrue(proceed.wait(5))
                return original(source, target)
            def activate():
                version_layout.set_active(root, 'new')
            def collect():
                version_leases.collect(root)
                collected.set()
            with patch.object(config, 'config_dir', return_value=base / 'data'), patch.object(version_layout.os, 'replace', side_effect=replace):
                activating = threading.Thread(target=activate)
                activating.start()
                self.assertTrue(entered.wait(5))
                collecting = threading.Thread(target=collect)
                collecting.start()
                try:
                    self.assertFalse(collected.wait(.2))
                finally:
                    proceed.set()
                    activating.join(5)
                    collecting.join(5)
                self.assertTrue(collected.is_set())
                self.assertTrue((root / 'versions' / 'new').is_dir())

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
            with patch.object(config, 'config_dir', return_value=base / 'data'), patch.object(version_leases.platform, 'process_identity', return_value=None), patch.object(version_leases.platform, 'process_absent', return_value=False):
                token = version_leases.reserve(root, 'unknown')
                version_leases.publish(root, token, 100)
                self.assertEqual(version_leases.collect(root), [])

    def test_unknown_identity_is_recovered_only_after_definite_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(version_leases.platform, 'process_identity', return_value=None), \
                 patch.object(version_leases.platform, 'process_absent', return_value=False) as absent:
                token = version_leases.reserve(root, 'old')
                version_leases.publish(root, token, 123)
                self.assertEqual(version_leases.collect(root), [])
                absent.return_value = True
                self.assertEqual(version_leases.collect(root), ['old'])
                self.assertNotIn(token, version_leases._load(root))

    def test_unavailable_probe_is_not_proof_of_death(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(version_leases.platform, 'process_identity', return_value='birth') as identity:
                token = version_leases.reserve(root, 'old')
                version_leases.publish(root, token, 123)
                identity.return_value = None
                with patch.object(version_leases.platform, 'process_alive', return_value=False), patch.object(version_leases.platform, 'process_absent', return_value=False) as absent:
                    self.assertEqual(version_leases.collect(root), [])
                    absent.return_value = True
                    self.assertEqual(version_leases.collect(root), ['old'])

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
