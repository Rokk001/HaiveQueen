import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen.process_scan import scan_linux


class ProcessScanTests(unittest.TestCase):
    def test_live_command_and_empty_table(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp) / 'proc'
            entry = proc / '42'
            entry.mkdir(parents=True)
            version = Path(temp) / 'versions' / 'old'
            (entry / 'stat').write_text('42 (worker) S', encoding='utf-8')
            (entry / 'cmdline').write_bytes(('python\0' + str(version / 'worker.py') + '\0').encode())
            with patch('haivequeen.process_scan.os.readlink', return_value='/elsewhere'):
                self.assertTrue(scan_linux(version, proc))
                (entry / 'cmdline').write_bytes(b'python\0/elsewhere/worker.py\0')
                self.assertFalse(scan_linux(version, proc))

    def test_inaccessible_process_is_uncertain(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = Path(temp) / '42'
            entry.mkdir()
            (entry / 'stat').write_text('42 (worker) S', encoding='utf-8')
            with patch('haivequeen.process_scan.os.readlink', side_effect=PermissionError()):
                self.assertIsNone(scan_linux('/versions/old', Path(temp)))


if __name__ == '__main__':
    unittest.main()
