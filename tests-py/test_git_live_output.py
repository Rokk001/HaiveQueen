import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from haivequeen import orchestrator


class GitLiveOutputTests(unittest.TestCase):
    def test_launch_failure_is_recorded_and_clears_pid(self):
        job = SimpleNamespace(cancelled=False, log_file='journal', pid=None)
        with patch.object(orchestrator.subprocess, 'Popen', side_effect=OSError('unavailable')), patch.object(orchestrator.action_logs, 'append') as append:
            code, output = orchestrator._git('.', 'status', job=job)
        self.assertEqual(code, 127)
        append.assert_called_once_with('journal', output)
        self.assertIsNone(job.pid)

    def test_stderr_and_unicode_are_streamed(self):
        real_popen = subprocess.Popen
        job = SimpleNamespace(cancelled=False, log_file='journal', pid=None)
        job.record_pid = lambda pid: setattr(job, 'pid', pid)
        script = "import sys;sys.stdout.buffer.write('Grüße\\n'.encode());sys.stdout.flush();sys.stderr.write('error\\n')"
        chunks = []
        with patch.object(orchestrator.subprocess, 'Popen', side_effect=lambda argv, **kw: real_popen([sys.executable, '-u', '-c', script], **kw)), patch.object(orchestrator.action_logs, 'append', side_effect=lambda path, text: chunks.append(text)):
            code, output = orchestrator._git('.', 'status', job=job)
        self.assertEqual(code, 0)
        self.assertEqual(output, 'Grüße\nerror\n')
        self.assertEqual(''.join(chunks), output)

    def test_output_is_published_before_exit_without_duplication(self):
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / 'release'
            script = ("import pathlib,time; print('first',flush=True); "
                      "p=pathlib.Path(" + repr(str(release)) + "); "
                      "\nwhile not p.exists(): time.sleep(.01)\nprint('last',flush=True)")
            real_popen = subprocess.Popen
            chunks, result = [], []
            job = SimpleNamespace(cancelled=False, log_file='journal', pid=None)
            job.record_pid = lambda pid: setattr(job, 'pid', pid)
            with patch.object(orchestrator.subprocess, 'Popen', side_effect=lambda argv, **kw: real_popen([sys.executable, '-u', '-c', script], **kw)), patch.object(orchestrator.action_logs, 'append', side_effect=lambda path, text: chunks.append(text)):
                worker = threading.Thread(target=lambda: result.append(orchestrator._git(folder, 'status', job=job)))
                worker.start()
                try:
                    deadline = time.monotonic() + 3
                    while not chunks and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIn('first', ''.join(chunks))
                    self.assertTrue(worker.is_alive())
                finally:
                    release.touch()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, [(0, 'first\nlast\n')])
                self.assertEqual(''.join(chunks), result[0][1])
                self.assertIsNone(job.pid)
