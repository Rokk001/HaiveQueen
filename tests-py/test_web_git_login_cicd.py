import isolation  # noqa: F401 -- protect the active profile before application imports
import os
import unittest
from unittest.mock import patch

from haivequeen import web_api


class GitLoginWebTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {key: '' for key in ('GITHUB_TOKEN', 'GH_TOKEN', 'GITLAB_TOKEN', 'AZURE_DEVOPS_TOKEN', 'GITEA_TOKEN', 'BITBUCKET_TOKEN')})
        environment.start()
        self.addCleanup(environment.stop)
        credentials = patch('haivequeen.config.git_credential', return_value=None)
        credentials.start()
        self.addCleanup(credentials.stop)
        legacy = patch('haivequeen.git_providers.github.auth_status', return_value=False)
        legacy.start()
        self.addCleanup(legacy.stop)

    def test_azure_token_uses_pat_method(self):
        with patch('haivequeen.config.save_git_credential') as save:
            web_api.execute(None, 'git-login/token', {'provider': 'azure', 'token': 'mock'})
        save.assert_called_once_with('azure', 'mock', 'pat')

    def test_azure_legacy_web_token_and_shared_projects_use_same_access(self):
        import io
        from haivequeen.git_providers import azure, gitlab
        credential = {'token': 'mock', 'method': 'token'}
        def response(body):
            stream = io.BytesIO(body)
            stream.headers = {}
            return stream
        with patch('haivequeen.config.git_credential', return_value=credential), \
             patch.object(azure, 'urlopen', side_effect=lambda *args, **kwargs: response(b'{}')) as request:
            azure.pipeline(('org', 'project', 'repo'), 1)
            self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Basic Om1vY2s=')
        with patch('haivequeen.config.git_credential', return_value=credential), \
             patch.object(gitlab, 'urlopen', side_effect=lambda *args, **kwargs: response(b'[]')) as request:
            gitlab.release_list('team/one')
            gitlab.release_list('team/two')
            self.assertEqual([call.args[0].get_header('Private-token') for call in request.call_args_list], ['mock', 'mock'])

    def test_existing_cli_login_method_is_visible(self):
        with patch('haivequeen.git_providers.github.auth_status', return_value=True):
            rows = web_api.execute(None, 'git-login/providers', {})
        self.assertEqual(next(row for row in rows if row['name'] == 'github')['method'], 'existing access')

    def test_only_token_controls_are_advertised_even_with_client_ids(self):
        with patch.dict(os.environ, {'HAIVEQUEEN_GITHUB_CLIENT_ID': 'registered'}):
            rows = web_api.execute(None, 'git-login/providers', {})
        self.assertEqual({row['name'] for row in rows}, {'github', 'gitlab', 'azure', 'bitbucket', 'gitea'})
        for row in rows:
            self.assertFalse(row['device'])
            for key in ('client_id', 'client_id_env', 'registration_url', 'registration_help'):
                self.assertNotIn(key, row)

    def test_broken_module_does_not_hide_other_login_controls(self):
        from types import SimpleNamespace
        from haivequeen import git_provider
        with patch.object(git_provider.pkgutil, 'iter_modules', return_value=[SimpleNamespace(name='broken'), SimpleNamespace(name='working')]), \
             patch.object(git_provider.importlib, 'import_module', side_effect=[RuntimeError('invalid module'), SimpleNamespace(LOGIN_OPTIONS={'device': False})]):
            rows = git_provider.login_options()
        self.assertIn('provider broken: invalid module', rows[0]['error'])
        self.assertEqual(rows[1]['name'], 'working')

    def test_expired_access_does_not_prevent_replacement_token(self):
        with patch('haivequeen.config.git_credential', side_effect=ValueError('expired access requires sign-in')), \
             patch('haivequeen.config.save_git_credential') as save:
            rows = web_api.execute(None, 'git-login/providers', {})
            self.assertFalse(next(row for row in rows if row['name'] == 'github')['device'])
            self.assertIn('expired access', rows[0]['status_error'])
            web_api.execute(None, 'git-login/token', {'provider': 'github', 'token': 'replacement'})
        save.assert_called_once_with('github', 'replacement', 'token')

    def test_deferred_device_endpoints_have_no_side_effects(self):
        with patch('haivequeen.git_auth.start_device_login') as start, \
             patch('haivequeen.git_auth.poll_device_login') as poll, \
             patch('haivequeen.config.save_git_credential') as save:
            for action in ('git-login/start', 'git-login/poll'):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'access token'):
                    web_api.execute(None, action, {'provider': 'github', 'client_id': 'registered', 'id': 'session'})
        start.assert_not_called()
        poll.assert_not_called()
        save.assert_not_called()

    def test_each_provider_saves_token_without_returning_it(self):
        for provider in ('github', 'gitlab', 'azure', 'bitbucket', 'gitea'):
            with self.subTest(provider=provider), patch('haivequeen.config.save_git_credential') as save:
                data = {'provider': provider, 'token': 'private-token'}
                if provider == 'gitea':
                    data['origin'] = 'https://git.example'
                self.assertEqual(web_api.execute(None, 'git-login/token', data), {'ok': True})
                save.assert_called_once_with(provider, 'private-token', 'pat' if provider == 'azure' else 'token',
                                            **({'origin': data['origin']} if provider == 'gitea' else {}))

    def test_token_is_provider_wide_and_never_returned(self):
        with patch('haivequeen.config.save_git_credential') as save, \
             patch('haivequeen.config.git_credential', return_value={'token': 'secret', 'method': 'token'}):
            self.assertEqual(web_api.execute(None, 'git-login/token', {'provider': 'github', 'token': 'secret'}), {'ok': True})
            save.assert_called_once_with('github', 'secret', 'token')
            result = web_api.execute(None, 'git-login/providers', {})
            github = next(p for p in result if p['name'] == 'github')
            self.assertEqual(github['method'], 'token')
            self.assertNotIn('secret', repr(result))

    def test_gitea_requires_https_origin_for_credential_scope(self):
        with patch('haivequeen.config.save_git_credential') as save:
            with self.assertRaisesRegex(ValueError, 'HTTPS origin'):
                web_api.execute(None, 'git-login/token', {'provider': 'gitea', 'token': 'secret'})
            save.assert_not_called()
        with patch('haivequeen.config.save_git_credential') as save:
            web_api.execute(None, 'git-login/token', {'provider': 'gitea', 'token': 'secret', 'origin': 'https://git.example'})
            save.assert_called_once_with('gitea', 'secret', 'token', origin='https://git.example')
