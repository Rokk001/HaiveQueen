import threading
import time
import unittest

from haivequeen import web_jobs


class WebJobsTests(unittest.TestCase):
    def finished(self, token):
        deadline = time.monotonic() + 3
        while web_jobs.status(token)['running'] and time.monotonic() < deadline:
            time.sleep(0.01)
        result = web_jobs.status(token)
        self.assertFalse(result['running'])
        return result

    def test_action_progress_is_live_and_duplicate_start_reuses_job(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def work(progress):
            progress('1/2')
            entered.set()
            release.wait(timeout=3)
            return {'completed': 2}

        job = web_jobs.start('test-progress', work)
        self.assertTrue(entered.wait(timeout=3))
        self.assertEqual(web_jobs.status(job['id'])['progress'], '1/2')
        self.assertEqual(web_jobs.start('test-progress', lambda progress: self.fail('duplicate action'))['id'], job['id'])
        release.set()
        self.assertEqual(self.finished(job['id'])['result'], {'completed': 2})

    def test_action_failure_is_retained_for_the_browser(self):
        def fail(progress):
            raise ValueError('measurement failed')
        job = web_jobs.start('test-failure', fail)
        self.assertEqual(self.finished(job['id'])['error'], 'measurement failed')
