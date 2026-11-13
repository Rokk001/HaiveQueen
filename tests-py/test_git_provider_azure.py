import io
import json
import unittest
from unittest.mock import patch

from haivequeen import git_provider
from haivequeen.git_providers import azure


class AzureTests(unittest.TestCase):
    def setUp(self):
        self.repo = ('org', 'Project Name', 'repo')

    def test_discovery_and_detection(self):
        for remote in ('https://dev.azure.com/org/Project%20Name/_git/repo',
                       'https://org.visualstudio.com/Project%20Name/_git/repo',
                       'git@ssh.dev.azure.com:v3/org/Project%20Name/repo',
                       'ssh://git@ssh.dev.azure.com/v3/org/Project%20Name/repo'):
            provider, errors = git_provider.discover(remote)
            self.assertEqual(errors, [])
            self.assertEqual(provider.repository, self.repo)
            self.assertEqual(provider.name, 'azure')
        for remote in ('https://evil.dev.azure.com/org/p/_git/r', 'https://dev.azure.com/org/p/_git/a%2Fb', 'git@github.com:org/repo'):
            self.assertIsNone(azure.detect(remote))

    def test_operations_use_documented_requests_and_auth(self):
        calls = []
        def respond(request):
            calls.append(request)
            body = {'value': [{'success': True}], 'artifacts': [{'alias': 'build'}], 'status': 'completed'}
            response = io.BytesIO(json.dumps(body).encode())
            response.headers = {'x-ms-continuationtoken': 'next-page'}
            return response
        with patch.object(azure, 'urlopen', side_effect=respond):
            azure.pull_request(self.repo, 'topic', 'main', 'Title', 'secret')
            azure.tag(self.repo, 'v1', 'a' * 40, 'secret')
            azure.release(self.repo, 3, 'secret', auth='bearer')
            page, continuation = azure.release_list(self.repo, 'secret', definition_id=3, continuation_token='next')
            self.assertEqual(continuation, 'next-page')
            self.assertEqual(azure.release_assets(self.repo, 5, 'secret'), [{'alias': 'build'}])
            self.assertEqual(azure.pipeline(self.repo, 7, 'secret')['status'], 'completed')
        self.assertIn('/org/Project%20Name/_apis/git/repositories/repo/pullrequests?api-version=7.1', calls[0].full_url)
        self.assertEqual(json.loads(calls[0].data)['sourceRefName'], 'refs/heads/topic')
        self.assertEqual(calls[0].get_header('Authorization'), 'Basic OnNlY3JldA==')
        self.assertEqual(json.loads(calls[1].data)[0]['oldObjectId'], '0' * 40)
        self.assertIn('vsrm.dev.azure.com', calls[2].full_url)
        self.assertEqual(calls[2].get_header('Authorization'), 'Bearer secret')
        self.assertIn('continuationToken=next', calls[3].full_url)
        self.assertIn('/build/builds/7?', calls[5].full_url)
        self.assertEqual(azure.login_methods(self.repo), ('pat', 'bearer', 'device'))

    def test_rejected_tag_and_bad_credentials_fail(self):
        with patch.object(azure, 'urlopen', return_value=io.BytesIO(b'{"value":[{"success":false}]}')):
            with self.assertRaises(ValueError):
                azure.tag(self.repo, 'v1', 'a' * 40, 'secret')
        with self.assertRaises(ValueError):
            azure.pipeline(self.repo, 1, '', auth='pat')
        with self.assertRaises(ValueError):
            azure.pipeline(self.repo, 1, 'secret', auth='unknown')


if __name__ == '__main__':
    unittest.main()
