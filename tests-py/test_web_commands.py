import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import cli, web_auth


class WebCommandTests(unittest.TestCase):
    def test_setup_and_off_use_config_and_hide_password(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('haivequeen.config.config_dir', return_value=Path(folder)), \
             patch('builtins.input', side_effect=['alice', '', '', '', '']), \
             patch('getpass.getpass', side_effect=['secret', 'secret']):
            args = cli.build_parser().parse_args(['web', 'setup'])
            self.assertEqual(args.fn(args, None), 0)
            path = Path(folder) / 'config.toml'
            settings = web_auth.read(path)
            self.assertTrue(web_auth.verify(settings, 'alice', 'secret'))
            self.assertNotIn('secret', path.read_text())
            args = cli.build_parser().parse_args(['web', 'off'])
            self.assertEqual(args.fn(args, None), 0)
            self.assertEqual(web_auth.bind_address(web_auth.read(path)), '127.0.0.1')

    def test_password_mismatch_does_not_write(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('haivequeen.config.config_dir', return_value=Path(folder)), \
             patch('builtins.input', side_effect=['alice']), \
             patch('getpass.getpass', side_effect=['secret', 'different']):
            args = cli.build_parser().parse_args(['web', 'setup'])
            self.assertEqual(args.fn(args, None), 1)
            self.assertFalse((Path(folder) / 'config.toml').exists())
