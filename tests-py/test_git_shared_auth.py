import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config
from haivequeen.git_providers import github, gitlab, gitea, bitbucket, azure


class Response:
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return b'{}'


class SharedCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'BZZ_CONFIG_DIR': self.tmp.name}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_profile_storage_is_shared_and_outside_repository(self):
        repo = Path(self.tmp.name) / 'repo'
        repo.mkdir()
        with patch('haivequeen.config.config_dir', return_value=Path(self.tmp.name) / 'profile'):
            config.save_git_credential('github', 'secret', 'device')
            self.assertEqual(config.git_credential('github'), {'token': 'secret', 'method': 'device'})
            self.assertEqual(list(repo.iterdir()), [])
            config.save_git_credential('github', None)
            self.assertIsNone(config.git_credential('github'))

    def test_requests_use_shared_token_and_azure_method(self):
        cases = [(github, lambda: github.pipeline('a/b', 'main'), 'Authorization', 'Bearer secret'),
                 (gitlab, lambda: gitlab.pipeline('a/b', 'main'), 'Private-token', 'secret'),
                 (gitea, lambda: gitea.pipeline(('https://gitea.com', 'a', 'b'), 'main'), 'Authorization', 'token secret'),
                 (bitbucket, lambda: bitbucket.login('a/b'), 'Authorization', 'Bearer secret'),
                 (azure, lambda: azure.pipeline(('org', 'project', 'repo'), 1), 'Authorization', 'Bearer secret')]
        for module, operation, header, expected in cases:
            with self.subTest(module=module.__name__), patch('haivequeen.config.git_credential', return_value={'token': 'secret', 'method': 'bearer', 'origin': 'https://gitea.com'}), patch.object(module, 'urlopen', return_value=Response()) as request:
                operation()
                self.assertEqual(request.call_args.args[0].get_header(header), expected)

    def test_gitlab_device_and_pat_credentials(self):
        cases = [({'token': 'device-secret', 'method': 'device'}, None, 'Bearer device-secret', None),
                 ({'token': 'pat-secret', 'method': 'token'}, None, None, 'pat-secret'),
                 ({'token': 'device-secret', 'method': 'device'}, 'explicit-pat', None, 'explicit-pat')]
        for credential, explicit, authorization, private_token in cases:
            with self.subTest(credential=credential, explicit=explicit), \
                    patch('haivequeen.config.git_credential', return_value=credential), \
                    patch.object(gitlab, 'urlopen', return_value=Response()) as request:
                gitlab.tag('a/b', 'v1', 'main', explicit)
                sent = request.call_args.args[0]
                self.assertEqual(sent.get_method(), 'POST')
                self.assertEqual(sent.get_header('Authorization'), authorization)
                self.assertEqual(sent.get_header('Private-token'), private_token)

    def test_github_fallbacks_and_status(self):
        with patch('haivequeen.config.git_credential', return_value=None), patch.dict(os.environ, {'GITHUB_TOKEN': 'env', 'GH_TOKEN': ''}):
            self.assertEqual(github.login('a/b'), 'env')
            self.assertTrue(github.auth_status())
        with patch('haivequeen.config.git_credential', return_value=None), patch.dict(os.environ, {'GITHUB_TOKEN': '', 'GH_TOKEN': ''}):
            self.assertEqual(github.login('a/b', run=lambda *args: (0, 'gh-token\n')), 'gh-token')

    def test_device_sign_in_saves_token_and_failure_does_not(self):
        with patch('haivequeen.publish._run', return_value=(0, 'device-token\n')), patch('haivequeen.config.save_git_credential') as save:
            github.device_sign_in(run=lambda args: 0)
            save.assert_called_once_with('github', 'device-token', 'device')
        with patch('haivequeen.config.save_git_credential') as save:
            with self.assertRaises(ValueError): github.device_sign_in(run=lambda args: 1)
            save.assert_not_called()


if __name__ == '__main__': unittest.main()
