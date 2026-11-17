"""One console supervisor serves every ordinary start of a caller; a start that leaves the caller's job gets one outside it."""
import ctypes
import os
import subprocess
import sys
import textwrap
import time
import unittest
from unittest import mock

import bzztest  # noqa: F401  (puts this checkout first on the path)
from bzztest import ROOT
from haivequeen import console_tree


def run_child(code, **kwargs):
    script = 'import sys; sys.path.insert(0, %r)\nimport haivequeen.platform as osplat, subprocess\n' % str(ROOT) + textwrap.dedent(code)
    return subprocess.Popen([sys.executable, '-I', '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, **kwargs)


def alive(pid):
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_uint32()
    try:
        return bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


class RoutingTests(unittest.TestCase):
    def test_breakaway_and_inherit_all_get_a_broker_of_their_own(self):
        info = subprocess.STARTUPINFO()
        with mock.patch.object(console_tree, '_create_once', return_value='once') as once, \
             mock.patch.object(console_tree, '_Broker') as shared:
            args = [None, 'cmd', None, None, False, console_tree.BREAKAWAY, None, None, info]
            self.assertEqual(console_tree.create_process(object(), args), 'once')
            args = [None, 'cmd', None, None, True, 0, None, None, info]   # inherits every inheritable handle
            self.assertEqual(console_tree.create_process(object(), args), 'once')
        self.assertEqual(once.call_count, 2)
        shared.assert_not_called()

    def test_broker_outside_the_job_falls_back_when_the_job_forbids_it(self):
        import _winapi
        flags = []

        def original(*args):
            flags.append(args[5])
            if args[5] & console_tree.BREAKAWAY:
                raise PermissionError('breakaway not allowed')
            read, write = _winapi.CreatePipe(None, 0)
            return read, write, 1, 1
        handle = console_tree._launch(original, 1, 2, [], True)
        _winapi.CloseHandle(handle)
        self.assertEqual(flags, [console_tree.BROKER_FLAGS | console_tree.BREAKAWAY, console_tree.BROKER_FLAGS])

    def test_undelivered_request_takes_a_new_broker_once(self):
        first, second = mock.Mock(), mock.Mock()
        first.create.side_effect = console_tree._NotDelivered('gone')
        second.create.return_value = 'created'
        info = subprocess.STARTUPINFO()
        with mock.patch.dict(console_tree._shared, {'broker': first}, clear=True), \
             mock.patch.object(console_tree, '_Broker', return_value=second):
            self.assertEqual(console_tree.create_process(object(), [None, 'cmd', None, None, False, 0, None, None, info]), 'created')
        first.close.assert_called_once()


@unittest.skipUnless(sys.platform == 'win32', 'Windows console supervision')
class SharedBrokerTests(unittest.TestCase):
    def test_ordinary_starts_share_one_supervisor_and_keep_their_streams(self):
        child = run_child('''
            probe = [sys.executable, '-I', '-c', 'import os, sys; print(os.getppid()); print(sys.stdin.read().strip())']
            for word in ('one', 'two', 'three'):
                print(subprocess.run(probe, input=word, capture_output=True, text=True, check=True).stdout.split())
        ''')
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, err)
        rows = [eval(line) for line in out.splitlines()]
        self.assertEqual([word for _parent, word in rows], ['one', 'two', 'three'])
        self.assertEqual(len({parent for parent, _word in rows}), 1, rows)   # one supervisor created all three
        self.assertNotEqual(rows[0][0], str(child.pid))

    def test_later_starts_inherit_the_callers_current_environment(self):
        child = run_child('''
            import os
            probe = [sys.executable, '-I', '-c', 'import os; print(os.environ.get("BROKER_PROBE", "-"))']
            print(subprocess.run(probe, capture_output=True, text=True, check=True).stdout.strip())
            os.environ['BROKER_PROBE'] = 'changed'
            print(subprocess.run(probe, capture_output=True, text=True, check=True).stdout.strip())
        ''')
        out, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.split(), ['-', 'changed'])

    def test_detached_start_outside_the_job_survives_the_end_of_the_callers_job(self):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        job = k.CreateJobObjectW(None, None)
        self.assertTrue(job)
        limits = (ctypes.c_byte * 144)()                     # JOBOBJECT_EXTENDED_LIMIT_INFORMATION (x64)
        ctypes.c_uint32.from_buffer(limits, 16).value = 0x2000 | 0x800   # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
        self.assertTrue(k.SetInformationJobObject(job, 9, limits, ctypes.sizeof(limits)))
        child = run_child('''
            sys.stdin.readline()                              # wait until the test put this caller into the job
            leaf = osplat.popen_outside_job(subprocess.Popen, [sys.executable, '-I', '-c', 'import time; time.sleep(60)'],
                                            creationflags=0x00000008, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(leaf.pid, flush=True)
            sys.stdin.readline()
        ''')
        leaf = None
        try:
            self.assertTrue(k.AssignProcessToJobObject(job, int(child._handle)))
            child.stdin.write('go\n')
            child.stdin.flush()
            leaf = int(child.stdout.readline())
            k.CloseHandle(job)                                # the agent's job ends: all its members are killed
            job = None
            child.wait(timeout=30)
            time.sleep(1)
            self.assertTrue(alive(leaf), 'the detached start ended with the job')
        finally:
            if job:
                k.CloseHandle(job)
            if child.poll() is None:
                child.kill()
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
            if leaf:
                subprocess.run(['taskkill', '/F', '/PID', str(leaf)], capture_output=True)


if __name__ == '__main__':
    unittest.main()
