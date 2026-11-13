import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from haivequeen import git_provider
from haivequeen.git_providers import gitea


class Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return self.value


class GiteaTests(unittest.TestCase):
    def setUp(self):
        self.repo = ('https://codeberg.org', 'team', 'project')

    def test_detection_and_discovery(self):
        with mock.patch.dict(os.environ, {'HAIVEQUEEN_GITEA_HOSTS': 'forge.example'}, clear=True):
            for remote in ('https://codeberg.org/team/project.git', 'git@codeberg.org:team/project.git', 'ssh://git@codeberg.org/team/project'):
                self.assertEqual(gitea.detect(remote), self.repo)
            self.assertEqual(gitea.detect('https://forge.example/team/project'), ('https://forge.example', 'team', 'project'))
            self.assertIsNone(gitea.detect('https://github.com/team/project'))
            self.assertIsNone(gitea.detect('https://codeberg.org/team/project/extra'))
            provider, errors = git_provider.discover('https://codeberg.org/team/project')
            self.assertEqual(errors, [])
            self.assertEqual(provider.name, 'gitea')
            self.assertEqual(provider.capabilities, gitea.CAPABILITIES)

    @mock.patch.object(gitea, 'urlopen')
    def test_operations_and_authentication(self, open_url):
        open_url.return_value = Response({'ok': True})
        operations = [
            (lambda: gitea.pull_request(self.repo, 'Title', 'feature', 'main', token='secret'), '/pulls', {'head': 'feature', 'base': 'main'}),
            (lambda: gitea.tag(self.repo, 'v1', 'main', token='secret'), '/tags', {'tag_name': 'v1', 'target': 'main'}),
            (lambda: gitea.release(self.repo, 'v1', token='secret'), '/releases', {'tag_name': 'v1'}),
        ]
        for call, suffix, expected in operations:
            self.assertEqual(call(), {'ok': True})
            req = open_url.call_args.args[0]
            self.assertTrue(req.full_url.endswith(suffix))
            self.assertEqual(req.get_method(), 'POST')
            self.assertEqual(req.get_header('Authorization'), 'token secret')
            self.assertTrue(expected.items() <= json.loads(req.data).items())
        gitea.pipeline(self.repo, 'feature/test', token='secret')
        self.assertTrue(open_url.call_args.args[0].full_url.endswith('/commits/feature%2Ftest/status'))
        gitea.login(self.repo, 'secret')
        self.assertEqual(open_url.call_args.args[0].full_url, 'https://codeberg.org/api/v1/user')
        with self.assertRaises(ValueError):
            gitea.login(self.repo, '')

    @mock.patch.object(gitea, 'urlopen')
    def test_assets_and_pagination(self, open_url):
        open_url.side_effect = [Response({'id': 9}), Response([{'id': i} for i in range(50)]), Response([{'id': 50}])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'release file.bin'
            path.write_bytes(b'payload\x00')
            self.assertEqual(gitea.asset_upload(self.repo, 3, path, token='t'), {'id': 9})
            req = open_url.call_args.args[0]
            self.assertIn('/releases/3/assets?name=release+file.bin', req.full_url)
            self.assertIn(b'name="attachment"', req.data)
            self.assertIn(b'payload\x00', req.data)
        self.assertEqual(len(gitea.release_list(self.repo)), 51)
        self.assertIn('page=2', open_url.call_args.args[0].full_url)


if __name__ == '__main__':
    unittest.main()
