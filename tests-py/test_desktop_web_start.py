from unittest import mock
from bzztest import RoundCase
from haivequeen import cli, loop, web_service

class DesktopWebStartTests(RoundCase):
    def test_background_start_opens_web_interface(self):
        args = cli.build_parser().parse_args(['start'])
        with mock.patch.object(web_service, 'start', return_value={'url': 'http://127.0.0.1:8484'}), mock.patch.object(web_service, 'open_browser', return_value={'web': 'http://127.0.0.1:8484'}) as browser, mock.patch.object(loop, 'loop_running', return_value=123), mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_start(args, self.conn), 0)
        browser.assert_called_once_with()

    def test_gui_opens_web_interface(self):
        args = cli.build_parser().parse_args(['gui'])
        with mock.patch.object(web_service, 'open_browser', return_value={'web': 'http://127.0.0.1:8484'}) as browser, mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_gui(args, self.conn), 0)
        browser.assert_called_once_with()


    def test_running_loop_reports_browser_failure(self):
        args = cli.build_parser().parse_args(['start'])
        with mock.patch.object(web_service, 'start'), mock.patch.object(web_service, 'open_browser', side_effect=OSError('browser unavailable')), mock.patch.object(loop, 'loop_running', return_value=123), mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_start(args, self.conn), 1)
        self.assertTrue(args.desktop_result['running'])
        self.assertIn('browser unavailable', args.desktop_result['error'])

    def test_browser_failure_is_reported_when_no_handler_opens_url(self):
        with mock.patch.object(web_service, 'start', return_value={'url': 'http://localhost'}), mock.patch('webbrowser.open', return_value=False):
            with self.assertRaisesRegex(OSError, 'Could not open'):
                web_service.open_browser()

    def test_server_start_failure_is_reported_without_starting_loop(self):
        args = cli.build_parser().parse_args(['start'])
        with mock.patch.object(web_service, 'start', side_effect=OSError('server unavailable')), mock.patch.object(loop, 'loop_running', return_value=None), mock.patch.object(loop, 'spawn_detached') as spawn, mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_start(args, self.conn), 1)
        self.assertIn('server unavailable', args.desktop_result['error'])
        spawn.assert_not_called()
