import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from haivequeen import cli, web_auth


class WebCommandTests(unittest.TestCase):
    def test_setup_and_off_use_config_and_hide_password(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('haivequeen.config.config_dir', return_value=Path(folder)), \
             patch('builtins.input', side_effect=['user', '', '', '', '']), \
             patch('getpass.getpass', side_effect=['secret', 'secret']), \
             patch('haivequeen.web_service.control', return_value=None) as control:
            args = cli.build_parser().parse_args(['web', 'setup'])
            self.assertEqual(args.fn(args, None), 0)
            path = Path(folder) / 'config.toml'
            settings = web_auth.read(path)
            self.assertTrue(web_auth.verify(settings, 'user', 'secret'))
            self.assertNotIn('secret', path.read_text())
            args = cli.build_parser().parse_args(['web', 'off'])
            self.assertEqual(args.fn(args, None), 0)
            self.assertEqual(web_auth.bind_address(web_auth.read(path)), '127.0.0.1')
            self.assertEqual(control.call_args_list, [call('reload'), call('reload')])

    def test_password_mismatch_does_not_write(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch('haivequeen.config.config_dir', return_value=Path(folder)), \
             patch('builtins.input', side_effect=['user']), \
             patch('getpass.getpass', side_effect=['secret', 'different']), \
             patch('haivequeen.web_service.control') as control:
            args = cli.build_parser().parse_args(['web', 'setup'])
            self.assertEqual(args.fn(args, None), 1)
            self.assertFalse((Path(folder) / 'config.toml').exists())
            control.assert_not_called()

    def test_lifecycle_commands_use_independent_service(self):
        for action in ('start', 'stop', 'restart'):
            args = cli.build_parser().parse_args(['web', action])
            with self.subTest(action=action), patch('haivequeen.web_service.' + action, return_value={'ok': True}) as service, patch('builtins.print'):
                self.assertEqual(args.fn(args, None), 0)
                service.assert_called_once_with()

    def test_start_reports_listener_error_to_caller(self):
        args = cli.build_parser().parse_args(['web', 'start'])
        with patch('haivequeen.web_service.start', side_effect=OSError('port occupied')), patch('builtins.print') as output:
            self.assertEqual(args.fn(args, None), 1)
        self.assertIn('port occupied', output.call_args.args[0])
