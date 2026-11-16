"""Known shared settings transitions need the same drain as database changes."""
import unittest

import test_release_compatibility_switch as fixtures
from release_authority_fixture import settings_break
from bzztest import RoundCase
from haivequeen import config, db, release_contract


class SettingsContractTests(unittest.TestCase):
    def fixture(self):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.target = settings_break(fixture.target)
        fixture.install()
        return fixture

    def test_changed_settings_consumers_require_a_known_drain(self):
        fixture = self.fixture()
        decision = fixture.assess()
        self.assertTrue(decision['known'], decision)
        self.assertFalse(decision['compatible'])
        self.assertTrue(decision['settings_transition'])
        self.assertIn('settings', decision['reason'])

    def test_settings_declaration_without_changed_consumers_is_rejected(self):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.target['haivequeen/shared-contract.json'] = settings_break(fixture.target)['haivequeen/shared-contract.json']
        fixture.install()
        self.assertFalse(fixture.assess()['known'])


class SettingsRecoveryTests(RoundCase):
    def test_rollback_preserves_the_latest_settings_bytes(self):
        old = config.config_dir() / 'config.toml'
        new = config.config_dir() / 'hq-machine.toml'
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b'roundMinutes = 2\n# retain comments\n')
        with db.filetxn.locked():
            release_contract.move_settings()
        self.assertFalse(old.exists())
        new.write_bytes(new.read_bytes() + b'slots = 7\n')
        with db.filetxn.locked():
            release_contract.move_settings(restore=True)
        self.assertEqual(old.read_bytes(), b'roundMinutes = 2\n# retain comments\nslots = 7\n')
        self.assertFalse(new.exists())

    def test_conflicting_settings_files_are_not_overwritten(self):
        old = config.config_dir() / 'config.toml'
        new = config.config_dir() / 'hq-machine.toml'
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b'slots = 3\n')
        new.write_bytes(b'slots = 7\n')
        with db.filetxn.locked():
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                release_contract.move_settings(restore=True)
        self.assertEqual(old.read_bytes(), b'slots = 3\n')
        self.assertEqual(new.read_bytes(), b'slots = 7\n')

    def test_partial_settings_consumer_transition_is_rejected(self):
        fixture = fixtures.VerifiedCompatibilityTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        target = settings_break(fixture.target)
        target['haivequeen/web_api.py'] = fixture.target['haivequeen/web_api.py']
        fixture.target = target
        fixture.install()
        self.assertFalse(fixture.assess()['known'])
