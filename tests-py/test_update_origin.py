"""An offered release must not silently install from another repository."""
import unittest
from unittest import mock
from bzztest import RoundCase

from haivequeen import updates, selfupdate


class UpdateOriginTests(RoundCase):
    def test_matching_https_and_ssh_repository_is_accepted(self):
        with mock.patch.object(selfupdate.config, 'load_config', return_value={
                'update': {'repository': 'Example/Tool'}}):
            updates.validate_install_repository('2.0.2',
                offer={'version': '2.0.2', 'repository': 'example/tool'},
                run=lambda *args: (0, 'git@github.com:example/tool.git'))

    def test_checked_offer_records_its_source(self):
        state = updates.check({'update': {'repository': 'example/tool'}},
            manual=True, current='2.0.1', lookup=lambda *args: {'version': '2.0.2'})
        self.assertEqual(state['available']['repository'], 'example/tool')

    def test_mismatched_offer_never_starts_worker(self):
        with mock.patch.object(updates, 'offered', return_value={
                'version': '2.0.2', 'repository': 'example/other'}), \
                mock.patch.object(selfupdate.config, 'load_config', return_value={}), \
                mock.patch.object(selfupdate, 'run_command', return_value=(0, 'https://github.com/example/tool.git')), \
                mock.patch.object(selfupdate, 'spawn_worker') as spawn:
            with self.assertRaisesRegex(ValueError, 'repository'):
                updates.request_install('2.0.2')
            spawn.assert_not_called()

    def test_worker_rejects_config_mismatch_before_taking_lock(self):
        with mock.patch.object(selfupdate.config, 'load_config', return_value={
                'update': {'repository': 'example/other'}}), \
                mock.patch.object(updates, 'offered', return_value=None), \
                mock.patch.object(selfupdate.loop, '_take_pid_lock', return_value=123) as lock:
            with self.assertRaisesRegex(ValueError, 'repository'):
                selfupdate.run_update(None, version='2.0.2',
                                      run=lambda *args: (0, 'https://github.com/example/tool.git'))
            lock.assert_not_called()
