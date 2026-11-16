import json
from pathlib import Path
import tempfile
import unittest

import isolation  # noqa: F401
from haivequeen import release_staging, version_layout
from release_authority_fixture import authority, package_files, responses


class VerifiedCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = package_files(Path(__file__).resolve().parent.parent)
        self.old = dict(self.files, **{'haivequeen/release_message.py': b'MESSAGE = "old"\n'})
        self.target = dict(self.files, **{'haivequeen/release_message.py': b'MESSAGE = "new"\n'})
        self.reply = {}

    def fetch(self, url):
        return self.reply['/' + url.split('/repos/', 1)[1].split('/', 2)[2]]

    def install(self):
        self.reply.update(responses({'1.0.0': authority(self.old, '1.0.0'),
                                     '2.0.0': authority(self.target, '2.0.0')}))
        for version in ('1.0.0', '2.0.0'):
            release_staging.stage_release(self.root, 'example/project', version, self.fetch)
        version_layout.install_launcher(self.root)
        version_layout.set_active(self.root, '1.0.0')

    def assess(self):
        from haivequeen import release_contract
        from haivequeen.stable_launcher import lifetime_lock
        authority = release_contract.prepare(self.root, 'example/project', fetch=self.fetch)
        with lifetime_lock(self.root):
            return release_contract.assess(self.root, '2.0.0', '1.0.0', authority)

    def test_verified_nonshared_payload_can_differ(self):
        self.install()
        result = self.assess()
        self.assertTrue(result['compatible'], result)
        self.assertEqual(set(result['evidence']), {'1.0.0', '2.0.0'})
        self.assertNotEqual(result['evidence']['1.0.0']['commit'], result['evidence']['2.0.0']['commit'])

    def test_changed_shared_consumer_is_unknown(self):
        self.target['haivequeen/db.py'] += b'\nSHARED_CHANGE = True\n'
        self.install()
        result = self.assess()
        self.assertFalse(result['compatible'])
        self.assertIn('shared', result['reason'])

    def test_inert_module_cannot_hide_shared_access(self):
        self.target['haivequeen/release_message.py'] = b'import sqlite3\n'
        self.install()
        self.assertFalse(self.assess()['compatible'])

    def test_referenced_constants_are_part_of_shared_projection(self):
        for files in (self.old, self.target):
            files['haivequeen/db.py'] += b'\nfrom .release_message import MESSAGE\n'
        self.install()
        self.assertFalse(self.assess()['compatible'])

    def test_missing_dependency_contract_is_unknown(self):
        self.target.pop('pyproject.toml')
        self.install()
        self.assertFalse(self.assess()['compatible'])

    def test_changed_dependency_contract_is_unknown(self):
        self.target['pyproject.toml'] = self.target['pyproject.toml'].replace(b'tomlkit>=0.13,<1', b'tomlkit>=2,<3')
        self.install()
        self.assertFalse(self.assess()['compatible'])

    def test_forged_markers_and_release_body_do_not_authorize(self):
        self.install()
        marker = self.root / 'versions/1.0.0/haivequeen-update.json'
        marker.write_text('{"compatible_with_older":true}')
        self.assertFalse(self.assess()['compatible'])

    def test_changed_target_and_active_pointer_are_rejected(self):
        self.install()
        version_layout.set_active(self.root, '2.0.0')
        self.assertFalse(self.assess()['compatible'])
        version_layout.set_active(self.root, '1.0.0')
        (self.root / 'versions/2.0.0/haivequeen/db.py').write_text('changed')
        self.assertFalse(self.assess()['compatible'])

    def test_unverified_extra_version_or_reservation_is_unknown(self):
        self.install()
        from haivequeen import version_leases
        version_leases._save(self.root, {'pending': {'version': '3.0.0', 'pid': None}})
        self.assertFalse(self.assess()['compatible'])
        version_leases._save(self.root, {})
        (self.root / 'versions/3.0.0').mkdir()
        self.assertFalse(self.assess()['compatible'])

    def test_unknown_fixed_launcher_is_rejected(self):
        self.install()
        (self.root / 'launcher.py').write_text('unknown')
        self.assertFalse(self.assess()['compatible'])

    def test_runtime_cache_must_match_authenticated_source(self):
        import py_compile
        self.install()
        source = self.root / 'versions/1.0.0/haivequeen/db.py'
        cache = Path(py_compile.compile(str(source), doraise=True))
        self.assertTrue(self.assess()['compatible'])
        cache.write_bytes(cache.read_bytes()[:16] + b'forged')
        self.assertFalse(self.assess()['compatible'])
