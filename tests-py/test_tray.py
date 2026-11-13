import unittest
from unittest.mock import patch

from haivequeen import tray


class TrayTests(unittest.TestCase):
    def test_states_and_menu(self):
        for state, expected in [('Running', (False, True, True)), ('Stopping', (False, False, True)), ('Stopped', (True, False, False))]:
            menu = tray.menu(state, True)
            self.assertEqual(tuple(item[2] for item in menu[1:4]), expected)
            self.assertEqual([item[1] for item in menu], ['Open web interface', 'Start', 'Stop gracefully', 'Force stop', 'Start', 'Stop', 'Restart'])
            self.assertEqual(tuple(item[2] for item in menu[4:]), (False, True, True))
        self.assertEqual(tuple(item[2] for item in tray.menu('Stopped', False)[4:]), (True, False, False))

    def test_confirmation_precedes_force(self):
        with patch.object(tray, 'run_command') as run, patch.object(tray, 'confirm', return_value=False):
            tray.invoke(4)
            run.assert_not_called()
        with patch.object(tray, 'run_command') as run, patch.object(tray, 'confirm', return_value=True):
            tray.invoke(4)
            run.assert_called_once_with(['stop', '--force'])

    def test_other_commands(self):
        with patch.object(tray, 'run_command') as run, patch.object(tray.web_service, 'open_browser') as browser:
            for number in (1, 2, 3, 5, 6, 7):
                tray.invoke(number)
            browser.assert_called_once_with()
            self.assertEqual([x.args[0] for x in run.call_args_list], [['start'], ['stop'], ['web', 'start'], ['web', 'stop'], ['web', 'restart']])

    def test_non_windows_never_spawns(self):
        with patch.object(tray.os, 'name', 'posix'), patch.object(tray.subprocess, 'Popen') as spawn:
            tray.ensure_started()
            spawn.assert_not_called()

    def test_orchestrator_state(self):
        with patch.object(tray.loop, 'loop_running', return_value=None):
            self.assertEqual(tray.state(), 'Stopped')
        with patch.object(tray.loop, 'loop_running', return_value=123), patch.object(tray.loop, 'stop_flag') as flag:
            flag.return_value.exists.return_value = False
            self.assertEqual(tray.state(), 'Running')
            flag.return_value.exists.return_value = True
            self.assertEqual(tray.state(), 'Stopping')

    def test_existing_web_service_ensures_tray(self):
        with patch.object(tray.web_service, 'control', return_value={'running': True}) as control, patch.object(tray, 'ensure_started') as ensure:
            tray.web_service.start()
            control.assert_called_once_with('status')
            ensure.assert_called_once_with()
