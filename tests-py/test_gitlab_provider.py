import io
import json
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from haivequeen import git_provider
from haivequeen.git_providers import gitlab


class Response(io.BytesIO):
    def __init__(self, value, next_page=''):
        super().__init__(json.dumps(value).encode())
        self.headers = {'X-Next-Page': next_page}


class GitLabTests(unittest.TestCase):
    @mock.patch.object(gitlab, 'urlopen')
    def test_local_asset_upload_and_attachment(self, urlopen):
        urlopen.side_effect = [Response({'url': '/uploads/abc/build_file.zip'}), Response({'id': 42})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'build file.zip'
            path.write_bytes(b'\x00binary\xff\r\n')
            result = gitlab.asset('group/sub/repo', 'v1/a', path.name, path, 'secret')

        self.assertEqual(result, {'id': 42})
        upload, link = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(upload.full_url, 'https://gitlab.com/api/v4/projects/group%2Fsub%2Frepo/uploads')
        self.assertEqual(upload.get_method(), 'POST')
        self.assertEqual(upload.get_header('Private-token'), 'secret')
        message = BytesParser(policy=default).parsebytes(
            ('Content-Type: ' + upload.get_header('Content-type') + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + upload.data)
        part = list(message.iter_parts())[0]
        self.assertEqual(part.get_param('name', header='content-disposition'), 'file')
        self.assertEqual(part.get_filename(), 'build file.zip')
        self.assertEqual(part.get_payload(decode=True), b'\x00binary\xff\r\n')
        self.assertTrue(link.full_url.endswith('/releases/v1%2Fa/assets/links'))
        self.assertEqual(json.loads(link.data), {'name': 'build file.zip', 'url': 'https://gitlab.com/group/sub/repo/uploads/abc/build_file.zip'})

    @mock.patch.object(gitlab, 'urlopen')
    def test_failed_upload_does_not_attach(self, urlopen):
        urlopen.side_effect = OSError('upload failed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file.bin'
            path.write_bytes(b'data')
            with self.assertRaisesRegex(OSError, 'upload failed'):
                gitlab.asset('group/repo', 'v1', path.name, path, 'secret')
        self.assertEqual(urlopen.call_count, 1)

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
