import http.client
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import settings, web_auth, web_service


class ListenerRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {'BZZ_DATA_DIR': folder.name, 'BZZ_CONFIG_DIR': folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.path = Path(folder.name) / 'config.toml'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        web_auth.setup(self.path, 'operator', 'secret', '127.0.0.1', port, '', '')
        self.service = web_service.Service()
        self.addCleanup(self.service.control.server_close)
        self.addCleanup(self.service.close_listener)
        self.assertTrue(self.service.reload()['ok'])
        self.original = web_auth.read(self.path)

    def change(self, **web):
        data = settings.load_raw(self.path)
        data['web'].update(web)
        data['custom_preserved'] = 'keep'
        settings.save_file(self.path, data)

    def reachable(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.original['port'], timeout=3)
        try:
            connection.request('GET', '/login')
            self.assertEqual(connection.getresponse().status, 200)
        finally:
            connection.close()

    def test_occupied_new_port_preserves_listener_and_restores_web_settings(self):
        original_listener = self.service.public
        with socket.socket() as occupied:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            self.change(port=occupied.getsockname()[1])
            result = self.service.reload()
        self.assertFalse(result['ok'])
        self.assertTrue(result['running'])
        self.assertIs(self.service.public, original_listener)
        self.assertEqual(web_auth.read(self.path), self.original)
        self.assertEqual(settings.load_raw(self.path)['custom_preserved'], 'keep')
        with patch.object(self.service.public, 'engine_api', return_value={'plans': []}):
            self.assertIn('Previous web settings restored', self.service.public.api('state', {})['web_listener_error'])
        self.reachable()

    def test_failed_same_port_address_restores_old_listener(self):
        self.change(address='256.256.256.256')
        result = self.service.reload()
        self.assertFalse(result['ok'])
        self.assertTrue(result['running'])
        self.assertEqual(web_auth.read(self.path), self.original)
        self.reachable()

    def test_invalid_tls_keeps_existing_listener(self):
        listener = self.service.public
        self.change(certificate='missing.pem', key='missing.key')
        self.assertFalse(self.service.reload()['ok'])
        self.assertIs(self.service.public, listener)
        self.assertEqual(web_auth.read(self.path), self.original)
        self.reachable()

    def test_successful_port_change_updates_listener_and_configuration(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            new_port = sock.getsockname()[1]
        self.change(port=new_port)
        self.assertTrue(self.service.reload()['ok'])
        self.assertEqual(self.service.public.server_address[1], new_port)
        self.assertEqual(web_auth.read(self.path)['port'], new_port)
