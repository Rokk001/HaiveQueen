"""Verify shortcut feedback and graceful start/stop without live processes."""
import contextlib
import io
import json
import unittest
from unittest import mock

from bzztest import RoundCase
from haivequeen import cli, desktop, loop, runstate, runtime


# Keep every command inside the disposable runtime with mocked UI.
class DesktopCommandsTest(RoundCase):
    """Keep every command inside the disposable runtime with mocked UI."""

    def test_desktop_language_regions_and_fallback(self):
        for language in ('German', 'Deutsch', 'de', 'de-CH', 'de_AT'):
            self.assertEqual(desktop.result_text('start', {'ok': True}, language), 'HQ wurde gestartet.')
        for language in ('French', 'en-US', ''):
            self.assertEqual(desktop.result_text('start', {'ok': True}, language), 'HQ has started.')

    # Run a CLI command and return its exit code and JSON output.
    def invoke(self, *arguments):
        """Run a CLI command and return its exit code and JSON output."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(list(arguments))
        return code, json.loads(output.getvalue())

    # Stop waits for the running agents and shows feedback without killing them.
    def test_desktop_stop_keeps_running_agents(self):
        """Stop waits for the running agents and shows feedback without killing them."""
        self.agent('busy', status='running', started=1)
        with mock.patch.object(loop, 'loop_running', return_value=4321), \
                mock.patch.object(loop.osplat, 'kill_process_tree') as kill, \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('stop', '--desktop')
        self.assertEqual(code, 0)
        self.assertTrue(loop.stop_flag().exists())
        self.assertEqual(result['running_agents'], 1)
        self.assertEqual(runtime.list_agents(self.conn, 'testproj')[0]['status'], 'running')
        kill.assert_not_called()
        show.assert_called_once_with('stop', result)

    # Automation gets the existing JSON result and no desktop UI.
    def test_ordinary_stop_keeps_json_without_dialog(self):
        """Automation gets the existing JSON result and no desktop UI."""
        with mock.patch.object(loop, 'loop_running', return_value=4321), \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('stop')
        self.assertEqual((code, result), (0, {'ok': True, 'running_agents': 0}))
        show.assert_not_called()

    # Start retains the requested stop until the old loop finishes.
    def test_start_during_stop_reports_wait_without_resuming(self):
        """Start retains the requested stop until the old loop finishes."""
        self.agent('busy', status='running', started=1)
        loop.stop_flag().write_text('stop requested\n', encoding='utf-8')
        runstate.pause(self.conn, 'test')
        with mock.patch.object(loop, 'loop_running', return_value=4321), \
                mock.patch.object(loop, 'spawn_detached') as spawn, \
                mock.patch.object(loop, 'run_loop') as run, \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('start', '--desktop')
        self.assertEqual(code, 1)   # a refused start is a failure for the caller
        self.assertFalse(result['ok'])
        self.assertTrue(result['stopping'])
        self.assertEqual(result['running_agents'], 1)
        self.assertEqual(loop.stop_flag().read_text(encoding='utf-8'), 'stop requested\n')
        self.assertEqual(runstate.get_state(self.conn)['state'], 'PAUSED')
        spawn.assert_not_called()
        run.assert_not_called()
        show.assert_called_once_with('start', result)

    # Start opens the window without starting another loop.
    def test_existing_loop_opens_the_window_without_spawning(self):
        """Start opens the dashboard window once without starting another loop."""
        with mock.patch.object(loop, 'loop_running', return_value=4321), \
                mock.patch('haivequeen.gui.launch_detached', return_value={'ok': True, 'gui': 77, 'already': False}) as window, \
                mock.patch.object(loop, 'spawn_detached') as spawn, \
                mock.patch.object(loop, 'run_loop') as run, \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('start', '--desktop')
        self.assertEqual(code, 0)
        self.assertEqual(result['gui'], 77)
        window.assert_called_once()
        spawn.assert_not_called()
        run.assert_not_called()
        show.assert_called_once_with('start', result)

    # A failing command produces both JSON failure and desktop feedback.
    def test_desktop_command_error_is_visible(self):
        """A failing command produces both JSON failure and desktop feedback."""
        with mock.patch.object(loop, 'loop_running', side_effect=RuntimeError('terminal unavailable')), \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('start', '--desktop')
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])
        self.assertIn('terminal unavailable', str(result))
        show.assert_called_once_with('start', {'ok': False, 'error': 'terminal unavailable'})

    # Connection failure is visible even before a command can run.
    def test_desktop_database_error_is_visible(self):
        """Connection failure is visible even before a command can run."""
        with mock.patch.object(cli.db, 'connect', side_effect=OSError('database unavailable')), \
                mock.patch.object(desktop, 'show_result') as show:
            code, result = self.invoke('stop', '--desktop')
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])
        show.assert_called_once_with('stop', {'ok': False, 'error': 'database unavailable'})

    # Reject foreground mode before a desktop shortcut can run a blocking loop.
    def test_foreground_desktop_combination_reports_error(self):
        """Unsupported foreground desktop calls fail visibly rather than after exit."""
        with mock.patch.object(desktop, 'show_result') as show, \
                mock.patch.object(loop, 'run_loop') as run:
            code, result = self.invoke('start', '--foreground', '--desktop')
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])
        run.assert_not_called()
        show.assert_called_once_with('start', result)

    # Ordinary CLI callers retain the existing failure behavior.
    def test_ordinary_error_keeps_exception_without_dialog(self):
        """Ordinary CLI callers retain the existing failure behavior."""
        with mock.patch.object(cli.db, 'connect', side_effect=OSError('database unavailable')), \
                mock.patch.object(desktop, 'show_result') as show:
            with self.assertRaisesRegex(OSError, 'database unavailable'):
                self.invoke('stop')
        show.assert_not_called()

    # Visible text explains that running agents are awaited and reports the actual error.
    def test_german_feedback_describes_wait_and_errors(self):
        """Visible text explains that running agents are awaited and reports the actual error."""
        text = desktop.result_text('start', {'ok': True, 'stopping': True, 'running_agents': 2}, 'German')
        self.assertIn('2 laufende Agenten', text)
        self.assertIn('ununterbrochen', text)
        self.assertEqual(desktop.result_text('start', {'ok': False, 'error': 'kaputt'}, 'German'), 'Fehler: kaputt')
        self.assertEqual(desktop.result_text('start', {'ok': True}, 'German'), 'HQ wurde gestartet.')

    # The Windows presenter actually invokes a visible native dialog.
    def test_windows_dialog_displays_result(self):
        """The Windows presenter actually invokes a visible native dialog."""
        with mock.patch.object(desktop.sys, 'platform', 'win32'), \
                mock.patch('haivequeen.i18n.machine_language', return_value='German'), \
                mock.patch('ctypes.windll', create=True) as dll:
            desktop.show_result('stop', {'ok': True, 'running_agents': 1})
        dll.user32.MessageBoxW.assert_called_once()
        arguments = dll.user32.MessageBoxW.call_args.args
        self.assertIn('1 laufende Agenten', arguments[1])
        self.assertEqual(arguments[2:], ('HaiveQueen', 0x40))


if __name__ == '__main__':
    unittest.main()
