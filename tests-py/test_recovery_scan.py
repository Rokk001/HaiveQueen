import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import version_leases


class RecoveryScanTests(unittest.TestCase):
    def test_unpublished_child_and_uncertainty_prevent_removal_then_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            token = version_leases.reserve(root, 'old')
            with patch.object(version_leases.process_scan, 'scan', side_effect=[True, None, False]):
                self.assertEqual(version_leases.collect(root), [])
                self.assertIn(token, version_leases._load(root))
                self.assertEqual(version_leases.collect(root), [])
                self.assertIn(token, version_leases._load(root))
                self.assertEqual(version_leases.collect(root), ['old'])
                self.assertNotIn(token, version_leases._load(root))

    def test_unleased_folder_also_requires_clear_scan_and_retries_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(version_leases.process_scan, 'scan', return_value=False), \
                 patch.object(version_leases.shutil, 'rmtree', side_effect=PermissionError):
                self.assertEqual(version_leases.collect(root), [])
            with patch.object(version_leases.process_scan, 'scan', return_value=False):
                self.assertEqual(version_leases.collect(root), ['old'])


if __name__ == '__main__':
    unittest.main()
