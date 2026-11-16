import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from haivequeen import stable_launcher, version_leases


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics required')
class RegistrySharingBarrierTests(unittest.TestCase):
    def test_real_reservation_and_launcher_with_owned_handle_and_recovery(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / 'versions' / 'old' / 'haivequeen'
            shutil.copytree(Path(version_leases.__file__).parent, package,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            marker = root / 'child-marker'
            (package / 'cli.py').write_text(
                'from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text("started")\n', encoding='utf-8')
            shutil.copyfile(stable_launcher.__file__, root / 'launcher.py')
            (root / 'active-version').write_text('old', encoding='utf-8')
            registry = root / 'version-leases.json'
            original = {'retained': {'version': 'old', 'pid': None, 'identity': None}}
            version_leases._save(root, original)
            caller = root / 'caller.py'
            caller.write_text(
                'import sys\nsys.path.insert(0, sys.argv[1])\n'
                'from haivequeen.version_leases import reserve\n'
                'print(reserve(sys.argv[2], "old"))\n', encoding='utf-8')
            def reserve():
                return subprocess.run([sys.executable, '-I', str(caller), str(package.parent), str(root)],
                                      capture_output=True, text=True, timeout=15)
            def launch():
                return subprocess.run([sys.executable, '-I', str(root / 'launcher.py'), str(marker)],
                                      capture_output=True, text=True, timeout=15)
            baseline = reserve()
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            token = baseline.stdout.strip()
            self.assertIn(token, version_leases._load(root))
            version_leases.release(root, token)
            baseline_launch = launch()
            self.assertEqual(baseline_launch.returncode, 0, baseline_launch.stderr)
            self.assertTrue(marker.exists())
            marker.unlink()
            self.assertEqual(version_leases._load(root), original)
            handle = kernel.CreateFileW(str(registry), 0x80000000, 0, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
            try:
                if os.environ.get('HQ_TEST_DISABLE_BARRIER') == '1':
                    kernel.CloseHandle(handle)
                    handle = None
                denied_reservation = reserve()
                denied_launcher = launch()
                print('under barrier reservation:', denied_reservation.returncode, denied_reservation.stderr.strip())
                print('under barrier launcher:', denied_launcher.returncode, denied_launcher.stderr.strip())
                print('under barrier pointer:', (root / 'active-version').read_text(encoding='utf-8'))
                print('under barrier child marker:', marker.exists())
                if handle:
                    buffer = ctypes.create_string_buffer(65536)
                    count = wintypes.DWORD()
                    self.assertTrue(kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None))
                    locked_registry = json.loads(buffer.raw[:count.value])
                    print('under barrier registry:', locked_registry)
                    self.assertEqual(locked_registry, original)
                self.assertNotEqual(denied_reservation.returncode, 0)
                self.assertNotEqual(denied_launcher.returncode, 0)
                self.assertIn('PermissionError', denied_reservation.stderr)
                self.assertIn('PermissionError', denied_launcher.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual((root / 'active-version').read_text(encoding='utf-8'), 'old')
            finally:
                if handle:
                    self.assertTrue(kernel.CloseHandle(handle))
            self.assertEqual(version_leases._load(root), original)
            recovered = reserve()
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn(recovered.stdout.strip(), version_leases._load(root))
            version_leases.release(root, recovered.stdout.strip())
            recovered_launch = launch()
            self.assertEqual(recovered_launch.returncode, 0, recovered_launch.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(version_leases._load(root), original)


if __name__ == '__main__':
    unittest.main()
