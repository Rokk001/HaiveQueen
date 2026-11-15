import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import version_leases


class LiveVersionSnapshotTests(unittest.TestCase):
    def test_surviving_child_and_unresolved_reservation_are_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('old', 'new'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('new')
            version_leases._save(root, {'interrupted': {'version': 'old', 'pid': None, 'identity': None}})
            with patch.object(version_leases.process_scan, 'scan', side_effect=lambda path: path.name == 'old'):
                snapshot = version_leases.live_snapshot(root)
            self.assertIn('old', snapshot['versions'])
            self.assertIn('interrupted', snapshot['unresolved'])
            self.assertFalse(snapshot['complete'])

    def test_clear_scan_after_launcher_crash_and_uncertain_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'versions' / 'old').mkdir(parents=True)
            (root / 'active-version').write_text('old')
            version_leases._save(root, {'interrupted': {'version': 'old', 'pid': None, 'identity': None}})
            with patch.object(version_leases.process_scan, 'scan', return_value=False):
                snapshot = version_leases.live_snapshot(root)
            self.assertNotIn('interrupted', snapshot['unresolved'])
            with patch.object(version_leases.process_scan, 'scan', return_value=None):
                snapshot = version_leases.live_snapshot(root)
            self.assertIn('old', snapshot['uncertain'])
            self.assertFalse(snapshot['complete'])

    def test_live_lease_and_missing_version_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'versions' / 'new').mkdir(parents=True)
            (root / 'active-version').write_text('new')
            version_leases._save(root, {'running': {'version': 'old', 'pid': 123, 'identity': 'birth'}})
            with patch.object(version_leases.process_scan, 'scan', return_value=False), \
                    patch.object(version_leases.platform, 'process_identity', return_value='birth'):
                snapshot = version_leases.live_snapshot(root)
            self.assertIn('old', snapshot['versions'])
            self.assertIn('old', snapshot['uncertain'])
            self.assertFalse(snapshot['complete'])


if __name__ == '__main__':
    unittest.main()
