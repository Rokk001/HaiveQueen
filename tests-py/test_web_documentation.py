import unittest
from pathlib import Path

from haivequeen import cli


class WebDocumentationTests(unittest.TestCase):
    def test_documentation_does_not_reference_removed_desktop_modules(self):
        root = Path(__file__).resolve().parents[1]
        documents = [*root.glob('README*'), *(root / 'docs').rglob('*')]
        removed_module = r'\b(?:gui\.pyw?|gui_[a-zA-Z0-9_]+(?:\.pyw?)?|haivequeen\.gui)\b'
        for document in documents:
            if document.is_file() and document.suffix.lower() in ('.md', '.rst', '.txt'):
                with self.subTest(document=document.relative_to(root).as_posix()):
                    self.assertNotRegex(document.read_text(encoding='utf-8'), removed_module)

    def test_readme_describes_browser_plan_menu_and_current_controls(self):
        readme = Path('README.md').read_text(encoding='utf-8')
        for obsolete in ('right click', 'right-click', 'Queen menu', 'Plans window',
                         'window: `Space`', 'paused and resumed in the window',
                         'End Queen and Agents'):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, readme, msg='Obsolete README control: ' + obsolete)
        self.assertIn('**Plan menu.**', readme, msg='Browser plan menu documentation is missing')
        menu = readme.split('**Plan menu.**', 1)[1].split('\n\n', 1)[0]
        for label in ('**Plan** button', '**Restart Queen on `<provider>`**',
                      '**Stop gracefully**', '**Force stop**', '**Resume**',
                      '**Cancel plan**', '**Start**', '**Back to draft**'):
            with self.subTest(label=label):
                self.assertIn(label, menu)
        for phrase in ('Queen class', 'current provider is marked', 'stopped plan',
                       'running integration step', 'keeps working'):
            self.assertIn(phrase, menu)
        dashboard = readme.split('## Dashboard', 1)[1].split('## Configuration', 1)[0]
        for phrase in ('Orchestrator: **Start**, **Stop gracefully**, **Force stop**',
                       'Web server: **Stop**, **Restart**', '**Resume package**'):
            self.assertIn(phrase, dashboard)

    def test_web_help_and_guide_cover_lifecycle(self):
        help_text = cli.build_parser()._subparsers._group_actions[0].choices['web'].format_help()
        for word in ('setup', 'start', 'stop', 'restart', 'off'):
            self.assertIn(word, help_text)
        self.assertIn('loopback', help_text)
        for phrase in ('address', 'port', 'certificate', 'key', 'login', 'tray', 'Force stop', 'Stop gracefully', 'Restart Queen', 'Resume package', 'systemd', 'other systems', 'docs/WEB-API.md'):
            self.assertIn(phrase.lower(), ' '.join(help_text.lower().split()))
        guide = Path('docs/WEB-API.md').read_text(encoding='utf-8')
        for phrase in ('systemd', 'bzz web setup', 'bzz web restart', 'Force stop', 'tray', 'background service'):
            self.assertIn(phrase.lower(), guide.lower())
        readme = Path('README.md').read_text(encoding='utf-8')
        self.assertIn('docs/WEB-API.md', readme)

    def test_provider_login_is_documented_in_the_web_interface(self):
        profile = Path('docs/PROFILE.md').read_text(encoding='utf-8')
        self.assertNotIn('visible terminal window', profile)
        self.assertIn('web interface', profile)
        self.assertIn('device login', profile)
        self.assertIn('code', profile)
