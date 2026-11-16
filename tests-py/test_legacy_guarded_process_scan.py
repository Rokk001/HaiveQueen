import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from haivequeen import version_leases


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics required')
class GuardedScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / 'version-leases.json'
        self.data = b' { } \n'
        self.registry.write_bytes(self.data)
        self.versions = self.root / 'versions'
        self.versions.mkdir()
        (self.versions / 'v1').mkdir()
        (self.versions / 'v2').mkdir()

    def reader(self):
        return subprocess.run([sys.executable, '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()', str(self.registry)],
            capture_output=True, timeout=10).returncode

    def test_clear_scan_holds_registry_through_body_and_releases_on_all_exits(self):
        for exceptional in (False, True):
            with self.subTest(exceptional=exceptional), mock.patch.object(version_leases.process_scan, 'scan', return_value=False) as scan:
                try:
                    with version_leases.guarded_process_scan(self.root):
                        self.assertNotEqual(self.reader(), 0)
                        self.assertEqual({p.name for (p,), _ in scan.call_args_list}, {'v1', 'v2'})
                        if exceptional:
                            raise RuntimeError('body failure')
                except RuntimeError:
                    if not exceptional:
                        raise
                self.assertEqual(self.registry.read_bytes(), self.data)
                self.assertEqual(self.reader(), 0)

    def test_observed_unknown_and_exceptional_scans_refuse(self):
        for result in (True, None, 0):
            with self.subTest(result=result), mock.patch.object(version_leases.process_scan, 'scan', return_value=result):
                with self.assertRaises((OSError, ValueError)):
                    with version_leases.guarded_process_scan(self.root):
                        self.fail('uncertain scan admitted')
        with mock.patch.object(version_leases.process_scan, 'scan', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                with version_leases.guarded_process_scan(self.root):
                    self.fail('failed scan admitted')
        self.assertEqual(self.registry.read_bytes(), self.data)
        self.assertEqual(self.reader(), 0)

    def test_invalid_file_and_inaccessible_enumeration_refuse(self):
        invalid = self.versions / 'bad name'
        invalid.mkdir()
        with mock.patch.object(version_leases.process_scan, 'scan', return_value=False):
            with self.assertRaises((OSError, ValueError)):
                with version_leases.guarded_process_scan(self.root):
                    self.fail('invalid entry admitted')
            invalid.rmdir()
            (self.versions / 'file').write_text('x')
            with self.assertRaises((OSError, ValueError)):
                with version_leases.guarded_process_scan(self.root):
                    self.fail('file admitted')
            (self.versions / 'file').unlink()
            with mock.patch('os.scandir', side_effect=PermissionError('denied')):
                with self.assertRaises(OSError):
                    with version_leases.guarded_process_scan(self.root):
                        self.fail('inaccessible directory admitted')
        self.assertEqual(self.registry.read_bytes(), self.data)
        self.assertEqual(self.reader(), 0)

    def test_missing_and_linked_versions_directory_refuse(self):
        for path in self.versions.iterdir():
            path.rmdir()
        self.versions.rmdir()
        with self.assertRaises(OSError):
            with version_leases.guarded_process_scan(self.root):
                self.fail('missing directory admitted')
        self.versions.mkdir()
        with self.assertRaises((OSError, ValueError)):
            with version_leases.guarded_process_scan(self.root):
                self.fail('empty directory admitted')
        target = self.root / 'target'
        target.mkdir()
        link = self.versions / 'v1'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
            if created.returncode:
                self.skipTest('directory link creation unavailable')
            self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        with mock.patch.object(version_leases.process_scan, 'scan', return_value=False):
            with self.assertRaises((OSError, ValueError)):
                with version_leases.guarded_process_scan(self.root):
                    self.fail('symlink admitted')


if __name__ == '__main__':
    unittest.main()
