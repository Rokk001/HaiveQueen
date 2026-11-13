import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import git_provider
from haivequeen.git_providers import bitbucket as bb


class BitbucketTests(unittest.TestCase):
    def test_detection_and_discovery(self):
        for remote in ('git@bitbucket.org:team/repo.git', 'ssh://git@bitbucket.org/team/repo', 'https://bitbucket.org/team/repo.git'):
            self.assertEqual(bb.detect(remote), 'team/repo')
        for remote in ('https://bitbucket.org.evil/team/repo', 'https://bitbucket.org/a/b/c', 'git@github.com:a/b', 'https://bitbucket.org/../repo'):
            self.assertIsNone(bb.detect(remote))
        provider, errors = git_provider.discover('git@bitbucket.org:team/repo.git')
        self.assertEqual(errors, [])
        self.assertEqual(provider.name, 'bitbucket')
        self.assertNotIn('release', provider.capabilities)
        self.assertNotIn('release_list', provider.capabilities)

    def test_requests_and_authentication(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id": 42}'
        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'secret'}), patch.object(bb, 'urlopen', return_value=response) as send:
            self.assertEqual(bb.pull_request('team/repo', 'Title', 'feature', 'main'), {'id': 42})
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.bitbucket.org/2.0/repositories/team/repo/pullrequests')
            self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
            self.assertEqual(json.loads(request.data)['source']['branch']['name'], 'feature')
            bb.tag('team/repo', 'v1', 'abc', 'release')
            self.assertEqual(json.loads(send.call_args.args[0].data)['target']['hash'], 'abc')
            bb.pipeline('team/repo', '{uuid}')
            self.assertTrue(send.call_args.args[0].full_url.endswith('/pipelines/%7Buuid%7D'))
            bb.login('team/repo')
            self.assertTrue(send.call_args.args[0].full_url.endswith('/repositories/team/repo'))
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'BITBUCKET_TOKEN'):
            bb.login('team/repo')

    def test_downloads_and_pagination(self):
        with patch.object(bb, '_request', side_effect=[{'values': [1], 'next': bb._API + 'next'}, {'values': [2]}]) as request:
            self.assertEqual(bb.asset_list('team/repo'), [1, 2])
            self.assertEqual(request.call_args.args[0], 'next')
        with patch.object(bb, '_request', return_value={'values': [], 'next': 'https://evil.test/'}), self.assertRaises(ValueError):
            bb.asset_list('team/repo')
        with tempfile.TemporaryDirectory() as tmp, patch.object(bb, '_request') as request:
            path = Path(tmp) / 'asset.bin'
            path.write_bytes(b'content')
            bb.asset_upload('team/repo', path)
            args = request.call_args.args
            self.assertEqual(args[:2], ('repositories/team/repo/downloads', 'POST'))
            self.assertIn(b'filename="asset.bin"', args[2])
            self.assertIn(b'content', args[2])

    def test_invalid_repository(self):
        with self.assertRaises(ValueError):
            bb.login('../repo')


if __name__ == '__main__':
    unittest.main()
