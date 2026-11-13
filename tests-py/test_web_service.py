import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config, db, runtime, web_auth, web_service


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {'BZZ_CONFIG_DIR': self.folder.name, 'BZZ_DATA_DIR': self.folder.name})
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

    def test_detached_start_restart_and_live_off(self):
        path = self.configure()
        started = web_service.start()
        self.assertNotEqual(started['pid'], os.getpid())
        self.assertEqual(web_service.start()['pid'], started['pid'])
        web_auth.off(path)
        reloaded = web_service.restart()
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
