"""CLI checks report versions without installing anything."""
import unittest
from unittest import mock
from types import SimpleNamespace
from haivequeen import profile

class CliUpdateTests(unittest.TestCase):
    def test_update_is_notice_only_and_uses_own_profile(self):
        from haivequeen import cli_updates
        provider={'protocol':'codex','command':'codex','name':'provider'}
        with mock.patch.object(cli_updates.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='codex-cli 1.2.3',stderr='')) as run, mock.patch.object(cli_updates,'latest',return_value='1.3.0'):
            result=cli_updates.check(provider)
        self.assertEqual(result['status'],'update')
        self.assertEqual(result['installed'],'1.2.3')
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0],['codex','--version'])
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'],str(profile.provider_home(provider)))

    def test_unavailable_is_not_up_to_date(self):
        from haivequeen import cli_updates
        with mock.patch.object(cli_updates.subprocess,'run',side_effect=OSError('missing')):
            self.assertEqual(cli_updates.check({'protocol':'claude','command':'claude'})['status'],'unknown')
        self.assertEqual(cli_updates.check({'protocol':'custom','command':'wrapper'})['status'],'unsupported')

    def test_pre_release_is_not_compared_as_stable(self):
        from haivequeen import cli_updates
        self.assertIsNone(cli_updates.version('1.2.3-beta.1'))
        self.assertEqual(cli_updates.version('codex-cli 1.2.3'),(1,2,3))
