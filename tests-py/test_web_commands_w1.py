import contextlib
import io
import unittest

from haivequeen import cli


class OrchestratorCommandTests(unittest.TestCase):
    def test_global_pause_and_resume_are_not_commands(self):
        parser = cli.build_parser()
        for command in ('pause', 'resume'):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args([command])
        help_text = parser.format_help()
        self.assertNotIn('\n    pause ', help_text)
        self.assertNotIn('\n    resume ', help_text)

    def test_plan_pause_and_resume_remain_available(self):
        parser = cli.build_parser()
        for action in ('pause', 'resume'):
            args = parser.parse_args(['plan', action, '--project', 'example', '--plan', 'plan.md'])
            self.assertEqual(args.action, action)
