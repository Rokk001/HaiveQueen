import io
import json
import unittest
from unittest import mock

from haivequeen import git_provider
from haivequeen.git_providers import gitlab


class Response(io.BytesIO):
    def __init__(self, value, next_page=''):
        super().__init__(json.dumps(value).encode())
        self.headers = {'X-Next-Page': next_page}


class GitLabTests(unittest.TestCase):
    def test_detection_and_dispatch(self):
        for remote in ('https://gitlab.com/group/sub/repo.git', 'git@gitlab.com:group/sub/repo.git', 'ssh://git@gitlab.com/group/sub/repo.git'):
            provider, errors = git_provider.discover(remote)
            self.assertEqual(errors, [])
            self.assertEqual(provider.repository, 'group/sub/repo')
            self.assertEqual(provider.call('login_methods'), ('personal_access_token',))
        for remote in ('https://gitlab.com.evil/group/repo', 'https://gitlab.com/group', 'https://user:secret@gitlab.com/group/repo', 'https://gitlab.com/group/../repo'):
            self.assertIsNone(gitlab.detect(remote))

    @mock.patch.object(gitlab, 'urlopen')
    def test_writes_and_pipeline(self, urlopen):
        urlopen.side_effect = [Response({'iid': 1}), Response({'name': 'v1'}), Response({'tag_name': 'v1'}), Response({'id': 2}), Response([{'status': 'success'}])]
        repo = 'group/sub/repo'
        calls = [lambda: gitlab.pull_request(repo, 'feature', 'main', 'Title', 'secret'),
                 lambda: gitlab.tag(repo, 'v1', 'abc', 'secret'),
                 lambda: gitlab.release(repo, 'v1', 'Release', 'Notes', 'secret', ref='abc'),
                 lambda: gitlab.asset(repo, 'v1/a', 'binary', 'https://example.org/file', 'secret'),
                 lambda: gitlab.pipeline(repo, 'main', 'secret')]
        results = [call() for call in calls]
        self.assertEqual(results[-1]['status'], 'success')
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertIn('/projects/group%2Fsub%2Frepo/merge_requests', requests[0].full_url)
        self.assertEqual(json.loads(requests[0].data)['source_branch'], 'feature')
        self.assertEqual(requests[1].get_method(), 'POST')
        self.assertEqual(json.loads(requests[2].data)['ref'], 'abc')
        self.assertIn('/releases/v1%2Fa/assets/links', requests[3].full_url)
        self.assertIn('ref=main', requests[4].full_url)
        self.assertEqual(requests[0].get_header('Private-token'), 'secret')

    @mock.patch.object(gitlab, 'urlopen')
    def test_pagination_and_failures(self, urlopen):
        urlopen.side_effect = [Response([{'tag_name': 'a'}], '2'), Response([{'tag_name': 'b'}])]
        self.assertEqual(len(gitlab.release_list('group/repo')), 2)
        self.assertIn('page=2', urlopen.call_args.args[0].full_url)
        urlopen.side_effect = [Response([], '1')]
        with self.assertRaisesRegex(ValueError, 'pagination'):
            gitlab.release_list('group/repo')
        urlopen.side_effect = OSError('offline')
        provider, _ = git_provider.discover('https://gitlab.com/group/repo')
        with self.assertRaisesRegex(git_provider.ProviderError, 'offline'):
            provider.call('pipeline', 'main')


if __name__ == '__main__':
    unittest.main()
