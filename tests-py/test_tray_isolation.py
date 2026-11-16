import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from haivequeen import tray

class TrayIsolationTests(unittest.TestCase):
    def test_same_runtime_uses_same_lock_but_test_and_production_differ(self):
        default = Path.cwd() / 'default-data'
        with patch.object(tray.osplat, 'data_dir', return_value=default):
            with patch.object(tray.config, 'data_dir', return_value=default):
                production = tray.mutex_name()
                self.assertEqual(production, 'Local\\HaiveQueenTray')
            with patch.object(tray.config, 'data_dir', return_value=default / 'test'):
                test = tray.mutex_name()
            with patch.object(tray.config, 'data_dir', return_value=default / 'test' / '..' / 'test'):
                self.assertEqual(tray.mutex_name(), test)
            self.assertNotEqual(production, test)

    def test_entrypoint_acquires_the_runtime_lock(self):
        import ctypes
        kernel = MagicMock()
        kernel.GetLastError.return_value = 183
        api = MagicMock(kernel32=kernel)
        with patch.object(ctypes, 'windll', api, create=True), patch.object(tray.os, 'name', 'nt'), \
                patch.object(tray, 'mutex_name', return_value='Local\\isolated-runtime'):
            tray.main()
        kernel.CreateMutexW.assert_called_once_with(None, False, 'Local\\isolated-runtime')
        kernel.CloseHandle.assert_called_once_with(kernel.CreateMutexW.return_value)

    @unittest.skipUnless(os.name == 'nt', 'Windows mutex API')
    def test_native_mutex_keeps_test_runtime_independent(self):
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        kernel.CreateMutexW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        root = Path.cwd() / ('mutex-test-%d' % os.getpid())
        handles = []
        try:
            with patch.object(tray.config, 'data_dir', return_value=root):
                first = kernel.CreateMutexW(None, False, tray.mutex_name())
                handles.append(first)
                self.assertTrue(first)
                duplicate = kernel.CreateMutexW(None, False, tray.mutex_name())
                handles.append(duplicate)
                self.assertEqual(ctypes.get_last_error(), 183)
            with patch.object(tray.config, 'data_dir', return_value=root / 'other'):
                other = kernel.CreateMutexW(None, False, tray.mutex_name())
                handles.append(other)
                self.assertTrue(other)
                self.assertNotEqual(ctypes.get_last_error(), 183)
        finally:
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)
