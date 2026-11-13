import io
import json
import unittest
from unittest import mock
from urllib.error import HTTPError

from haivequeen import git_provider
from haivequeen.git_providers import github


class GithubProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider, errors = git_provider.discover('git@github.com:team/project.git')
        self.assertEqual(errors, [])
        self.assertEqual(self.provider.repository, 'team/project')

    def response(self, value):
        return io.BytesIO(json.dumps(value).encode())

    def test_operations_use_github_interface(self):
        with mock.patch.object(github, 'urlopen', side_effect=[self.response({'number': 1}), self.response({'ref': 'refs/tags/v1'}), self.response([{'id': 1}]), self.response({'state': 'success'}), self.response({'id': 2})]) as request:
            self.assertEqual(self.provider.call('pull_request', 'title', 'feature', 'main', token='secret')['number'], 1)
            self.provider.call('tag', 'v1', 'abc', token='secret')
            self.assertEqual(self.provider.call('release_list'), [{'id': 1}])
            self.assertEqual(self.provider.call('pipeline', 'main')['state'], 'success')
            self.provider.call('release_asset', 1, 'a b.zip', b'data', 'secret')
        calls = [c.args[0] for c in request.call_args_list]
        self.assertEqual(json.loads(calls[0].data)['head'], 'feature')
        self.assertTrue(calls[1].full_url.endswith('/git/refs'))
        self.assertIn('per_page=100&page=1', calls[2].full_url)
        self.assertTrue(calls[3].full_url.endswith('/commits/main/status'))
        self.assertIn('uploads.github.com/repos/team/project/releases/1/assets?name=a%20b.zip', calls[4].full_url)
        self.assertEqual(calls[4].data, b'data')

    def test_credentials_and_errors(self):
        run = mock.Mock(return_value=(0, 'cli-token\n'))
        with mock.patch.dict('os.environ', {'GITHUB_TOKEN': 'first', 'GH_TOKEN': 'second'}, clear=True):
            self.assertEqual(self.provider.call('login', run=run), 'first')
        with mock.patch.dict('os.environ', {'GH_TOKEN': 'second'}, clear=True):
            self.assertEqual(self.provider.call('login', run=run), 'second')
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(self.provider.call('login', run=run), 'cli-token')
            self.assertEqual(self.provider.call('login', run=lambda *a: (1, '')), '')
        with mock.patch.object(github, 'urlopen', side_effect=HTTPError('url', 403, 'denied', {}, None)):
            with self.assertRaises(git_provider.ProviderError):
                self.provider.call('pipeline', 'main')

    def test_release_list_paginates_and_remote_detection_is_strict(self):
        with mock.patch.object(github, 'urlopen', side_effect=[self.response([{}] * 100), self.response([{'id': 101}])]):
            self.assertEqual(len(self.provider.call('release_list')), 101)
        self.assertIsNone(github.detect('https://github.com.evil/team/project'))
        self.assertIsNone(github.detect('https://github.com/../project'))


if __name__ == '__main__':
    unittest.main()
