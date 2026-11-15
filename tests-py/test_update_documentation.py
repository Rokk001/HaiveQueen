from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UpdateDocumentationTest(unittest.TestCase):
    def test_release_update_instructions_cover_both_paths(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        guide = (ROOT / 'docs' / 'UPDATES.md').read_text(encoding='utf-8')
        self.assertIn('docs/UPDATES.md', readme)
        self.assertNotIn('fast-forwards the clone to the release tag', readme)
        for phrase in ('versions/', 'active-version', 'bzz update --install',
                       'bzz update --force', 'confirmation', 'running jobs'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, guide)
