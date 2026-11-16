import json
import unittest
from unittest.mock import patch
from test_web_routes import WebRoutesTests
from bzztest import RoundCase
from haivequeen import config, settings, web_api, web_auth


class SecurityRoutes(WebRoutesTests):
    # Reuse the isolated listener fixture without duplicating its test cases.
    def test_login_budget_precedes_password_hashing(self):
        self.configure()
        with patch.object(web_auth, 'verify', return_value=False) as verify:
            codes = [self.request('POST', '/login', {'username': 'operator', 'password': 'bad'})[0] for _ in range(8)]
        self.assertIn(429, codes)
        self.assertLess(verify.call_count, 8)

    def test_parallel_login_hashes_are_bounded_and_slots_are_released(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.configure()
        entered = threading.Barrier(3)
        release = threading.Event()

        def verify(*args):
            entered.wait(timeout=5)
            if not release.wait(timeout=5):
                raise TimeoutError('test did not release password checks')
            return False

        with patch.object(web_auth, 'verify', side_effect=verify) as hashing:
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(self.request, 'POST', '/login',
                                       {'username': 'operator', 'password': 'wrong'}) for _ in range(2)]
                try:
                    entered.wait(timeout=5)
                    code, _, body = self.request('POST', '/login', {'username': 'operator', 'password': 'wrong'})
                    self.assertEqual(code, 429)
                    self.assertIn('Wait one minute', body)
                    self.assertEqual(hashing.call_count, 2)
                finally:
                    release.set()
                self.assertEqual([request.result()[0] for request in pending], [401, 401])
        self.assertEqual(self.server.login_inflight, 0)
        self.login()  # Normal HTTP login still works after concurrent rejected attempts.

    def test_login_budget_recovers_after_one_minute(self):
        from types import SimpleNamespace
        from haivequeen import web_server
        clock = [100.0]
        with patch.object(web_server, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
            for _ in range(5):
                self.assertTrue(self.server.begin_login())
                self.server.end_login()
            self.assertFalse(self.server.begin_login())
            clock[0] = 159.9
            self.assertFalse(self.server.begin_login())
            clock[0] = 160.0
            self.assertTrue(self.server.begin_login())
            self.server.end_login()
        self.assertEqual(self.server.login_inflight, 0)
        self.assertEqual(len(self.server.login_attempts), 1)

    def test_untrusted_forwarded_scheme_is_ignored_and_http_works(self):
        self.configure()
        configured = settings.load_raw(self.path)
        configured['web']['trustedProxies'] = ['192.168.50.2']
        settings.save_file(self.path, configured)
        self.assertTrue('unencrypted' in self.request('GET', '/login', headers={'X-Forwarded-Proto': 'https'})[2])
        self.login()

    def test_api_internal_exception_is_not_returned(self):
        self.configure()
        headers = self.login()
        self.server.api = lambda *_: (_ for _ in ()).throw(RuntimeError('private-path secret-token'))
        status, _, body = self.request('GET', '/api/state', headers=headers)
        self.assertEqual(status, 500)
        self.assertNotIn('secret-token', body)
        self.assertNotIn('private-path', body)


# Only these three regressions, not the inherited route suite.
for name in dir(WebRoutesTests):
    if name.startswith('test_'):
        setattr(SecurityRoutes, name, None)


class SecretSettings(RoundCase):
    def test_settings_redact_key_and_preserve_it_on_unrelated_save(self):
        path = config.config_dir() / 'config.toml'
        data = settings.load_raw(path)
        data['providers'] = [{'name': 'local', 'protocol': 'codex', 'command': 'codex', 'apiKey': 'private-test-key'}]
        settings.save_file(path, data)
        view = web_api.settings_read(self.conn)
        self.assertNotIn('private-test-key', json.dumps(view))
        web_api.settings_save(self.conn, {'revision': view['revision'], 'data': view['data']})
        self.assertEqual(settings.load_raw(path)['providers'][0]['apiKey'], 'private-test-key')

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows user-bound credential protection')
    def test_git_token_not_plaintext_on_disk(self):
        config.save_git_credential('github', 'private-test-token')
        self.assertNotIn(b'private-test-token', (config.config_dir() / 'git-credentials.json').read_bytes())
        self.assertEqual(config.git_credential('github')['token'], 'private-test-token')


class ProxyTrustTests(unittest.TestCase):
    def test_private_defaults_public_rejection_and_explicit_override(self):
        from email.message import Message
        from haivequeen.web_server import _Handler
        handler = object.__new__(_Handler)
        handler.connection = object()
        handler.headers = Message()
        handler.headers['X-Forwarded-Proto'] = 'https'
        handler.settings = lambda: {'trustedProxies': []}
        for address in ('127.0.0.1', '10.20.30.40', '172.16.2.3', '192.168.1.2', '::1', 'fd00::2', '169.254.2.3', 'fe80::2'):
            handler.client_address = (address, 1234)
            self.assertEqual(handler.external_scheme(), 'https', address)
        for address in ('8.8.8.8', '100.64.0.1', '192.0.2.1', '0.0.0.0', '224.0.0.1'):
            handler.client_address = (address, 1234)
            self.assertEqual(handler.external_scheme(), 'http', address)
        handler.settings = lambda: {'trustedProxies': ['192.168.1.2']}
        handler.client_address = ('192.168.1.3', 1234)
        self.assertEqual(handler.external_scheme(), 'http')
        handler.client_address = ('192.168.1.2', 1234)
        self.assertEqual(handler.external_scheme(), 'https')
        handler.headers.replace_header('X-Forwarded-Proto', 'https,http')
        self.assertEqual(handler.external_scheme(), 'http')


class SecretCompatibilityTests(unittest.TestCase):
    def test_raw_legacy_server_key_survives_masked_save(self):
        from haivequeen import web_secrets
        raw = {'servers': [{'name': 'local', 'apiKey': 'legacy-secret'}]}
        migrated = {'providers': [{'name': 'local', 'apiKey': 'legacy-secret'}]}
        result = web_secrets.restore(web_secrets.redact(raw), migrated, raw)
        self.assertEqual(result['servers'][0]['apiKey'], 'legacy-secret')

    def test_blank_key_clears_and_unknown_mask_is_rejected(self):
        from haivequeen import web_secrets
        original = {'providers': [{'name': 'local', 'apiKey': 'secret'}]}
        self.assertEqual(web_secrets.restore({'providers': [{'name': 'local', 'apiKey': ''}]}, original)['providers'][0]['apiKey'], '')
        with self.assertRaises(ValueError):
            web_secrets.restore({'providers': [{'name': 'renamed', 'apiKey': web_secrets.MASK}]}, original)

    def test_secure_atomic_write_sets_mode_before_publish(self):
        import tempfile, os
        from haivequeen import filetxn
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'credential'
            path.write_bytes(b'old')
            events = []
            replace = os.replace
            with patch.object(filetxn.os, 'chmod', side_effect=lambda p, mode: events.append(('chmod', mode))), patch.object(filetxn.os, 'replace', side_effect=lambda a,b: (events.append(('replace', None)), replace(a,b))[-1]):
                filetxn.atomic_write(path, b'new', mode=0o600)
            self.assertEqual(events, [('chmod', 0o600), ('replace', None)])
