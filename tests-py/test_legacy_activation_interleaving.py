import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from haivequeen import release_activation, version_layout, version_leases


class LegacyActivationInterleavingTests(unittest.TestCase):
    def test_queued_version_local_caller_reserves_old_after_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'versions' / '1.0.0'
            new = root / 'versions' / '2.0.0'
            shutil.copytree(Path(version_leases.__file__).parent, old / 'haivequeen',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (new / 'haivequeen').mkdir(parents=True)
            (new / 'haivequeen' / 'cli.py').write_text('pass', encoding='utf-8')
            version_layout.set_active(root, '1.0.0')
            code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                    "from haivequeen import version_leases; "
                    "print('attempting', flush=True); "
                    "token = version_leases.reserve(sys.argv[2], '1.0.0'); "
                    "print(token, flush=True)")
            child = None
            signals = queue.Queue()
            try:
                def verify_under_lock():
                    nonlocal child
                    child = subprocess.Popen([sys.executable, '-I', '-c', code, str(old), str(root)],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    threading.Thread(target=lambda: signals.put(child.stdout.readline().strip()),
                                     daemon=True).start()
                    self.assertEqual(signals.get(timeout=10), 'attempting')
                    self.assertEqual((root / 'active-version').read_text().strip(), '1.0.0')
                    return True

                def first_round():
                    self.assertEqual((root / 'active-version').read_text().strip(), '2.0.0')
                    out, err = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, err)
                    token = out.strip()
                    self.assertTrue(token, err)
                    self.assertEqual(version_leases._load(root)[token]['version'], '1.0.0')
                    return True

                self.assertTrue(release_activation.activate(root, '2.0.0', first_round,
                                                            target_verifier=verify_under_lock))
                self.assertEqual((root / 'active-version').read_text().strip(), '2.0.0')
            finally:
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
