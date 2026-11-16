import ctypes
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from ctypes import wintypes
from pathlib import Path

from haivequeen import process_scan, version_leases


@unittest.skipUnless(sys.platform == 'win32', 'Windows inspector characterization')
class LoadedLegacyCallerScanTests(unittest.TestCase):
    def test_loaded_caller_and_path_bearing_control(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'versions' / '1.0.0'
            shutil.copytree(Path(version_leases.__file__).parent, old / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            code = ("import os,sys\n"
                    "sys.path.insert(0, os.environ['LEGACY_IMPORT_ROOT'])\n"
                    "from haivequeen import version_leases\n"
                    "print('loaded', flush=True)\n"
                    "sys.stdin.readline()\n")
            env = os.environ.copy()
            env['LEGACY_IMPORT_ROOT'] = str(old)
            inspector = process_scan._WindowsProcesses()
            paths = {process_scan._windows_path_text(str(p)).rstrip('\\')
                     for p in (old.absolute(), old.resolve())}
            def matches(value):
                value = process_scan._windows_path_text(value)
                return any(path in value for path in paths)

            def inspect(control):
                args = [sys.executable, '-I', '-c', code]
                if control:
                    args.append(str(old))
                child = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                lines = queue.Queue()
                threading.Thread(target=lambda: lines.put(child.stdout.readline().strip()),
                                 daemon=True).start()
                try:
                    self.assertEqual(lines.get(timeout=10), 'loaded')
                    self.assertIsNone(child.poll())
                    handle = inspector.kernel.OpenProcess(inspector._QUERY | inspector._WAIT | inspector._READ,
                                                          False, child.pid)
                    fields = None
                    error = None
                    if handle:
                        try:
                            image = ctypes.create_unicode_buffer(32768)
                            size = wintypes.DWORD(len(image))
                            if not inspector.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
                                raise ctypes.WinError(ctypes.get_last_error())
                            fields = [image.value, *inspector.parameters(handle)]
                        except (OSError, ValueError, OverflowError) as exc:
                            error = repr(exc)
                        finally:
                            inspector.kernel.CloseHandle(handle)
                    else:
                        error = f'OpenProcess error {ctypes.get_last_error()}'
                    result = inspector.uses(child.pid, matches)
                    print(f'control={control} inspector={result!r} fields={fields!r} access_error={error!r}', flush=True)
                    if fields is not None and all(field is not None for field in fields):
                        self.assertEqual([matches(field) for field in fields],
                                         [False, False, control])
                    else:
                        self.assertIsNone(result, 'unreadable fields must remain uncertain')
                    return result
                finally:
                    if child.poll() is None:
                        child.stdin.write('\n')
                        child.stdin.flush()
                    try:
                        out, err = child.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        out, err = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, err)

            hidden = inspect(False)
            control = inspect(True)
            if hidden is not None and control is not None:
                self.assertTrue(control)
                if os.environ.get('HQ_SCAN_BASELINE') == '1':
                    self.assertTrue(hidden, 'baseline: loaded module is not visible to field scan')
                self.assertFalse(hidden)
            else:
                print('Inspection unavailable; no absence inferred.', flush=True)


if __name__ == '__main__':
    unittest.main()
