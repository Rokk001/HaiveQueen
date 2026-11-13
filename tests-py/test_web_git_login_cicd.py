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

    def test_registered_public_client_can_be_provided_for_all_projects(self):
        with patch.dict(os.environ, {'HAIVEQUEEN_GITHUB_CLIENT_ID': 'registered-public-client'}):
            rows = web_api.execute(None, 'git-login/providers', {})
        self.assertEqual(next(row for row in rows if row['name'] == 'github')['client_id'], 'registered-public-client')

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
            self.assertTrue(next(row for row in rows if row['name'] == 'github')['device'])
            self.assertIn('expired access', rows[0]['status_error'])
            web_api.execute(None, 'git-login/token', {'provider': 'github', 'token': 'replacement'})
        save.assert_called_once_with('github', 'replacement', 'token')

    def test_device_flow_web_transport_for_each_provider_never_exposes_secrets(self):
        from haivequeen import git_auth
        for provider in ('github', 'gitlab', 'azure'):
            with self.subTest(provider=provider), \
                 patch('haivequeen.git_auth._post', side_effect=[
                     {'device_code': 'private-device', 'user_code': 'VISIBLE', 'verification_uri': 'https://example.invalid/device', 'interval': 1, 'expires_in': 600},
                     {'error': 'authorization_pending'}, {'access_token': 'private-token'}]), \
                 patch('haivequeen.git_auth.time.monotonic', side_effect=[0, 2, 2, 2, 4, 4, 4]), \
                 patch('haivequeen.config.save_git_credential') as save:
                started = web_api.execute(None, 'git-login/start', {'provider': provider, 'client_id': 'registered-public-client'})
                self.assertEqual(started['user_code'], 'VISIBLE')
                self.assertNotIn('private-device', repr(started))
                pending = web_api.execute(None, 'git-login/poll', {'id': started['session_id']})
                self.assertEqual(pending['status'], 'pending')
                complete = web_api.execute(None, 'git-login/poll', {'id': started['session_id']})
                self.assertEqual(complete['status'], 'success')
                self.assertNotIn('private-token', repr(complete))
                self.assertEqual(save.call_args.args[:2], (provider, 'private-token'))
                git_auth._sessions.pop(started['session_id'])

    def test_provider_modules_offer_registration_instructions_and_both_choices(self):
        with patch('haivequeen.config.git_credential', return_value=None):
            rows = web_api.execute(None, 'git-login/providers', {})
        self.assertEqual(len(rows), 5)
        for row in rows:
            if row['device']:
                self.assertTrue(row['registration_url'].startswith('https://'))
                self.assertIn('token', row['registration_help'])

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
