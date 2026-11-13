import tempfile
import unittest
from pathlib import Path

from haivequeen import web_server


class WebListenerTests(unittest.TestCase):
    def test_unconfigured_listener_is_local(self):
        settings = {'address': '0.0.0.0', 'port': 0, 'network': False,
                    'username': '', 'password': '', 'certificate': '', 'key': ''}
        server = web_server.make_server(settings)
        try:
            self.assertEqual(server.server_address[0], '127.0.0.1')
        finally:
            server.server_close()

    def test_missing_certificate_names_file(self):
        with tempfile.TemporaryDirectory() as folder:
            cert = str(Path(folder) / 'missing-cert.pem')
            settings = {'address': '127.0.0.1', 'port': 0, 'network': False,
                        'username': '', 'password': '', 'certificate': cert, 'key': 'key.pem'}
            with self.assertRaisesRegex(OSError, 'missing-cert.pem'):
                web_server.make_server(settings)

    def test_incomplete_tls_configuration_is_rejected(self):
        settings = {'address': '127.0.0.1', 'port': 0, 'network': False,
                    'username': '', 'password': '', 'certificate': '', 'key': 'key.pem'}
        with self.assertRaisesRegex(ValueError, 'certificate and key'):
            web_server.make_server(settings)


    def test_occupied_port_reports_bind_error(self):
        settings = {'address': '127.0.0.1', 'port': 0, 'network': False,
                    'username': '', 'password': '', 'certificate': '', 'key': ''}
        server = web_server.make_server(settings)
        try:
            settings['port'] = server.server_address[1]
            with self.assertRaises(OSError):
                web_server.make_server(settings)
        finally:
            server.server_close()
