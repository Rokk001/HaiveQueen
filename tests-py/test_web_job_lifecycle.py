import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import web_jobs


class OwnerJobLifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {'BZZ_DATA_DIR': folder.name})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (('_jobs', {}), ('_threads', {}), ('_closing', False)):
            mocked = patch.object(web_jobs, name, value, create=True)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_shutdown_waits_for_work_and_recovery_retains_result(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def work(progress):
            progress('Measuring')
            entered.set()
            release.wait(3)
            return {'completed': 2}

        job = web_jobs.start('benchmark:test', work)
        self.assertTrue(entered.wait(3))
        web_jobs.begin_shutdown()
        with self.assertRaisesRegex(ValueError, 'stopping'):
            web_jobs.start('tools:install', lambda progress: None)
        waiter = threading.Thread(target=lambda: (web_jobs.finish_shutdown(), stopped.set()))
        waiter.start()
        self.assertFalse(stopped.wait(.05))
        release.set()
        waiter.join(3)
        self.assertTrue(stopped.is_set())
        web_jobs._jobs.clear()
        web_jobs.recover()
        result = web_jobs.status(job['id'])
        self.assertFalse(result['running'])
        self.assertEqual(result['result'], {'completed': 2})
        self.assertEqual(result['progress'], 'Measuring')

    def test_interrupted_job_is_visible_and_not_replayed(self):
        folder = self.root / 'owner-jobs'
        folder.mkdir()
        token = 'a' * 32
        (folder / (token + '.json')).write_text(json.dumps({
            'id': token, 'key': 'tools:install', 'running': True,
            'progress': 'One file written', 'result': None, 'error': ''}), encoding='utf-8')
        web_jobs.recover()
        result = web_jobs.status(token)
        self.assertFalse(result['running'])
        self.assertTrue(result['interrupted'])
        self.assertIn('unknown', result['error'].lower())
        self.assertEqual(result['progress'], 'One file written')
        self.assertIn(token, [job['id'] for job in web_jobs.recent()])
        web_jobs._jobs.clear()
        web_jobs.recover()
        self.assertTrue(web_jobs.status(token)['interrupted'])

    def test_storage_failure_does_not_start_action(self):
        with patch('haivequeen.filetxn.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                web_jobs.start('tools:install', lambda progress: self.fail('must not start'))
        self.assertFalse(web_jobs._jobs)

    def test_corrupt_history_remains_visible_without_overwriting_evidence(self):
        folder = self.root / 'owner-jobs'
        folder.mkdir()
        token = 'b' * 32
        path = folder / (token + '.json')
        path.write_bytes(b'{broken')
        web_jobs.recover()
        self.assertTrue(web_jobs.status(token)['interrupted'])
        self.assertIn('unreadable', web_jobs.status(token)['error'])
        self.assertEqual(path.read_bytes(), b'{broken')
