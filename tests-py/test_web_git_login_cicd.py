import unittest
from unittest.mock import patch

from haivequeen import web_api


class GitLoginWebTest(unittest.TestCase):
    def test_token_is_provider_wide_and_never_returned(self):
        with patch('haivequeen.config.save_git_credential') as save, \
             patch('haivequeen.config.git_credential', return_value={'token': 'secret', 'method': 'token'}):
            self.assertEqual(web_api.execute(None, 'git-login/token', {'provider': 'github', 'token': 'secret'}), {'ok': True})
            save.assert_called_once_with('github', 'secret', 'token')
            result = web_api.execute(None, 'git-login/providers', {})
            github = next(p for p in result if p['name'] == 'github')
            self.assertEqual(github['method'], 'token')
            self.assertNotIn('secret', repr(result))

    def test_device_login_requires_explicit_client_id(self):
        with self.assertRaisesRegex(ValueError, 'client ID'):
            web_api.execute(None, 'git-login/start', {'provider': 'github'})
        with patch('haivequeen.git_auth.start_device_login', return_value={'session_id': 'session'}) as start:
            web_api.execute(None, 'git-login/start', {'provider': 'github', 'client_id': 'registered'})
            start.assert_called_once_with('github', 'registered')

    def test_gitea_requires_https_origin_for_credential_scope(self):
        with patch('haivequeen.config.save_git_credential') as save:
            with self.assertRaisesRegex(ValueError, 'HTTPS origin'):
                web_api.execute(None, 'git-login/token', {'provider': 'gitea', 'token': 'secret'})
            save.assert_not_called()
        with patch('haivequeen.config.save_git_credential') as save:
            web_api.execute(None, 'git-login/token', {'provider': 'gitea', 'token': 'secret', 'origin': 'https://git.example'})
            save.assert_called_once_with('gitea', 'secret', 'token', origin='https://git.example')
