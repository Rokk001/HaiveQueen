import json
from pathlib import Path
import tempfile
import unittest

import isolation  # noqa: F401
from haivequeen import release_staging, version_layout
from release_authority_fixture import authority, package_files, responses, database_break


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
        self.target['pyproject.toml'] = self.target['pyproject.toml'].replace(b'tomlkit==0.15.2', b'tomlkit>=2,<3')
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

    def test_authenticated_database_consumer_break_is_known_incompatible(self):
        self.target = database_break(self.target)
        self.install()
        result = self.assess()
        self.assertTrue(result['known'], result)
        self.assertFalse(result['compatible'], result)
        self.assertIn('providers_usage.actions becomes actions_json', result['reason'])
        self.assertEqual(result['affected_versions'], ['1.0.0'])

    def test_declared_database_break_without_migration_is_unknown(self):
        self.target['haivequeen/shared-contract.json'] = b'{"format":1,"database":"usage-actions-json-v2"}\n'
        self.install()
        self.assertFalse(self.assess()['known'])

    def test_database_migration_with_missing_support_is_unknown(self):
        self.target = database_break(self.target)
        self.target.pop('haivequeen/shared-contract.json')
        self.install()
        self.assertFalse(self.assess()['known'])

    def test_unmodeled_consumer_or_schema_change_is_unknown(self):
        self.target = database_break(self.target)
        self.target['haivequeen/runtime.py'] += b'\nUNKNOWN_CONSUMER = True\n'
        self.install()
        self.assertFalse(self.assess()['known'])

    def test_database_break_requires_actual_schema_and_consumers(self):
        self.target = database_break(self.target)
        self.target['schema.sql'] = self.old['schema.sql']
        self.install()
        self.assertFalse(self.assess()['known'])


class DependencyEnvironmentTests(unittest.TestCase):
    def test_distributed_pin_matches_installed_dependency(self):
        import tomllib
        from unittest.mock import patch
        from haivequeen import release_contract
        project = tomllib.loads((Path(__file__).resolve().parent.parent / 'pyproject.toml').read_text(encoding='utf-8'))
        with patch.object(release_contract.importlib.metadata, 'version', return_value='0.15.2'):
            self.assertTrue(release_contract._environment(project))
        for version in ('0.15.1', '0.15.3', '1.0.0'):
            with self.subTest(version=version), patch.object(release_contract.importlib.metadata, 'version', return_value=version):
                self.assertFalse(release_contract._environment(project))
