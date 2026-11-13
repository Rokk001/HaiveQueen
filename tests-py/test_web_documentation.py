import unittest
from pathlib import Path

from haivequeen import cli


class WebDocumentationTests(unittest.TestCase):
    def test_web_help_and_guide_cover_lifecycle(self):
        help_text = cli.build_parser()._subparsers._group_actions[0].choices['web'].format_help()
        for word in ('setup', 'start', 'stop', 'restart', 'off'):
            self.assertIn(word, help_text)
        self.assertIn('loopback', help_text)
        guide = Path('docs/WEB-API.md').read_text(encoding='utf-8')
        for phrase in ('systemd', 'bzz web setup', 'bzz web restart', 'Force stop', 'tray', 'background service'):
            self.assertIn(phrase.lower(), guide.lower())
        readme = Path('README.md').read_text(encoding='utf-8')
        self.assertIn('docs/WEB-API.md', readme)
