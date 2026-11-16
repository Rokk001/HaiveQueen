"""Machine settings and newly registered projects commit together."""
from pathlib import Path
from unittest import mock

from bzztest import RoundCase
from haivequeen import filetxn, settings, web_api


class SettingsAtomic(RoundCase):
    def setUp(self):
        super().setUp()
        self.path = web_api.settings_path(self.conn)
        settings.save_file(self.path, {})
        self.before = self.path.read_bytes()
        self.workspace = self.base / 'new-project'
        self.workspace.mkdir()
        view = web_api.settings_read(self.conn)
        view['data'].setdefault('projects', []).append({'name': 'new-project', 'workspace': str(self.workspace)})
        self.request = {'revision': view['revision'], 'data': view['data']}

    def registered(self):
        return self.conn.execute("SELECT workspace FROM projects WHERE name='new-project'").fetchone()

    def test_failed_settings_save_rolls_back_project_registration(self):
        with mock.patch.object(settings, 'save_file', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                web_api.settings_save(self.conn, self.request)
        self.assertIsNone(self.registered())
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_failed_transaction_preparation_preserves_file_and_database(self):
        with mock.patch.object(filetxn.Batch, 'prepare', side_effect=OSError('journal unavailable')):
            with self.assertRaisesRegex(OSError, 'journal unavailable'):
                web_api.settings_save(self.conn, self.request)
        self.assertIsNone(self.registered())
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_publication_failure_recovers_committed_settings_and_registration(self):
        write = filetxn.atomic_write
        def locked(path, contents, **kwargs):
            if Path(path) == self.path:
                raise PermissionError('settings locked')
            return write(path, contents, **kwargs)
        with mock.patch.object(filetxn, 'atomic_write', side_effect=locked):
            with self.assertRaises(filetxn.RecoveryError):
                web_api.settings_save(self.conn, self.request)
        self.assertIsNotNone(self.registered())
        loaded = web_api.settings_read(self.conn)
        self.assertIn('new-project', [p['name'] for p in loaded['data']['projects']])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM projects WHERE name='new-project'").fetchone()[0], 1)
