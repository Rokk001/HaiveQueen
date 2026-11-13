"""Browser services and shared provider settings."""
import unittest
from unittest import mock

from bzztest import RoundCase
from haivequeen import provider_labels, settings, web_api, web_ui


class BrowserOnlyTests(unittest.TestCase):
    def test_provider_readiness_and_saved_settings_use_shared_services(self):
        entry = {'name': 'example', 'enabled': True}
        with mock.patch('haivequeen.scheduler.get_providers', return_value=[{'login': True, 'notReady': ''}]):
            self.assertEqual(provider_labels.provider_state({'providers': [entry]}), (True, ''))
        with mock.patch.object(settings, 'load_migrated', return_value={'providers': [entry]}):
            self.assertTrue(provider_labels.saved_provider('unused.toml', entry))
            self.assertFalse(provider_labels.saved_provider('unused.toml', {**entry, 'enabled': False}))


class BrowserServiceTests(RoundCase):
    def test_browser_snapshot_and_page(self):
        self.assertIn('web-client.js', web_ui.page('token'))
        snapshot = web_api.execute(self.conn, 'state', {})
        self.assertIn('bees', snapshot)
