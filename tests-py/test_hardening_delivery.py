"""Real files/Git at the controlled deployment boundary, including interruption."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import isolation

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import hardening_delivery as delivery


class DeliveryRecovery(unittest.TestCase):
    def test_partial_delivery_has_no_success_receipt_and_retry_preserves_completed_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for directory in ('config', 'data', 'venv', 'repo'):
                (root / directory).mkdir()
            (root / 'config/config.toml').write_text('')
            (root / 'data/haivequeen.db').touch()
            repo = root / 'repo'
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            (repo / 'a.txt').write_text('first')
            (repo / 'b.txt').write_text('second')
            git('add', '--', 'a.txt', 'b.txt')
            git('commit', '-qm', 'Seed delivery')
            head = git('rev-parse', 'HEAD')
            destination = root / 'data/delivery'
            copyfile = delivery.shutil.copyfile
            def interrupted(source, target):
                if source.name == 'b.txt':
                    raise OSError('Simulated interruption after first actual copy')
                return copyfile(source, target)
            with mock.patch.object(Path, 'cwd', return_value=repo), \
                 mock.patch.object(delivery.subprocess, 'check_output', side_effect=lambda args, **kw:
                     subprocess.run(args, cwd=repo, capture_output=True, check=True, **kw).stdout):
                with mock.patch.object(delivery.shutil, 'copyfile', side_effect=interrupted):
                    with self.assertRaises(OSError):
                        delivery.deliver(root, destination)
                self.assertFalse((destination / 'receipt.json').exists())
                first = destination / head / 'a.txt'
                timestamp = first.stat().st_mtime_ns
                receipt = delivery.deliver(root, destination)
                self.assertEqual(receipt['commit'], head)
                self.assertEqual(first.stat().st_mtime_ns, timestamp)
                self.assertEqual((destination / head / 'b.txt').read_text(), 'second')
                self.assertTrue((destination / 'receipt.json').is_file())
                self.assertEqual(delivery.deliver(root, destination), receipt)
