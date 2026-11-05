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
