import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen.process_scan import scan_linux


class LinuxUnmatchedProcessTests(unittest.TestCase):
    def test_live_nonmatch_is_uncertain_and_match_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp) / 'proc'
            version = Path(temp) / 'versions' / 'old'
            first = proc / '1'
            first.mkdir(parents=True)
            (first / 'stat').write_text('1 (worker) S', encoding='utf-8')
            (first / 'cmdline').write_bytes(b'python\0neutral\0')
            with patch('haivequeen.process_scan.os.readlink', return_value='/neutral'):
                self.assertIsNone(scan_linux(version, proc))
                second = proc / '2'
                second.mkdir()
                (second / 'stat').write_text('2 (worker) S', encoding='utf-8')
                (second / 'cmdline').write_bytes(str(version / 'worker.py').encode())
                self.assertIs(scan_linux(version, proc), True)

    def test_empty_zombie_and_vanished_are_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp) / 'proc'
            proc.mkdir()
            version = Path(temp) / 'old'
            self.assertIs(scan_linux(version, proc), False)
            zombie = proc / '1'
            zombie.mkdir()
            (zombie / 'stat').write_text('1 (worker) Z', encoding='utf-8')
            self.assertIs(scan_linux(version, proc), False)
            vanished = proc / '2'
            vanished.mkdir()
            original = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == vanished / 'stat':
                    vanished.rmdir()
                    raise FileNotFoundError()
                return original(path, *args, **kwargs)

            with patch.object(Path, 'read_text', read_text):
                self.assertIs(scan_linux(version, proc), False)


if __name__ == '__main__':
    unittest.main()
