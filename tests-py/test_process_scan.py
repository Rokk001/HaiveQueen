import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import process_scan
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
                self.assertIsNone(scan_linux(version, proc))

    def test_inaccessible_process_is_uncertain(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = Path(temp) / '42'
            entry.mkdir()
            (entry / 'stat').write_text('42 (worker) S', encoding='utf-8')
            with patch('haivequeen.process_scan.os.readlink', side_effect=PermissionError()):
                self.assertIsNone(scan_linux('/versions/old', Path(temp)))


@unittest.skipUnless(sys.platform == 'win32', 'requires Windows process APIs')
class WindowsProcessScanTests(unittest.TestCase):
    def test_native_command_line_and_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            command_dir = Path(temp) / 'Command Path'
            cwd = Path(temp) / 'Working Directory'
            cwd.mkdir()
            marker = '--path=' + command_dir.as_posix().upper()
            with subprocess.Popen([sys.executable, '-I', '-c', 'import sys; sys.stdin.read()', marker],
                                  cwd=cwd, stdin=subprocess.PIPE) as child:
                with patch.object(process_scan._WindowsProcesses, 'pids',
                                  return_value=[0, child.pid]):
                    try:
                        self.assertIs(process_scan.scan(command_dir), True)
                        self.assertIs(process_scan.scan(cwd), True)
                        self.assertIs(process_scan.scan(Path(sys.executable).parent), True)
                        self.assertIs(process_scan.scan(Path(temp) / 'absent'), False)
                    finally:
                        child.stdin.close()
                        child.wait(timeout=10)
                    self.assertIs(process_scan.scan(cwd), False)

    def test_incomplete_table_and_unreadable_process_are_uncertain(self):
        directory = Path.cwd()
        with patch.object(process_scan._WindowsProcesses, 'pids', side_effect=OSError):
            self.assertIsNone(process_scan.scan(directory))

        class UnreadableProcess:
            def pids(self):
                return [0, 42, os.getpid()]

            def uses(self, pid, matches):
                if pid == 42:
                    return None
                return matches(r'\\?\C:\Versions\Old\tool.exe')

        with patch.object(process_scan, '_WindowsProcesses', UnreadableProcess):
            self.assertIsNone(process_scan.scan(Path(r'C:\versions\unrelated')))
            self.assertIs(process_scan.scan(Path(r'C:\versions\old')), True)

    def test_denied_process_open_does_not_prove_absence(self):
        processes = process_scan._WindowsProcesses()
        with patch.object(processes.kernel, 'OpenProcess', return_value=0), \
             patch.object(process_scan.ctypes, 'get_last_error', return_value=5):
            self.assertIsNone(processes.uses(42, lambda _: False))

    def test_failed_parameter_read_does_not_prove_absence(self):
        processes = process_scan._WindowsProcesses()
        with patch.object(processes, 'parameters', side_effect=OSError):
            self.assertIsNone(processes.uses(os.getpid(), lambda _: False))
            self.assertIs(processes.uses(os.getpid(), lambda value: value.lower().endswith('.exe')), True)


if __name__ == '__main__':
    unittest.main()
