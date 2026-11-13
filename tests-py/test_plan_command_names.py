import contextlib
import io
import os
import unittest
from unittest import mock

from haivequeen import cli, hives, owner


class PlanCommandNamesTests(unittest.TestCase):
    def test_commands_dispatch_to_plan_controls(self):
        parser = cli.build_parser()
        for action, target, expected in (
            ('stop-gracefully', 'owner', 'pause'),
            ('pause', 'owner', 'pause'),
            ('resume', 'owner', 'resume'),
            ('force-stop', 'hives', None),
        ):
            with self.subTest(action=action), mock.patch.dict(os.environ, {}, clear=True):
                args = parser.parse_args(['plan', action, '--project', 'p', '--plan', 'f'])
                with mock.patch.object(owner, 'apply', return_value={'ok': True}) as apply, \
                     mock.patch.object(hives, 'stop_hive') as stop, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.cmd_plan(args, 'connection'), 0)
                if target == 'owner':
                    self.assertEqual(apply.call_args.args[:4], ('connection', expected, 'p', 'f'))
                    stop.assert_not_called()
                else:
                    stop.assert_called_once_with('connection', 'p', 'f')
                    apply.assert_not_called()

    def test_force_stop_is_owner_only(self):
        args = cli.build_parser().parse_args(['plan', 'force-stop', '--project', 'p', '--plan', 'f'])
        with mock.patch.dict(os.environ, {'BZZ_AGENT': 'worker'}), \
             mock.patch.object(hives, 'stop_hive') as stop, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_plan(args, 'connection'), 1)
        stop.assert_not_called()

    def test_help_explains_control_names(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(['plan', '--help'])
        help_text = ' '.join(output.getvalue().split())
        for phrase in ('stop-gracefully', 'force-stop', 'resume', 'running', 'pause'):
            self.assertIn(phrase, help_text)
