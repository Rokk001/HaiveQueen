"""Keep the public entry point usable while the CLI and documentation evolve."""
import argparse
from pathlib import Path
import re
import unittest

from haivequeen.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def commands(parser, prefix='bzz'):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {item for name, child in action.choices.items()
                    for item in commands(child, prefix + ' ' + name)}
    choices = next((a for a in parser._actions if a.dest == 'action' and a.choices), None)
    if choices:
        result = {prefix + ' ' + choice for choice in choices.choices}
        if choices.nargs == '?':
            result.add(prefix)
        return result
    return {prefix}


class ReadmeEntrypointTests(unittest.TestCase):
    def test_command_inventory_matches_real_parser(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        listed = set(re.findall(r'^\| `(bzz[^`]+)` \|', text, re.M))
        self.assertEqual(commands(build_parser()), listed)

    def test_first_start_includes_both_platforms_and_provider_setup(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        section = text.split('## Install and start', 1)[1].split('\n## ', 1)[0]
        for instruction in ('Python 3.11', 'Git', 'PowerShell', 'Linux', 'pip install -e .',
                            'bzz init', 'bzz register', 'bzz gui', 'bzz start', 'Login',
                            'status = "SENT"'):
            self.assertIn(instruction, section)

    def test_product_claims_do_not_promise_unmeasured_savings(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertNotIn('very token-saving', text)
        self.assertNotIn('run into provider limits much later', text)
        self.assertNotIn('uses only the Python standard library', text)
        self.assertIn('tomlkit', text)
        self.assertIn('Total usage depends', text)

    def test_public_links_are_resolvable_and_include_guides(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        for target in re.findall(r'\]\(([^)]+)\)|src="([^"]+)"', text):
            value = target[0] or target[1]
            if '://' in value or value.startswith('#'):
                continue
            self.assertTrue((ROOT / value.split('#')[0]).exists(), value)
        for name in ('GETTING-STARTED', 'PLANS', 'OPERATIONS', 'PROVIDERS', 'SETTINGS', 'COMMANDS'):
            self.assertIn('docs/' + name + '.md', text)

    def test_future_product_changes_must_update_documentation(self):
        text = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertIn('documented in docs/ in the same change', text)

    def test_entrypoint_and_topic_anchors_resolve(self):
        sources = [ROOT / 'README.md'] + [ROOT / 'docs' / (name + '.md') for name in
                   ('GETTING-STARTED', 'PLANS', 'OPERATIONS', 'PROVIDERS', 'SETTINGS',
                    'INBOX', 'COMMANDS', 'DEVELOPMENT', 'DOCUMENTATION-MAP')]
        for source in sources:
            for link in re.findall(r'\]\(([^)]+)\)', source.read_text(encoding='utf-8')):
                if '://' in link or '#' not in link:
                    continue
                file, anchor = link.split('#', 1)
                target = source.parent / file if file else source
                text = re.sub(r'```.*?```', '', target.read_text(encoding='utf-8'), flags=re.S)
                headings = re.findall(r'^#{1,6} (.+)$', text, re.M)
                ids = {re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in headings}
                self.assertIn(anchor, ids, f'{source.name}: {link}')

    def test_removed_desktop_screenshot_is_not_shipped(self):
        self.assertFalse((ROOT / 'docs/HaiveQueen_Dashboard.png').exists())


if __name__ == '__main__':
    unittest.main()
