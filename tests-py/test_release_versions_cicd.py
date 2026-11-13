import unittest
from haivequeen import releases


class TagVersions(unittest.TestCase):
    def test_full_tags_and_prerelease_sequence(self):
        tags = ['v1.9.9', 'v2.0.0', 'v2.0.1-dev.1', 'v2.0.1-dev.2', '2.9.0', 'v9.0.0-rc.1']
        self.assertEqual(releases.prerelease_tag(tags, 'dev'), 'v2.0.1-dev.3')
        self.assertEqual(releases.prerelease_tag(tags, 'preview', requested='3.0.0'), 'v3.0.0-preview.1')

    def test_start_version_is_required_and_used_directly(self):
        with self.assertRaises(ValueError):
            releases.prerelease_tag(['v1.0.0-dev.1'], 'dev')
        self.assertEqual(releases.prerelease_tag([], 'dev', start='1.2.3'), 'v1.2.3-dev.1')

    def test_invalid_postfix_and_start_rejected(self):
        for kwargs in ({'postfix': 'bad value', 'start': '1.0.0'}, {'postfix': 'dev', 'start': '01.0.0'}):
            with self.assertRaises(ValueError):
                releases.prerelease_tag([], **kwargs)
