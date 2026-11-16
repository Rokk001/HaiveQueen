import io
import queue
import subprocess
import unittest
from contextlib import ExitStack
from unittest import mock
from haivequeen import codex_quota


class QuotaDeadline(unittest.TestCase):
    def setup_call(self, stack):
        process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO())
        stack.enter_context(mock.patch.object(codex_quota.shutil, 'which', return_value='codex'))
        stack.enter_context(mock.patch.object(codex_quota.profile, 'environment', return_value={}))
        stack.enter_context(mock.patch.object(codex_quota.subprocess, 'Popen', return_value=process))
        stack.enter_context(mock.patch.object(codex_quota.threading, 'Thread'))
        return process

    def test_unrelated_stream_cannot_extend_deadline(self):
        with ExitStack() as stack:
            self.setup_call(stack)
            replies = mock.Mock()
            replies.get.side_effect = [{'method': 'notice'}, {'id': 1}, {'id': 2, 'result': {'late': True}}]
            stack.enter_context(mock.patch.object(codex_quota.queue, 'Queue', return_value=replies))
            stack.enter_context(mock.patch.object(codex_quota.time, 'monotonic', side_effect=[0, .5, 2, 3]))
            with self.assertRaises(TimeoutError):
                codex_quota.read(timeout=1)
            self.assertEqual(replies.get.call_count, 1)

    def test_response_buffer_is_bounded(self):
        with ExitStack() as stack:
            self.setup_call(stack)
            factory = stack.enter_context(mock.patch.object(codex_quota.queue, 'Queue', wraps=queue.Queue))
            with self.assertRaises(TimeoutError):
                codex_quota.read(timeout=.01)
            args, kwargs = factory.call_args
            self.assertGreater(kwargs.get('maxsize', args[0] if args else 0), 0)

    def test_cleanup_never_uses_unbounded_wait(self):
        with ExitStack() as stack:
            process = self.setup_call(stack)
            process.wait.side_effect = [subprocess.TimeoutExpired('codex', 2), subprocess.TimeoutExpired('codex', 2), 0]
            with self.assertRaises(TimeoutError):
                codex_quota.read(timeout=.01)
            self.assertTrue(all(call.kwargs.get('timeout', 0) > 0 for call in process.wait.call_args_list))
            process.terminate.assert_called_once()
            process.kill.assert_called_once()

    def test_handshake_and_quota_result(self):
        with ExitStack() as stack:
            self.setup_call(stack)
            replies = mock.Mock()
            replies.get.side_effect = [{'id': 1, 'result': {}}, {'id': 2, 'result': {'remaining': 42}}]
            stack.enter_context(mock.patch.object(codex_quota.queue, 'Queue', return_value=replies))
            self.assertEqual(codex_quota.read(), {'remaining': 42})

class QuotaHelperProcesses(unittest.TestCase):
    def test_real_helpers_finish_and_release_reader(self):
        import sys
        import threading
        import time
        scripts = {
            'flood': "import sys\nwhile True: print('{\"method\":\"notice\"}', flush=True)",
            'silent': 'import time; time.sleep(30)',
            'success': "import sys,json\nfor line in sys.stdin:\n m=json.loads(line)\n if 'id' in m: print(json.dumps({'id':m['id'],'result':{'remaining':42}}),flush=True)",
        }
        popen = subprocess.Popen
        thread_type = threading.Thread
        for name, script in scripts.items():
            with self.subTest(helper=name), ExitStack() as stack:
                processes, readers = [], []
                def launch(_command, **kwargs):
                    child = popen([sys.executable, '-u', '-c', script], **kwargs)
                    processes.append(child)
                    return child
                def thread(*args, **kwargs):
                    reader = thread_type(*args, **kwargs)
                    readers.append(reader)
                    return reader
                stack.enter_context(mock.patch.object(codex_quota.shutil, 'which', return_value=sys.executable))
                stack.enter_context(mock.patch.object(codex_quota.profile, 'environment', return_value=None))
                stack.enter_context(mock.patch.object(codex_quota.subprocess, 'Popen', side_effect=launch))
                stack.enter_context(mock.patch.object(codex_quota.threading, 'Thread', side_effect=thread))
                started = time.monotonic()
                try:
                    if name == 'success':
                        self.assertEqual(codex_quota.read(timeout=5), {'remaining': 42})
                    else:
                        with self.assertRaises(TimeoutError):
                            codex_quota.read(timeout=.2)
                    self.assertLess(time.monotonic() - started, 6)
                    self.assertIsNotNone(processes[0].poll())
                    self.assertFalse(readers[0].is_alive())
                    self.assertTrue(processes[0].stdout.closed)
                finally:
                    for child in processes:
                        if child.poll() is None:
                            child.kill()
                            child.wait(timeout=5)
