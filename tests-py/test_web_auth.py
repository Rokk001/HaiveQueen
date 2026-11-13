import tempfile
import unittest
from pathlib import Path

from haivequeen import web_auth


class WebAuthTest(unittest.TestCase):
    def test_setup_hashes_password_and_replaces_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            web_auth.setup(path, 'user1', 'first password', '0.0.0.0', 8484, '', '')
            self.assertNotIn('first password', path.read_text(encoding='utf-8'))
            settings = web_auth.read(path)
            self.assertTrue(web_auth.verify(settings, 'user1', 'first password'))
            self.assertFalse(web_auth.verify(settings, 'user1', 'wrong'))
            web_auth.setup(path, 'user2', 'second password', '127.0.0.1', 9000, '', '')
            settings = web_auth.read(path)
            self.assertFalse(web_auth.verify(settings, 'user1', 'first password'))
            self.assertTrue(web_auth.verify(settings, 'user2', 'second password'))
            self.assertEqual(settings['port'], 9000)
            self.assertTrue(settings['network'])

    def test_unconfigured_and_off_bind_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            self.assertEqual(web_auth.bind_address(web_auth.read(path)), '127.0.0.1')
            web_auth.setup(path, 'user1', 'password', '0.0.0.0', 8484, '', '')
            web_auth.off(path)
            settings = web_auth.read(path)
            self.assertEqual(web_auth.bind_address(settings), '127.0.0.1')
            self.assertTrue(web_auth.verify(settings, 'user1', 'password'))

    def test_invalid_setup_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            web_auth.setup(path, 'user1', 'password', '0.0.0.0', 8484, '', '')
            before = path.read_bytes()
            for values in [('', 'password', 8484, '', ''), ('user1', '', 8484, '', ''),
                           ('user1', 'password', 0, '', ''), ('user1', 'password', 8484, 'cert', '')]:
                user, password, port, cert, key = values
                with self.assertRaises(ValueError):
                    web_auth.setup(path, user, password, '0.0.0.0', port, cert, key)
                self.assertEqual(path.read_bytes(), before)
