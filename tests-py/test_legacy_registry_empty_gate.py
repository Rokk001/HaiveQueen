import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from haivequeen import version_leases


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics required')
class EmptyGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'version-leases.json'

    def reader(self):
        return subprocess.run([sys.executable, '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()', str(self.path)],
            capture_output=True, timeout=10)

    def test_empty_gate_holds_handle_and_releases_on_normal_and_exceptional_exit(self):
        data = b' {  } \n'
        self.path.write_bytes(data)
        for exceptional in (False, True):
            with self.subTest(exceptional=exceptional):
                try:
                    with version_leases.exclusive_empty_registry(self.root):
                        self.assertNotEqual(self.reader().returncode, 0)
                        if exceptional:
                            raise RuntimeError('body failure')
                except RuntimeError:
                    if not exceptional:
                        raise
                self.assertEqual(self.path.read_bytes(), data)
                self.assertEqual(self.reader().returncode, 0)

    def test_nonempty_including_installation_reservation_refuses_and_recovers(self):
        for data in (b'{"lease": {"version": "1"}}',
                     b'{"token": {"version": "2", "pid": null, "identity": null}}'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    with version_leases.exclusive_empty_registry(self.root):
                        self.fail('nonempty registry admitted')
                self.assertEqual(self.path.read_bytes(), data)
                self.path.write_bytes(b'{}')
                with version_leases.exclusive_empty_registry(self.root):
                    self.assertNotEqual(self.reader().returncode, 0)
                self.assertEqual(self.path.read_bytes(), b'{}')

    def test_missing_invalid_and_acquisition_failure_refuse_without_changes(self):
        with self.assertRaises(FileNotFoundError):
            with version_leases.exclusive_empty_registry(self.root):
                self.fail('missing registry admitted')
        self.assertFalse(self.path.exists())
        for data in (b'{', b'[]'):
            self.path.write_bytes(data)
            with self.assertRaises(ValueError):
                with version_leases.exclusive_empty_registry(self.root):
                    self.fail('invalid registry admitted')
            self.assertEqual(self.path.read_bytes(), data)
        self.path.write_bytes(b'{}')
        with self.path.open('rb'):
            with self.assertRaises(OSError):
                with version_leases.exclusive_empty_registry(self.root):
                    self.fail('contended registry admitted')
        self.assertEqual(self.path.read_bytes(), b'{}')
        with version_leases.exclusive_empty_registry(self.root):
            pass


class UnsupportedPlatformTests(unittest.TestCase):
    def test_unsupported_platform_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'version-leases.json'
            path.write_bytes(b'{}')
            with mock.patch.object(version_leases.os, 'name', 'unsupported'):
                with self.assertRaises(OSError):
                    with version_leases.exclusive_empty_registry(root):
                        self.fail('unsupported platform admitted')
            self.assertEqual(path.read_bytes(), b'{}')
