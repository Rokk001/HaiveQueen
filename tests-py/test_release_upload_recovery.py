"""Manifest and cleanup failure cases, using the publication entry point."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock

from haivequeen import publish


class UploadRecovery(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.env = SimpleNamespace(name='test', settings={})
        self.state = {'merged': 'a' * 40}
        self.settings = {'releaseMode': 'local', 'releaseFolder': str(self.folder)}
        self.assets = {}
        self.uploads = []
        self.persisted = None
        self.service = SimpleNamespace(name='test', repository='team/repo',
            capabilities={'release_prepare', 'release_upload', 'release_asset_digest'},
            credential=lambda: 'credential', call=self.call)
        for patch in (mock.patch('haivequeen.git_provider.for_project', return_value=(self.service, [])),
                      mock.patch('haivequeen.publish._release_tag', return_value='v1.0.0-dev.1'),
                      mock.patch('haivequeen.orchestrator.save', side_effect=self.save)):
            patch.start()
            self.addCleanup(patch.stop)

    def save(self, *args):
        self.persisted = json.loads(json.dumps(args[-1]))

    def call(self, op, *args):
        if op == 'release_prepare':
            return {'id': 1, 'assets': list(self.assets.values())}
        if op == 'release_upload':
            _, name, content, _ = args
            self.uploads.append((name, content))
            self.assets[name] = {'name': name, 'sha256': hashlib.sha256(content).hexdigest()}
            return self.assets[name]
        if op == 'release_asset_digest':
            return args[1]['sha256']
        raise AssertionError(op)

    def publish(self):
        return publish.pipeline_release(None, self.env, 'plan', self.state, self.folder, self.settings)

    def test_cleanup_failure_after_upload_resumes_without_reupload(self):
        (self.folder / 'first.bin').write_bytes(b'first')
        (self.folder / 'second.bin').write_bytes(b'second')
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path.name == 'second.bin':
                raise OSError('cleanup interrupted')
            return unlink(path, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', interrupted), self.assertRaisesRegex(OSError, 'interrupted'):
            self.publish()
        self.assertTrue(self.persisted['release']['uploaded'])
        self.assertFalse((self.folder / 'first.bin').exists())
        self.assertTrue((self.folder / 'second.bin').exists())
        self.state = self.persisted
        self.assertIn('published', self.publish())
        self.assertEqual(len(self.uploads), 2)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_cleanup_never_deletes_changed_files_after_restart(self):
        path = self.folder / 'artifact.bin'
        path.write_bytes(b'original')
        with mock.patch.object(Path, 'unlink', side_effect=OSError('busy')), self.assertRaises(OSError):
            self.publish()
        self.state = self.persisted
        path.write_bytes(b'new build')
        with self.assertRaisesRegex(ValueError, 'changed before cleanup'):
            self.publish()
        self.assertEqual(path.read_bytes(), b'new build')

    def test_recursive_upload_keeps_basename_and_removes_empty_subfolders(self):
        nested = self.folder / 'nested'
        nested.mkdir()
        (nested / 'Build output.zip').write_bytes(b'original')
        self.publish()
        self.assertEqual(self.uploads, [('Build output.zip', b'original')])
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_duplicate_basenames_fail_before_creating_release(self):
        (self.folder / 'artifact.zip').write_bytes(b'one')
        (self.folder / 'nested').mkdir()
        (self.folder / 'nested/artifact.zip').write_bytes(b'two')
        with self.assertRaisesRegex(ValueError, 'duplicate file names'):
            self.publish()
        self.assertEqual(self.uploads, [])
        self.assertNotIn('tag', self.state['release'])

    def test_remote_mismatch_keeps_local_file(self):
        path = self.folder / 'artifact.zip'
        path.write_bytes(b'original')
        self.assets[path.name] = {'name': path.name, 'sha256': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'cannot be verified'):
            self.publish()
        self.assertEqual(path.read_bytes(), b'original')
        self.assertEqual(self.uploads, [])

    def test_reconfigured_folder_cannot_redirect_cleanup(self):
        path = self.folder / 'artifact.bin'
        path.write_bytes(b'original')
        with mock.patch.object(Path, 'unlink', side_effect=OSError('busy')), self.assertRaises(OSError):
            self.publish()
        self.settings['releaseFolder'] = str(self.folder / 'other')
        with self.assertRaisesRegex(ValueError, 'folder changed'):
            self.publish()
        self.assertTrue(path.exists())

    def test_new_files_after_manifest_are_retained_and_reported(self):
        (self.folder / 'artifact.bin').write_bytes(b'original')
        with mock.patch.object(Path, 'unlink', side_effect=OSError('busy')), self.assertRaises(OSError):
            self.publish()
        (self.folder / 'new.bin').write_bytes(b'new')
        with self.assertRaisesRegex(ValueError, 'gained files'):
            self.publish()
        self.assertEqual((self.folder / 'new.bin').read_bytes(), b'new')

    def test_unset_and_missing_and_directory_only_folders_do_not_publish(self):
        for folder in ('', str(self.folder / 'missing'), str(self.folder)):
            with self.subTest(folder=folder):
                self.settings['releaseFolder'] = folder
                self.state = {'merged': 'a' * 40}
                (self.folder / 'empty').mkdir(exist_ok=True)
                self.assertIn('no release made', self.publish())
                self.assertNotIn('tag', self.state['release'])
        self.assertEqual(self.uploads, [])
