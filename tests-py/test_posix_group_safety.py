"""Signals must never escape a registered process into a shared runner group."""
import signal
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import isolation  # noqa: F401
from haivequeen import platform


class GroupSafetyTests(unittest.TestCase):
    def test_shared_group_is_not_signalled_even_from_another_session(self):
        for group, caller in ((100, 100), (100, 900)):
            with self.subTest(group=group, caller=caller), \
                 patch.object(platform.os, 'getpgid', create=True, return_value=group), \
                 patch.object(platform.os, 'getpgrp', create=True, return_value=caller), \
                 patch.object(platform.os, 'killpg', create=True) as kill_group, \
                 patch.object(platform.os, 'kill') as kill_one, \
                 patch.object(platform, '_posix_process_alive', return_value=True), \
                 patch.object(platform.time, 'sleep'):
                self.assertFalse(platform._posix_kill_tree(500, grace=0))
                kill_group.assert_not_called()
                self.assertEqual([c.args for c in kill_one.call_args_list],
                                 [(500, signal.SIGTERM), (500, getattr(signal, 'SIGKILL', 9))])

    def test_own_group_is_protected_even_when_target_is_its_leader(self):
        with patch.object(platform.os, 'getpgid', create=True, return_value=500), \
             patch.object(platform.os, 'getpgrp', create=True, return_value=500), \
             patch.object(platform.os, 'killpg', create=True) as kill_group, \
             patch.object(platform.os, 'kill') as kill_one, \
             patch.object(platform, '_posix_process_alive', return_value=False):
            self.assertTrue(platform._posix_kill_tree(500))
        kill_group.assert_not_called()
        kill_one.assert_called_once_with(500, signal.SIGTERM)

    @unittest.skipIf(os.name == 'nt', 'requires real POSIX sessions')
    def test_detached_stopper_preserves_shared_parent_session(self):
        # The harness owns a new session, separate from unittest and CI.
        # Its victim shares that session; its stopper owns another session.
        source = r"""
import subprocess, sys
from haivequeen import platform
victim = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
try:
    stopper = subprocess.run([sys.executable, '-c',
        'import sys; from haivequeen import platform; platform._posix_kill_tree(int(sys.argv[1]), grace=.1)',
        str(victim.pid)], start_new_session=True, timeout=5)
    victim.wait(timeout=5)
    assert stopper.returncode == 0
    print('parent session survived', flush=True)
finally:
    if victim.poll() is None:
        victim.kill()
    victim.wait(timeout=5)
"""
        result = subprocess.run([sys.executable, '-c', source],
                                start_new_session=True, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('parent session survived', result.stdout)
