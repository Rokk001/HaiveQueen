import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import config, web_settings


class WebSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.patch = mock.patch.object(config, 'config_dir', return_value=self.base)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_round_trip_and_stale_refusal_for_both_files(self):
        for project in (False, True):
            path = self.base / ('project.toml' if project else 'config.toml')
            path.write_text('# keep\nslots = 1\n', encoding='utf-8')
            first = json.loads(json.dumps(web_settings.load(path)))
            second = json.loads(json.dumps(web_settings.load(path)))
            first['settings']['slots'] = 2
            saved = web_settings.save(path, first['settings'], first['revision'], project=project)
            self.assertEqual(saved['settings']['slots'], 2)
            before = path.read_bytes()
            second['settings']['slots'] = 3
            with self.assertRaisesRegex(ValueError, 'changed.*reload'):
                web_settings.save(path, second['settings'], second['revision'], project=project)
            self.assertEqual(path.read_bytes(), before)
            self.assertIn('# keep', before.decode())

    def test_external_change_and_missing_file_are_detected(self):
        path = self.base / 'config.toml'
        loaded = web_settings.load(path)
        path.write_text('slots = 9\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed.*reload'):
            web_settings.save(path, {'slots': 1}, loaded['revision'])
        self.assertEqual(path.read_text(encoding='utf-8'), 'slots = 9\n')

    def test_project_save_does_not_copy_settings_to_database(self):
        path = self.base / 'project.toml'
        path.write_text('slots = 1\n', encoding='utf-8')
        conn = mock.Mock()
        loaded = web_settings.load(path)
        web_settings.save(path, {'slots': 4}, loaded['revision'], project=True, conn=conn)
        conn.execute.assert_not_called()
        self.assertIn('slots = 4', path.read_text(encoding='utf-8'))

    def test_save_requires_revision(self):
        path = self.base / 'config.toml'
        with self.assertRaises(ValueError):
            web_settings.save(path, {'slots': 1}, None)
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
