from unittest import mock

from bzztest import RoundCase
from haivequeen import cli, loop, web_service


class WebStartTests(RoundCase):
    def test_foreground_starts_web_without_browser(self):
        args = cli.build_parser().parse_args(['start', '--foreground', '--rounds', '1'])
        with mock.patch.object(web_service, 'start') as start, mock.patch.object(web_service, 'open_browser') as browser, mock.patch.object(loop, 'run_loop', return_value=0):
            self.assertEqual(loop.cmd_start(args, self.conn), 0)
        start.assert_called_once()
        browser.assert_not_called()

    def test_web_bind_error_prevents_background_start_and_reports_reason(self):
        args = cli.build_parser().parse_args(['start'])
        with mock.patch.object(web_service, 'start', side_effect=OSError('port is occupied')), mock.patch.object(loop, 'spawn_detached') as spawn, mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_start(args, self.conn), 1)
        spawn.assert_not_called()
        self.assertIn('port is occupied', args.desktop_result['error'])

    def test_gui_reports_web_error(self):
        args = cli.build_parser().parse_args(['gui'])
        with mock.patch.object(web_service, 'start', side_effect=OSError('certificate file missing')), mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_gui(args, self.conn), 1)
        self.assertIn('certificate file missing', args.desktop_result['error'])

    def test_graceful_stop_leaves_web_service_running(self):
        args = cli.build_parser().parse_args(['stop'])
        with mock.patch.object(web_service, 'stop') as stop, mock.patch('builtins.print'):
            self.assertEqual(loop.cmd_stop(args, self.conn), 0)
        stop.assert_not_called()
