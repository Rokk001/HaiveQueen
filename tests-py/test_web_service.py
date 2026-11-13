import http.client
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config, db, runtime, tray, web_auth, web_service


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {'BZZ_CONFIG_DIR': self.folder.name, 'BZZ_DATA_DIR': self.folder.name,
                                         'BZZ_NO_DESKTOP': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.stop_service)

    def stop_service(self):
        web_service.stop()
        deadline = time.monotonic() + 5
        while web_service.state_path().exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(web_service.state_path().exists())

    def configure(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        path = Path(self.folder.name) / 'config.toml'
        web_auth.setup(path, 'operator', 'secret', '0.0.0.0', port, '', '')
        return path

    def test_direct_serve_starts_tray_after_publishing_running_service(self):
        self.configure()
        service = web_service.Service()
        output = io.StringIO()

        def ensure_tray():
            self.assertEqual(tray.state(), 'Stopped')
            state = json.loads(web_service.state_path().read_text(encoding='utf-8'))
            self.assertEqual(state['control_port'], service.control.server_address[1])
            self.assertEqual(state['token'], service.token)
            self.assertTrue(service.status()['running'])

        def stop():
            service.stopping = True

        with patch.object(tray.loop, 'loop_running', return_value=None), \
                patch.object(tray, 'ensure_started', side_effect=ensure_tray) as ensure, \
                patch.object(service.control, 'handle_request', side_effect=stop), \
                patch('sys.stdout', output):
            self.assertEqual(service.run(), 0)
            ensure.assert_called_once_with()
        self.assertTrue(json.loads(output.getvalue())['running'])
        self.assertIsNone(service.public)
        self.assertFalse(web_service.state_path().exists())
        self.assertFalse(web_service.state_path().with_suffix('.lock').exists())

    def test_failed_direct_serve_does_not_start_tray(self):
        self.configure()
        service = web_service.Service()
        output = io.StringIO()
        with patch('haivequeen.web_server.make_server', side_effect=OSError('listener unavailable')), \
                patch.object(tray, 'ensure_started') as ensure, patch('sys.stdout', output):
            self.assertEqual(service.run(), 1)
            ensure.assert_not_called()
        self.assertIn('listener unavailable', json.loads(output.getvalue())['error'])
        self.assertIsNone(service.public)
        self.assertFalse(web_service.state_path().exists())
        self.assertFalse(web_service.state_path().with_suffix('.lock').exists())

    def test_detached_start_restart_and_live_off(self):
        path = self.configure()
        started = web_service.start()
        self.assertNotEqual(started['pid'], os.getpid())
        self.assertEqual(web_service.start()['pid'], started['pid'])
        web_auth.off(path)
        reloaded = web_service.control('reload')
        self.assertTrue(reloaded['ok'])
        self.assertEqual(reloaded['pid'], started['pid'])
        connection = http.client.HTTPConnection('127.0.0.1', web_auth.read(path)['port'], timeout=3)
        try:
            connection.request('GET', '/api/state')
            self.assertEqual(connection.getresponse().status, 401)
        finally:
            connection.close()
        # A restarted listener must be loopback only, without opening an Orchestrator.
        with patch('haivequeen.web_server.make_server', wraps=__import__('haivequeen.web_server', fromlist=['']).make_server) as factory:
            service = web_service.Service()
            service.path = path
            self.stop_service()
            try:
                self.assertTrue(service.reload()['ok'])
                self.assertEqual(service.public.server_address[0], '127.0.0.1')
                self.assertEqual(factory.call_count, 1)
            finally:
                service.close_listener()
                service.control.server_close()

    def test_occupied_port_is_returned_to_start_caller(self):
        path = self.configure()
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', web_auth.read(path)['port']))
            sock.listen()
            with self.assertRaisesRegex(OSError, 'Web server could not start'):
                web_service.start()

    def test_missing_certificate_is_returned_to_start_caller(self):
        path = self.configure()
        web_auth.setup(path, 'operator', 'secret', '127.0.0.1', web_auth.read(path)['port'], 'absent-certificate.pem', 'absent-key.pem')
        with self.assertRaisesRegex(OSError, 'absent-certificate.pem'):
            web_service.start()

    def test_agent_identity_cannot_capture_detached_service_readiness(self):
        self.configure()
        conn = db.connect(config.db_path())
        try:
            runtime.upsert_agent(conn, 'test', 'test-queen', {'phase': 'queen', 'status': 'running'})
            with patch.dict(os.environ, {'BZZ_AGENT': 'test-queen'}):
                self.assertTrue(web_service.start()['running'])
        finally:
            conn.close()

    def test_restart_closes_idle_connections_before_rebinding(self):
        path = self.configure()
        web_service.start()
        with socket.create_connection(('127.0.0.1', web_auth.read(path)['port']), timeout=3) as idle:
            # Wait for acceptance without sending a complete HTTP request.
            idle.sendall(b'GET / HTTP/1.1\r\n')
            time.sleep(0.1)
            result = web_service.restart()
            self.assertTrue(result['ok'], result)
            self.assertEqual(idle.recv(1), b'')

    def test_restart_replaces_service_process_to_load_updated_code(self):
        self.configure()
        first = web_service.start()
        restarted = web_service.restart()
        self.assertTrue(restarted['running'])
        self.assertNotEqual(restarted['pid'], first['pid'])

    def test_browser_restart_replaces_process_after_reply(self):
        path = self.configure()
        first = web_service.start()
        connection = http.client.HTTPConnection('127.0.0.1', web_auth.read(path)['port'], timeout=3)
        try:
            connection.request('POST', '/login', json.dumps({'username': 'operator', 'password': 'secret'}))
            response = connection.getresponse()
            cookie = response.getheader('Set-Cookie').split(';')[0]
            csrf = json.loads(response.read())['csrf']
            connection.request('POST', '/api/web/restart', '{}', {'Cookie': cookie, 'X-CSRF-Token': csrf})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            deadline = time.monotonic() + 12
            result = first
            while time.monotonic() < deadline:
                try:
                    result = web_service.control('status')
                except OSError:
                    result = None
                if result and result['running'] and result['pid'] != first['pid']:
                    break
                time.sleep(0.05)
            self.assertTrue(result and result['running'], result)
            self.assertNotEqual(result['pid'], first['pid'])
        finally:
            connection.close()
