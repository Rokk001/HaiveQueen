import unittest

from haivequeen import releases


class VersionTests(unittest.TestCase):
    def test_patch_and_explicit_version(self):
        self.assertEqual(releases.next_version('2.0.0'), '2.0.1')
        self.assertEqual(releases.next_version('2.0.1', '2.1.0'), '2.1.0')

    def test_rejects_invalid_or_non_increasing_version(self):
        for current, requested in [('2.0', ''), ('2.0.1', '2.0.1'), ('2.0.1', '1.9.9'), ('2.0.1', 'v2.1.0')]:
            with self.subTest(current=current, requested=requested), self.assertRaises(ValueError):
                releases.next_version(current, requested)

class ReleaseCheckTests(unittest.TestCase):
    def test_check_selects_only_new_stable_release(self):
        from unittest.mock import patch
        from io import BytesIO
        import json
        response = BytesIO(json.dumps({'tag_name': 'v2.0.1', 'html_url': 'https://github.com/example/app/releases/tag/v2.0.1'}).encode())
        with patch('haivequeen.releases.urlopen', return_value=response):
            self.assertEqual(releases.check_latest('example/app', '2.0.0')['version'], '2.0.1')
        response = BytesIO(json.dumps({'tag_name': 'v2.0.1'}).encode())
        with patch('haivequeen.releases.urlopen', return_value=response):
            self.assertIsNone(releases.check_latest('example/app', '2.0.1'))

    def test_rejects_invalid_repository(self):
        with self.assertRaises(ValueError):
            releases.check_latest('../bad/path', '2.0.0')

class PublishingTests(unittest.TestCase):
    def test_remote_validation(self):
        for remote in ('https://github.com/owner/repo.git', 'git@github.com:owner/repo.git',
                       'ssh://git@github.com/owner/repo'):
            self.assertEqual(releases.repository_from_remote(remote), 'owner/repo')
        for remote in ('https://github.com.evil.test/owner/repo', 'https://gitlab.com/owner/repo',
                       'git@github.com:../repo', 'https://github.com/owner/repo/extra'):
            with self.subTest(remote=remote), self.assertRaises(ValueError):
                releases.repository_from_remote(remote)

    def test_publish_creates_tag_then_release_and_retries(self):
        import json
        from io import BytesIO
        from urllib.error import HTTPError
        from unittest.mock import patch
        sha = 'a' * 40
        ref = {'object': {'type': 'commit', 'sha': sha}}
        release = {'tag_name': 'v2.0.1', 'html_url': 'https://github.com/o/r/releases/tag/v2.0.1'}
        def response(value):
            return BytesIO(json.dumps(value).encode())
        missing = HTTPError('https://api.github.com/', 404, 'missing', {}, None)
        with patch('haivequeen.releases.urlopen', side_effect=[missing, response(ref), missing, response(release)]) as call:
            self.assertEqual(releases.publish_release('o/r', '2.0.1', sha, 'secret'), release)
            self.assertEqual(json.loads(call.call_args_list[1].args[0].data),
                             {'ref': 'refs/tags/v2.0.1', 'sha': sha})
            self.assertEqual(json.loads(call.call_args_list[3].args[0].data)['target_commitish'], sha)
        with patch('haivequeen.releases.urlopen', side_effect=[response(ref), response(release)]) as call:
            self.assertEqual(releases.publish_release('o/r', '2.0.1', sha, 'secret'), release)
            self.assertEqual(call.call_count, 2)

    def test_rejects_conflicting_tag_and_propagates_network_errors(self):
        import json
        from io import BytesIO
        from urllib.error import HTTPError
        from unittest.mock import patch
        sha = 'a' * 40
        for obj in ({'type': 'commit', 'sha': 'b' * 40}, {'type': 'tag', 'sha': sha}):
            with patch('haivequeen.releases.urlopen', return_value=BytesIO(json.dumps({'object': obj}).encode())) as call:
                with self.assertRaises(ValueError):
                    releases.publish_release('o/r', '2.0.1', sha, 'secret')
                self.assertEqual(call.call_count, 1)
        error = HTTPError('https://api.github.com/', 403, 'forbidden', {}, None)
        with patch('haivequeen.releases.urlopen', side_effect=error), self.assertRaises(HTTPError):
            releases.publish_release('o/r', '2.0.1', sha, 'secret')

    def test_validate_before_network(self):
        from unittest.mock import patch
        with patch('haivequeen.releases.urlopen') as call:
            for args in [('bad/repo/extra', '2.0.1', 'a' * 40, 'x'),
                         ('o/r', 'v2.0.1', 'a' * 40, 'x'),
                         ('o/r', '2.0.1', 'main', 'x'),
                         ('o/r', '2.0.1', 'a' * 40, '')]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    releases.publish_release(*args)
            call.assert_not_called()
