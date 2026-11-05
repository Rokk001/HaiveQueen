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
