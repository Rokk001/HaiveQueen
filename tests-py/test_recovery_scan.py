from functools import partial
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import process_scan, version_leases


class RecoveryScanTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32' or sys.platform.startswith('linux'),
                         'requires process table inspection')
    def test_child_survives_crash_before_lease_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'versions' / 'old'
            old.mkdir(parents=True)
            (root / 'versions' / 'active').mkdir()
            (root / 'active-version').write_text('active', encoding='utf-8')
            stop = root / 'stop'
            launcher = '''
import os
from pathlib import Path
import subprocess
import sys
from haivequeen import version_leases

def crash_after_spawn(*args, **kwargs):
    child = subprocess.Popen(*args, **kwargs)
    print(child.pid, flush=True)
    os._exit(23)

old, stop = map(Path, sys.argv[1:])
worker = """
import sys, time
from pathlib import Path
stop = Path(sys.argv[1])
deadline = time.monotonic() + 30
while not stop.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
"""
version_leases.launch(old, crash_after_spawn, [sys.executable, '-I', '-c', worker, str(stop), str(old)],
                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''
            try:
                parent = subprocess.run([sys.executable, '-B', '-c', launcher, str(old), str(stop)],
                                        cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(parent.returncode, 23, parent.stderr)
                pid = int(parent.stdout)
                leases = version_leases._load(root)
                self.assertEqual(list(leases.values()), [{'version': 'old', 'pid': None, 'identity': None}])
                self.assertIs(process_scan.scan(old), True)
                self.assertEqual(version_leases.collect(root), [])
                self.assertEqual(version_leases._load(root), leases)
                self.assertTrue(old.is_dir())
            finally:
                stop.touch()

            # Unrelated protected processes may make the full table inconclusive.
            # Restrict enumeration to the real child for the absence phase.
            if sys.platform == 'win32':
                table = patch.object(process_scan._WindowsProcesses, 'pids', return_value=[pid])
            else:
                proc = root / 'proc'
                proc.mkdir()
                (proc / str(pid)).symlink_to(Path('/proc') / str(pid), target_is_directory=True)
                table = patch.object(process_scan, 'scan_linux', partial(process_scan.scan_linux, proc=proc))
            with table:
                deadline = time.monotonic() + 10
                result = process_scan.scan(old)
                while result is not False and time.monotonic() < deadline:
                    time.sleep(0.02)
                    result = process_scan.scan(old)
                self.assertIs(result, False)
                self.assertEqual(version_leases.collect(root), ['old'])
            self.assertFalse(old.exists())
            self.assertEqual(version_leases._load(root), {})

    def test_unpublished_child_and_uncertainty_prevent_removal_then_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            token = version_leases.reserve(root, 'old')
            with patch.object(version_leases.process_scan, 'scan', side_effect=[True, None, False]):
                self.assertEqual(version_leases.collect(root), [])
                self.assertIn(token, version_leases._load(root))
                self.assertEqual(version_leases.collect(root), [])
                self.assertIn(token, version_leases._load(root))
                self.assertEqual(version_leases.collect(root), ['old'])
                self.assertNotIn(token, version_leases._load(root))

    def test_published_pid_without_birth_identity_requires_clear_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            token = version_leases.reserve(root, 'old')
            with patch.object(version_leases.platform, 'process_identity', return_value=None):
                version_leases.publish(root, token, 42)
            with patch.object(version_leases.platform, 'process_absent', return_value=True), \
                 patch.object(process_scan, 'scan', side_effect=[None, True, False]):
                for _ in range(2):
                    self.assertEqual(version_leases.collect(root), [])
                    self.assertIn(token, version_leases._load(root))
                self.assertEqual(version_leases.collect(root), ['old'])
                self.assertEqual(version_leases._load(root), {})

    def test_unleased_folder_also_requires_clear_scan_and_retries_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('active', 'old'):
                (root / 'versions' / name).mkdir(parents=True)
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(version_leases.process_scan, 'scan', return_value=False), \
                 patch.object(version_leases.shutil, 'rmtree', side_effect=PermissionError):
                self.assertEqual(version_leases.collect(root), [])
                self.assertTrue((root / 'versions' / 'old').is_dir())
            with patch.object(version_leases.process_scan, 'scan', return_value=False):
                self.assertEqual(version_leases.collect(root), ['old'])

    @unittest.skipUnless(sys.platform == 'win32', 'requires Windows file sharing semantics')
    def test_windows_cleanup_retries_after_locked_file_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'versions' / 'old'
            old.mkdir(parents=True)
            (root / 'versions' / 'active').mkdir()
            (root / 'active-version').write_text('active', encoding='utf-8')
            with patch.object(process_scan, 'scan', return_value=False):
                with (old / 'locked.txt').open('w', encoding='utf-8'):
                    self.assertEqual(version_leases.collect(root), [])
                    self.assertTrue(old.is_dir())
                self.assertEqual(version_leases.collect(root), ['old'])
            self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main()
