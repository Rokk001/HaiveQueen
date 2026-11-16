import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from haivequeen import version_leases


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics required')
class ExclusiveSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'version-leases.json'

    def test_valid_snapshot_holds_exclusive_handle_and_preserves_bytes(self):
        data = b'{"lease": {"version": "1"}}'
        self.path.write_bytes(data)
        with version_leases.exclusive_registry_snapshot(self.root) as value:
            self.assertEqual(value, json.loads(data))
            with self.assertRaises(OSError):
                version_leases._load(self.root)
            self.assertEqual(self.path.stat().st_size, len(data))
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(version_leases._load(self.root), json.loads(data))

    def test_existing_reader_denies_acquisition_and_recovers(self):
        data = b'{"a": 1}'
        self.path.write_bytes(data)
        with self.path.open('rb'):
            with self.assertRaises(OSError):
                with version_leases.exclusive_registry_snapshot(self.root):
                    self.fail('exclusive acquisition succeeded')
        self.assertEqual(self.path.read_bytes(), data)
        with version_leases.exclusive_registry_snapshot(self.root) as value:
            self.assertEqual(value, {'a': 1})

    def test_invalid_json_closes_handle_and_preserves_bytes(self):
        for data in (b'{', b'[1]'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    with version_leases.exclusive_registry_snapshot(self.root):
                        self.fail('invalid registry accepted')
                self.assertEqual(self.path.read_bytes(), data)
                with self.path.open('rb') as reader:
                    self.assertEqual(reader.read(), data)

    def test_missing_file_fails_without_creation(self):
        with self.assertRaises(FileNotFoundError):
            with version_leases.exclusive_registry_snapshot(self.root):
                self.fail('missing registry accepted')
        self.assertFalse(self.path.exists())


class UnsupportedPlatformTests(unittest.TestCase):
    def test_fails_closed_without_opening_registry(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'version-leases.json'
            path.write_bytes(b'{}')
            with mock.patch.object(version_leases.os, 'name', 'unsupported'):
                with self.assertRaises(OSError):
                    with version_leases.exclusive_registry_snapshot(root):
                        self.fail('unsupported platform accepted')
            self.assertEqual(path.read_bytes(), b'{}')
