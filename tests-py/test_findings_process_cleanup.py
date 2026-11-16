"""Cleanup must exclude the owning runner exemption, not child runners."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import bzztest
from haivequeen import proctree

class RunnerExemptionTests(unittest.TestCase):
    def test_owner_exemption_does_not_protect_own_leftovers(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / '100').write_text('id100')
            (folder / '200').write_text('id200')
            procs = {101: {'ppid': 100, 'identity': 'id101'},
                     200: {'ppid': 100, 'identity': 'id200'},
                     201: {'ppid': 200, 'identity': 'id201'}}
            with mock.patch.object(proctree, 'exempt_dir', return_value=folder), \
                 mock.patch.object(proctree.osplat, 'process_identity', side_effect=lambda pid: 'id%d' % pid), \
                 mock.patch.object(proctree.osplat, 'process_alive', return_value=True), \
                 mock.patch.object(proctree.osplat, 'is_console_supervisor', return_value=False), \
                 mock.patch.object(proctree, '_terminate') as terminate:
                tracker = proctree.Tracker(100)
                tracker.procs = procs
                with mock.patch.object(tracker, 'poll'), mock.patch.object(proctree.time, 'sleep'):
                    tracker.finish()
                self.assertIn(mock.call(101), terminate.call_args_list)
                self.assertNotIn(mock.call(200), terminate.call_args_list)
                self.assertNotIn(mock.call(201), terminate.call_args_list)
