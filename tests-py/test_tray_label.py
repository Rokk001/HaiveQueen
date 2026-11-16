import unittest
from unittest.mock import patch
from haivequeen import tray

class TrayLabelTests(unittest.TestCase):
    def test_test_runtime_label_is_visible(self):
        with patch.dict(tray.os.environ, {'BZZ_TRAY_LABEL': 'HaiveQueen-Test'}):
            self.assertEqual(tray.display_name(), 'HaiveQueen-Test')
            self.assertEqual(tray.status_tip({'state': 'Stopped', 'stale': False}), 'HaiveQueen-Test - Stopped')

    def test_default_label_is_unchanged(self):
        with patch.dict(tray.os.environ, {'BZZ_TRAY_LABEL': ''}):
            self.assertEqual(tray.display_name(), 'HaiveQueen')

    def test_named_runtime_uses_distinct_icon(self):
        with patch.dict(tray.os.environ, {'BZZ_TRAY_LABEL': 'HaiveQueen-Test'}):
            self.assertEqual(tray.icon_path().name, 'app-test.ico')
            self.assertTrue(tray.icon_path().is_file())
        with patch.dict(tray.os.environ, {'BZZ_TRAY_LABEL': ''}):
            self.assertEqual(tray.icon_path().name, 'app.ico')
