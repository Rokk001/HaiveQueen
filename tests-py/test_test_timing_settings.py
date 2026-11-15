import unittest
from unittest import mock
from haivequeen import config, settings, web_step_logs
class TimingSettings(unittest.TestCase):
    def test_default_and_configured_threshold(self):
        self.assertEqual(config.DEFAULT_CONFIG['slowTestSeconds'],10)
        with mock.patch.object(config,'load_config',return_value={'slowTestSeconds':27}):
            self.assertEqual(web_step_logs.timing_threshold(),27)
    def test_invalid_threshold_is_rejected(self):
        for value in (0,-1,True,'ten',float('inf')):
            with self.subTest(value=value):
                self.assertTrue(settings.check({'slowTestSeconds':value}))
        self.assertEqual(settings.check({'slowTestSeconds':2.5}),[])

    def test_setting_is_saved_and_visible_in_general_settings(self):
        import tempfile
        from pathlib import Path
        from haivequeen import web_settings, web_ui
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(config, 'config_dir', return_value=Path(folder)):
            path = Path(folder) / 'config.toml'
            loaded = web_settings.load(path)
            web_settings.save(path, {'slowTestSeconds': 17.5}, loaded['revision'])
            self.assertEqual(web_step_logs.timing_threshold(), 17.5)
            with self.assertRaises(ValueError):
                web_settings.save(path, {'slowTestSeconds': -1}, web_settings.load(path)['revision'])
            self.assertEqual(web_step_logs.timing_threshold(), 17.5)
        self.assertIn('slowTestSeconds', settings.GENERAL_LABELS)
        self.assertIn('slowTestSeconds', web_ui.page('test-token'))
