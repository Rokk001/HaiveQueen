import unittest
from pathlib import Path
from unittest import mock

from haivequeen import process_scan


class Kernel:
    def __init__(self, waits=(258, 258), image=r'C:\other\python.exe'):
        self.waits = iter(waits)
        self.image = image

    def OpenProcess(self, *args):
        return 1

    def WaitForSingleObject(self, *args):
        return next(self.waits)

    def QueryFullProcessImageNameW(self, process, flags, buffer, size):
        buffer.value = self.image
        return 1

    def CloseHandle(self, process):
        pass


class WindowsUnmatchedProcessTests(unittest.TestCase):
    def inspector(self, waits=(258, 258), image=r'C:\other\python.exe', params=()):
        inspector = process_scan._WindowsProcesses.__new__(process_scan._WindowsProcesses)
        inspector.kernel = Kernel(waits, image)
        inspector.parameters = lambda process: params
        return inspector

    def test_live_unmatched_is_uncertain(self):
        inspector = self.inspector(params=(r'C:\other', 'python -m worker'))
        self.assertIsNone(inspector.uses(42, lambda value: 'target' in value))

    def test_matching_image_and_parameters_are_positive(self):
        inspector = self.inspector(image=r'C:\target\python.exe')
        self.assertIs(inspector.uses(42, lambda value: 'target' in value), True)
        inspector = self.inspector(params=(r'C:\target\script.py',))
        self.assertIs(inspector.uses(42, lambda value: 'target' in value), True)

    def test_verified_exit_is_absent(self):
        inspector = self.inspector(waits=(258, 0))
        self.assertIs(inspector.uses(42, lambda value: False), False)
        inspector = self.inspector(waits=(0,))
        self.assertIs(inspector.uses(42, lambda value: False), False)

    def test_scan_propagates_uncertainty_and_positive_match(self):
        inspector = self.inspector()
        inspector.pids = lambda: [42]
        with mock.patch.object(process_scan, '_WindowsProcesses', return_value=inspector):
            self.assertIsNone(process_scan.scan_windows(Path('target').resolve()))
        inspector = self.inspector(image=str(Path('target/python.exe').resolve()))
        inspector.pids = lambda: [42]
        with mock.patch.object(process_scan, '_WindowsProcesses', return_value=inspector):
            self.assertIs(process_scan.scan_windows(Path('target').resolve()), True)


if __name__ == '__main__':
    unittest.main()
