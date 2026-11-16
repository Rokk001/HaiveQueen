import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import config, tray, version_leases, web_service


class ServiceLeaseTests(unittest.TestCase):
    def test_posix_tray_command_uses_a_session_without_windows_flags(self):
        with patch.object(tray.osplat, 'is_windows', return_value=False), \
             patch.object(version_leases, 'launch') as launch:
            tray.run_command(['start'])
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertNotIn('creationflags', launch.call_args.kwargs)

    def test_tray_command_and_web_server_reserve_version_before_spawn(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'install'
            old = root / 'versions' / 'old'
            (old / 'haivequeen').mkdir(parents=True)
            (root / 'versions' / 'new').mkdir()
            (root / 'active-version').write_text('new', encoding='utf-8')
            class Process:
                pid = 123
                stdout = io.BytesIO(b'{"ok": true}\n')
                def wait(self, timeout=None): return 0
            def spawn(*args, **kwargs):
                leases = version_leases._load(root)
                self.assertTrue(any(r.get('version') == 'old' and r.get('pid') is None for r in leases.values()))
                self.assertTrue(old.is_dir())
                return Process()
            with patch.object(config, 'config_dir', return_value=base / 'data'), \
                 patch.object(config, 'data_dir', return_value=base / 'data'), \
                 patch.object(version_leases.platform, 'process_identity', return_value='live'), \
                 patch.object(tray, '__file__', str(old / 'haivequeen' / 'tray.py')), \
                 patch.object(web_service, '__file__', str(old / 'haivequeen' / 'web_service.py')), \
                 patch.object(tray.subprocess, 'Popen', side_effect=spawn), \
                 patch.object(web_service, 'control', return_value=None), \
                 patch('haivequeen.proctree.exempt_register'), \
                 patch.object(tray, 'ensure_started'):
                tray.run_command(['start'])
                self.assertTrue(web_service.start()['ok'])
                self.assertEqual(len(version_leases._load(root)), 2)


if __name__ == '__main__':
    unittest.main()
