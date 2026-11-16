"""Descendant cleanup requires birth identity evidence, including saved runs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bzztest  # noqa: F401  (isolated runtime)
from haivequeen import proctree


class DescendantIdentityTests(unittest.TestCase):
    def setUp(self):
        for target, options in (
            ('is_exempt', {'return_value': False}),
            ('osplat.process_alive', {'return_value': True}),
            ('osplat.is_console_supervisor', {'return_value': False}),
        ):
            patch = mock.patch('haivequeen.proctree.' + target, **options)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(proctree, '_terminate')
        self.terminate = patch.start()
        self.addCleanup(patch.stop)

    def test_unverified_or_reused_pid_is_not_terminated(self):
        for recorded, current in ((None, 'new'), ('', 'new'), (None, None),
                                  ('birth', None), ('birth', ''), ('birth', 'new')):
            with self.subTest(recorded=recorded, current=current):
                self.terminate.reset_mock()
                with mock.patch.object(proctree.osplat, 'process_identity', return_value=current):
                    self.assertEqual(proctree.kill_recorded({101: {'identity': recorded, 'ppid': 100}}), [])
                self.terminate.assert_not_called()

    def test_tracker_failed_identity_lookup_cannot_authorize_later_kill(self):
        tracker = proctree.Tracker(100)
        with mock.patch.object(proctree, 'snapshot', return_value={101: 100}), \
             mock.patch.object(proctree, 'descendants', return_value={101: 100}), \
             mock.patch.object(proctree.osplat, 'process_identity', return_value=None):
            tracker.poll()
        with mock.patch.object(proctree.osplat, 'process_identity', return_value='replacement'):
            self.assertEqual(proctree.kill_recorded(tracker.procs, root=100), [])
        self.terminate.assert_not_called()

    def test_saved_run_without_child_identity_does_not_kill_reused_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'project' / 'agent'
            folder.mkdir(parents=True)
            (folder / proctree.STATE_FILE).write_text(json.dumps({
                'root': 100, 'rootIdentity': 'old-root',
                'procs': {'101': {'identity': '', 'ppid': 100}},
            }), encoding='utf-8')
            with mock.patch.object(proctree.osplat, 'process_identity', return_value='replacement'):
                self.assertEqual(proctree.sweep(directory), [])
        self.terminate.assert_not_called()

    def test_verified_children_still_end_before_parents(self):
        procs = {'101': {'identity': 'birth101', 'ppid': 100},
                 '102': {'identity': 'birth102', 'ppid': 101}}
        with mock.patch.object(proctree.osplat, 'process_identity', side_effect=lambda pid: 'birth%d' % pid):
            self.assertEqual(proctree.kill_recorded(procs), [102, 101])
        self.assertEqual(self.terminate.call_args_list, [mock.call(102), mock.call(101)])
