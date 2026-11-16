import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from haivequeen import version_leases
from haivequeen.stable_launcher import lifetime_lock


class LegacyDirectoryIsolationTests(unittest.TestCase):
    def test_renamed_directory_against_queued_and_fresh_legacy_callers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'versions' / '1.0.0'
            isolated = root / 'versions' / 'isolated-old'
            shutil.copytree(Path(version_leases.__file__).parent, old / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            caller = old / 'caller.py'
            caller.write_text(
                "import sys\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from haivequeen import version_leases\n"
                "print('loaded', flush=True)\n"
                "try:\n"
                "    token = version_leases.reserve(sys.argv[2], '1.0.0')\n"
                "except FileNotFoundError:\n"
                "    print('missing', flush=True)\n"
                "else:\n"
                "    print('reserved:' + token, flush=True)\n", encoding='utf-8')
            child = None
            signals = queue.Queue()
            try:
                with lifetime_lock(root):
                    child = subprocess.Popen([sys.executable, '-I', str(caller), str(old), str(root)],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    threading.Thread(target=lambda: signals.put(child.stdout.readline().strip()),
                                     daemon=True).start()
                    self.assertEqual(signals.get(timeout=10), 'loaded')
                    self.assertIsNone(child.poll())
                    rename_error = None
                    try:
                        old.rename(isolated)
                    except OSError as exc:
                        rename_error = exc
                    if rename_error is None:
                        self.assertFalse(old.exists())
                        self.assertTrue(isolated.is_dir())
                        fresh = subprocess.run([sys.executable, '-I', str(caller), str(old), str(root)],
                                               capture_output=True, text=True, timeout=10)
                        self.assertNotEqual(fresh.returncode, 0)
                        self.assertNotIn('loaded', fresh.stdout)
                        print('rename succeeded; fresh old-path caller exit:', fresh.returncode)
                    else:
                        self.assertTrue(old.is_dir())
                        self.assertFalse(isolated.exists())
                        print('rename denied:', repr(rename_error), 'winerror:',
                              getattr(rename_error, 'winerror', None))
                out, err = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, err)
                outcome = out.strip()
                print('already-loaded caller outcome:', outcome)
                if rename_error is None:
                    self.assertEqual(outcome, 'missing')
                    self.assertEqual(version_leases._load(root), {})
                else:
                    self.assertTrue(outcome.startswith('reserved:'), outcome)
                    token = outcome.removeprefix('reserved:')
                    self.assertEqual(version_leases._load(root)[token]['version'], '1.0.0')
            finally:
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
