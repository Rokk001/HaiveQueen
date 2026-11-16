from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import isolation  # noqa: F401

from haivequeen import db, release_activation, release_staging, selfupdate, version_layout, version_leases
from test_release_staging import archive, authenticated_fetch


class ReservationOwnershipTests(unittest.TestCase):
    def test_staging_activation_consumes_only_owned_token(self):
        with tempfile.TemporaryDirectory() as root:
            data = archive([('project/haivequeen/cli.py', 'code')])
            fetch = authenticated_fetch(data)
            old, old_token = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
            version_layout.set_active(root, '1.0.0', installation_token=old_token)
            target, owned = release_staging.stage_release(root, 'example/project', '2.0.0', fetch, return_token=True)
            leases = version_leases._load(root)
            other = 'install-other'
            leases[other] = dict(leases[owned])
            version_leases._save(root, leases)
            self.assertTrue(release_activation.activate(root, '2.0.0', lambda: True, installation_token=owned, target_verifier=lambda: True))
            leases = version_leases._load(root)
            self.assertNotIn(owned, leases)
            self.assertIn(other, leases)
            self.assertEqual(Path(root, 'active-version').read_text().strip(), '2.0.0')

    def test_invalid_token_cannot_publish_or_consume(self):
        with tempfile.TemporaryDirectory() as root:
            data = archive([('project/haivequeen/cli.py', 'code')])
            fetch = authenticated_fetch(data)
            _, old = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
            version_layout.set_active(root, '1.0.0', installation_token=old)
            _, owned = release_staging.stage_release(root, 'example/project', '2.0.0', fetch, return_token=True)
            with self.assertRaises(ValueError):
                release_activation.activate(root, '2.0.0', lambda: True, installation_token='invalid', target_verifier=lambda: True)
            self.assertIn(owned, version_leases._load(root))
            self.assertEqual(Path(root, 'active-version').read_text().strip(), '1.0.0')


    def test_failed_first_round_rolls_back_and_keeps_unrelated_reservation(self):
        with tempfile.TemporaryDirectory() as root:
            data = archive([('project/haivequeen/cli.py', 'code')])
            fetch = authenticated_fetch(data)
            _, old = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
            version_layout.set_active(root, '1.0.0', installation_token=old)
            _, owned = release_staging.stage_release(root, 'example/project', '2.0.0', fetch, return_token=True)
            leases = version_leases._load(root)
            leases['install-other'] = dict(leases[owned])
            version_leases._save(root, leases)
            self.assertFalse(release_activation.activate(root, '2.0.0', lambda: False, installation_token=owned, target_verifier=lambda: True))
            self.assertEqual(Path(root, 'active-version').read_text().strip(), '1.0.0')
            leases = version_leases._load(root)
            self.assertNotIn(owned, leases)
            self.assertIn('install-other', leases)

    def test_rejection_cleans_only_owned_real_staging_reservation(self):
        for raising in (False, True):
            with self.subTest(raising=raising), tempfile.TemporaryDirectory() as root:
                data = archive([('project/haivequeen/cli.py', 'code')])
                fetch = authenticated_fetch(data)
                _, old = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
                version_layout.set_active(root, '1.0.0', installation_token=old)
                target, owned = release_staging.stage_release(root, 'example/project', '2.0.0', fetch, return_token=True)
                candidate = target / 'haivequeen' / 'cli.py'
                expected = candidate.read_bytes()
                candidate.write_bytes(b'mutated')
                leases = version_leases._load(root)
                leases['install-other'] = dict(leases[owned])
                version_leases._save(root, leases)
                def verify():
                    if raising:
                        raise ValueError('verifier failed')
                    return candidate.read_bytes() == expected
                with self.assertRaises((RuntimeError, ValueError)):
                    release_activation.activate(root, '2.0.0', lambda: self.fail('published'), installation_token=owned, target_verifier=verify)
                leases = version_leases._load(root)
                self.assertNotIn(owned, leases)
                self.assertIn('install-other', leases)
                self.assertTrue(target.exists())
                self.assertEqual(Path(root, 'active-version').read_text().strip(), '1.0.0')

    def test_rejection_retains_invalid_foreign_and_uncertain_reservations(self):
        for field, value in [('installation', False), ('version', '1.0.0'), ('installer_pid', -1), ('installer_identity', ''), ('installer_identity', 'foreign')]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as root:
                data = archive([('project/haivequeen/cli.py', 'code')])
                fetch = authenticated_fetch(data)
                _, old = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
                version_layout.set_active(root, '1.0.0', installation_token=old)
                _, owned = release_staging.stage_release(root, 'example/project', '2.0.0', fetch, return_token=True)
                leases = version_leases._load(root)
                leases[owned][field] = value
                version_leases._save(root, leases)
                with self.assertRaises(RuntimeError):
                    release_activation.activate(root, '2.0.0', lambda: self.fail('published'), installation_token=owned, target_verifier=lambda: False)
                self.assertIn(owned, version_leases._load(root))
                self.assertEqual(Path(root, 'active-version').read_text().strip(), '1.0.0')

    def test_worker_carries_real_staging_token_to_activation(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'BZZ_DATA_DIR': temp}):
            root = Path(temp, 'install')
            data = archive([('project/haivequeen/cli.py', 'code')])
            fetch = authenticated_fetch(data)
            _, old = release_staging.stage_release(root, 'example/project', '1.0.0', fetch, return_token=True)
            version_layout.set_active(root, '1.0.0', installation_token=old)
            stage = release_staging.stage_release
            def staging(root, repo, version, return_token=False):
                return stage(root, 'example/project', version, fetch, return_token=return_token)
            conn = db.connect(Path(temp, 'test.db'))
            try:
                with patch.object(release_staging, 'stage_release', side_effect=staging), \
                     patch.object(release_staging, 'verify_installed', return_value=True):
                    result = selfupdate.run_release_update(conn, root, '2.0.0', jobs=lambda c: [],
                        loop_pid=lambda: 0, start=lambda: True, receipt=lambda *a, **k: True)
                self.assertEqual(result['result'], 'ok', result)
                self.assertEqual(version_leases._load(root), {})
            finally:
                conn.close()
