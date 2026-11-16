from pathlib import Path
import unittest
from unittest.mock import patch

import test_release_compatibility_switch as fixtures
from haivequeen import release_contract, version_layout, version_leases
from haivequeen.stable_launcher import lifetime_lock


class PublicationRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerifiedCompatibilityTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install()
        self.root = self.fixture.root
        self.authority = release_contract.prepare(self.root, 'example/project', fetch=self.fixture.fetch)
        self.assertTrue(self.authority)

    def assess(self):
        # No remote request is permitted while supported old launches wait for
        # this lock. All authority has already been resolved.
        with patch('haivequeen.release_staging._fetch', side_effect=AssertionError('network under publication lock')):
            with lifetime_lock(self.root):
                return release_contract.assess(self.root, '2.0.0', '1.0.0', self.authority)

    def test_local_assessment_needs_no_network(self):
        self.assertTrue(self.assess()['compatible'])

    def test_target_identity_change_after_resolution_is_rejected(self):
        # This remains inert and unreferenced, so shared projection equality
        # alone cannot detect replacement of the authenticated release.
        (self.root / 'versions/2.0.0/haivequeen/release_message.py').write_text('MESSAGE = "replaced"\n')
        self.assertFalse(self.assess()['compatible'])

    def test_pointer_change_after_resolution_is_rejected(self):
        version_layout.set_active(self.root, '2.0.0')
        self.assertFalse(self.assess()['compatible'])

    def test_new_installed_version_after_resolution_is_rejected(self):
        (self.root / 'versions/3.0.0').mkdir()
        self.assertFalse(self.assess()['compatible'])

    def test_unknown_reservation_after_resolution_is_rejected(self):
        version_leases._save(self.root, {'queued': {'version': '3.0.0', 'pid': None}})
        self.assertFalse(self.assess()['compatible'])

    def test_supported_old_reservation_after_resolution_is_allowed(self):
        token = version_leases.reserve(self.root, '1.0.0')
        self.assertTrue(self.assess()['compatible'])
        self.assertEqual(version_leases._load(self.root)[token]['version'], '1.0.0')
