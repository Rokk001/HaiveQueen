import io
import json
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs
from unittest.mock import patch

from haivequeen import git_auth


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return self.data


class DeviceLoginTests(unittest.TestCase):
    def setUp(self):
        git_auth._sessions.clear()
        self.clock = patch('haivequeen.git_auth.time.monotonic', return_value=100)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(git_auth._sessions.clear)

    def test_github_start_pending_success_and_shared_store(self):
        start = {'device_code': 'secret-device', 'user_code': 'ABCD',
                 'verification_uri': 'https://github.com/login/device', 'expires_in': 600, 'interval': 5}
        with patch('haivequeen.git_auth.urlopen', side_effect=[Response(start), Response({'error': 'authorization_pending'}), Response({'access_token': 'secret-token'})]) as http, patch('haivequeen.config.save_git_credential') as save:
            result = git_auth.start_device_login('github', 'client')
            sid = result['session_id']
            self.assertEqual(result['user_code'], 'ABCD')
            self.assertNotIn('secret-device', str(result))
            self.assertEqual(http.call_args.args[0].full_url, 'https://github.com/login/device/code')
            self.assertEqual(parse_qs(http.call_args.args[0].data.decode())['client_id'], ['client'])
            self.assertEqual(git_auth.poll_device_login(sid)['status'], 'pending')
            self.assertEqual(http.call_count, 1)
            with patch('haivequeen.git_auth.time.monotonic', return_value=105):
                self.assertEqual(git_auth.poll_device_login(sid)['status'], 'pending')
            with patch('haivequeen.git_auth.time.monotonic', return_value=110):
                result = git_auth.poll_device_login(sid)
            self.assertEqual(result['status'], 'success')
            self.assertNotIn('secret-token', str(result))
            save.assert_called_once_with('github', 'secret-token', 'device')
            self.assertEqual(git_auth.device_login_status(sid)['status'], 'success')

    def test_azure_http_failure_and_slow_down(self):
        start = {'device_code': 'device', 'user_code': 'code', 'verification_uri': 'https://microsoft.com/devicelogin', 'expires_in': 100, 'interval': 5}
        error = HTTPError('https://example.com', 400, 'bad', {}, io.BytesIO(b'{"error":"access_denied"}'))
        with patch('haivequeen.git_auth.urlopen', side_effect=[Response(start), Response({'error': 'slow_down'}), error]), patch('haivequeen.config.save_git_credential') as save:
            sid = git_auth.start_device_login('azure', 'client')['session_id']
            with patch('haivequeen.git_auth.time.monotonic', return_value=105):
                self.assertEqual(git_auth.poll_device_login(sid)['interval'], 10)
            with patch('haivequeen.git_auth.time.monotonic', return_value=115):
                self.assertEqual(git_auth.poll_device_login(sid)['error'], 'access_denied')
            save.assert_not_called()

    def test_expiration_and_invalid_start(self):
        start = {'device_code': 'device', 'user_code': 'code', 'verification_uri': 'https://example.com', 'expires_in': 10}
        with patch('haivequeen.git_auth.urlopen', return_value=Response(start)) as http:
            sid = git_auth.start_device_login('github', 'client')['session_id']
            with patch('haivequeen.git_auth.time.monotonic', return_value=110):
                self.assertEqual(git_auth.poll_device_login(sid)['error'], 'expired_token')
            self.assertEqual(http.call_count, 1)
        with self.assertRaises(ValueError):
            git_auth.start_device_login('unknown', 'client')
        with patch('haivequeen.git_auth.urlopen', return_value=Response({'error': 'bad'})):
            with self.assertRaises(ValueError):
                git_auth.start_device_login('github', 'client')

    def test_gitlab_device_flow(self):
        start = {'device_code': 'device', 'user_code': 'code', 'verification_uri': 'https://gitlab.com/oauth/device', 'expires_in': 30, 'interval': 1}
        with patch('haivequeen.git_auth.urlopen', side_effect=[Response(start), Response({'access_token': 'token'})]) as http, patch('haivequeen.config.save_git_credential') as save:
            sid = git_auth.start_device_login('gitlab', 'client')['session_id']
            self.assertEqual(http.call_args.args[0].full_url, 'https://gitlab.com/oauth/authorize_device')
            with patch('haivequeen.git_auth.time.monotonic', return_value=101):
                self.assertEqual(git_auth.poll_device_login(sid)['status'], 'success')
            self.assertEqual(http.call_args.args[0].full_url, 'https://gitlab.com/oauth/token')
            save.assert_called_once_with('gitlab', 'token', 'device')

    def test_azure_success_uses_bearer(self):
        start = {'device_code': 'device', 'user_code': 'code', 'verification_uri': 'https://example.com', 'expires_in': 30, 'interval': 1}
        with patch('haivequeen.git_auth.urlopen', side_effect=[Response(start), Response({'access_token': 'token'})]), patch('haivequeen.config.save_git_credential') as save:
            sid = git_auth.start_device_login('azure', 'client')['session_id']
            with patch('haivequeen.git_auth.time.monotonic', return_value=101):
                self.assertEqual(git_auth.poll_device_login(sid)['status'], 'success')
            save.assert_called_once_with('azure', 'token', 'bearer')


if __name__ == '__main__':
    unittest.main()
