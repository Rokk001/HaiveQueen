"""Shared browser services must be usable without the retired desktop UI."""
import ast
import importlib
import unittest
from pathlib import Path
from unittest import mock

from bzztest import RoundCase
from haivequeen import provider_labels, settings, web_api, web_ui


class BrowserOnlyTests(unittest.TestCase):
    def test_shared_services_have_no_desktop_dependencies(self):
        for name in ('settings', 'provider_labels', 'dashboard_data', 'dashboard_overlays',
                     'plan_view', 'question_service'):
            module = importlib.import_module('haivequeen.' + name)
            tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ''] + [alias.name for alias in node.names]
                else:
                    continue
                self.assertFalse(any(n.startswith(('tkinter', 'gui')) for n in names), names)

    def test_desktop_window_sources_are_removed(self):
        package = Path(__file__).resolve().parents[1] / 'haivequeen'
        self.assertEqual(list(package.glob('gui*.py')), [])
        for path in package.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(a.name.startswith('tkinter') for a in node.names), path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith('tkinter'), path.name)

    def test_provider_readiness_and_saved_settings_use_shared_services(self):
        entry = {'name': 'example', 'enabled': True}
        with mock.patch('haivequeen.scheduler.get_providers', return_value=[{'login': True, 'notReady': ''}]):
            self.assertEqual(provider_labels.provider_state({'providers': [entry]}), (True, ''))
        with mock.patch.object(settings, 'load_migrated', return_value={'providers': [entry]}):
            self.assertTrue(provider_labels.saved_provider('unused.toml', entry))
            self.assertFalse(provider_labels.saved_provider('unused.toml', {**entry, 'enabled': False}))


class BrowserServiceTests(RoundCase):
    def test_browser_snapshot_and_settings_load_without_desktop_modules(self):
        import sys
        with mock.patch.dict(sys.modules, {'tkinter': None, 'haivequeen.gui': None,
                                          'haivequeen.gui_config': None}):
            self.assertIn('web-client.js', web_ui.page('token'))
            snapshot = web_api.execute(self.conn, 'state', {})
        self.assertIn('bees', snapshot)
