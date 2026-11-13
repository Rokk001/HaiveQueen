import http.client
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import web_auth, web_server


class WebRoutesTests(unittest.TestCase):
    def test_disconnected_client_does_not_report_server_failure(self):
        handler = object.__new__(web_server._Handler)
        with patch.object(handler, 'handle_one_request', side_effect=BrokenPipeError('peer closed')):
            handler.handle()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'config.toml'
        self.server = web_server.make_server({**web_auth.read(self.path), 'port': 0})
        self.server.config_path = self.path
        self.server.api = lambda action, data: {'action': action}
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        conn.request(method, path, json.dumps(data) if data is not None else None,
                     {'Content-Type': 'application/json', **(headers or {})})
        response = conn.getresponse()
        body = response.read().decode()
        result = response.status, dict(response.getheaders()), body
        conn.close()
        return result

    def configure(self):
        web_auth.setup(self.path, 'operator', 'secret', '127.0.0.1', 8484, '', '')

    def login(self):
        status, headers, body = self.request('POST', '/login', {'username': 'operator', 'password': 'secret'})
        self.assertEqual(status, 200)
        return {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': json.loads(body)['csrf']}

    def test_first_start_exposes_setup_only(self):
        self.assertIn('Setup', self.request('GET', '/')[2])
        self.assertEqual(self.request('GET', '/api/state')[0], 401)
        self.assertEqual(self.request('POST', '/api/start', {})[0], 401)

    def test_setup_rejects_dns_rebinding_and_requires_one_time_token(self):
        self.assertEqual(self.request('GET', '/', headers={'Host': 'external.invalid'})[0], 403)
        payload = {'username': 'operator', 'password': 'secret', 'address': '127.0.0.1', 'port': 8484}
        self.assertEqual(self.request('POST', '/setup', payload)[0], 403)
        status, _, body = self.request('POST', '/setup', payload, {'X-CSRF-Token': self.server.setup_token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['url'], 'http://127.0.0.1:8484/login')
        self.assertTrue(web_auth.verify(web_auth.read(self.path), 'operator', 'secret'))
        self.assertEqual(self.request('POST', '/setup', payload, {'X-CSRF-Token': self.server.setup_token})[0], 403)

    def test_setup_refuses_invalid_types_before_writing(self):
        payload = {'username': 'operator', 'password': 'secret', 'port': True}
        self.assertEqual(self.request('POST', '/setup', payload, {'X-CSRF-Token': self.server.setup_token})[0], 400)
        self.assertFalse(self.path.exists())

    def test_authenticated_network_request_reaches_real_engine_snapshot(self):
        self.configure()
        self.server.api = self.server.engine_api
        headers = self.login()
        with patch.dict(os.environ, {'BZZ_CONFIG_DIR': self.folder.name, 'BZZ_DATA_DIR': self.folder.name}):
            status, _, body = self.request('GET', '/api/state', headers=headers)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['controls']['web'], ['Stop', 'Restart'])

    def test_authentication_applies_to_local_pages_and_actions(self):
        self.configure()
        self.assertIn('unencrypted', self.request('GET', '/login')[2])
        self.assertEqual(self.request('GET', '/')[0], 401)
        self.assertEqual(self.request('POST', '/login', {'username': 'operator', 'password': 'wrong'})[0], 401)
        headers = self.login()
        self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/start', {}, headers)[0], 200)
        self.assertEqual(self.request('POST', '/api/start', {}, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', '/api/start', {}, {**headers, 'Origin': 'http://other.test'})[0], 403)

    def test_credential_change_revokes_sessions_and_setup_cannot_replace_user(self):
        self.configure()
        headers = self.login()
        self.assertEqual(self.request('POST', '/setup', {})[0], 403)
        web_auth.setup(self.path, 'operator', 'replacement', '127.0.0.1', 8484, '', '')
        self.assertEqual(self.request('GET', '/api/state', headers=headers)[0], 401)

    def test_bad_request_does_not_execute_action(self):
        self.configure()
        headers = self.login()
        self.assertEqual(self.request('POST', '/api/start', [], headers)[0], 400)
        self.assertEqual(self.request('GET', '/api/start', headers=headers)[0], 404)

    def test_https_uses_supplied_certificate_and_secure_cookie(self):
        self.configure()
        fixtures = Path(__file__).parent / 'fixtures' / 'web-tls'
        settings = {**web_auth.read(self.path), 'port': 0,
                    'certificate': str(fixtures / 'cert.pem'), 'key': str(fixtures / 'key.pem')}
        server = web_server.make_server(settings)
        server.config_path = self.path
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        connection = http.client.HTTPSConnection(*server.server_address, context=ssl._create_unverified_context(), timeout=3)
        try:
            connection.request('GET', '/login')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertNotIn('unencrypted', response.read().decode())
            connection.request('POST', '/login', json.dumps({'username': 'operator', 'password': 'secret'}))
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('; Secure', response.getheader('Set-Cookie'))
            response.read()
        finally:
            connection.close()
            server.shutdown()
            worker.join()
            server.server_close()

    def test_idle_tls_client_cannot_block_listener_shutdown(self):
        fixtures = Path(__file__).parent / 'fixtures' / 'web-tls'
        server = web_server.make_server({**web_auth.read(self.path), 'port': 0,
                                        'certificate': str(fixtures / 'cert.pem'), 'key': str(fixtures / 'key.pem')})
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        idle = socket.create_connection(server.server_address, timeout=3)
        finished = threading.Event()
        def close():
            server.shutdown()
            finished.set()
        closer = threading.Thread(target=close, daemon=True)
        try:
            # TCP connects, but the peer never sends its TLS ClientHello.
            import time
            time.sleep(0.1)
            closer.start()
            responsive = finished.wait(timeout=2)
        finally:
            idle.close()
            closer.join(timeout=3)
            worker.join(timeout=3)
            server.server_close()
        self.assertTrue(responsive, 'TLS handshake blocked shutdown')
