from unittest.mock import patch
from bzztest import RoundCase
from haivequeen import updates, selfupdate


class AbandonedUpdateTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.state = {'installing': {'version': '0.2.45', 'after_row': 73}, 'available': {'version': '0.2.45'}}
        self.row = {'id': 74, 'ended': None, 'result': None}
        self.lock = self.base / 'update.lock'
        self.lock.write_text('15336', encoding='ascii')
        self.addCleanup(patch.stopall)
        patch.object(selfupdate, 'lock_path', return_value=self.lock).start()
        patch.object(updates, 'state_path', return_value=self.base / 'update-check.json').start()
        updates.save_state(self.state)

    def test_dead_worker_settles_without_restart_or_history_success(self):
        with patch.object(updates.osplat, 'process_alive', return_value=False):
            self.assertEqual(updates.settle_install(self.row), 'failed')
        self.assertNotIn('installing', updates.load_state())
        self.assertEqual(updates.load_state()['failed'], '0.2.45')
        self.assertIsNone(self.row['ended'])
        self.assertTrue(self.lock.exists())

    def test_live_worker_is_not_interrupted(self):
        with patch.object(updates.osplat, 'process_alive', return_value=True), patch.object(updates.osplat, 'process_command_line', return_value='python -m haivequeen self-update --worker'):
            self.assertIsNone(updates.settle_install(self.row))
        self.assertIn('installing', updates.load_state())

    def test_uninspectable_live_worker_is_preserved(self):
        with patch.object(updates.osplat, 'process_alive', return_value=True), patch.object(updates.osplat, 'process_command_line', return_value=None):
            self.assertIsNone(updates.settle_install(self.row))

    def test_no_new_history_row_does_not_misidentify_startup_as_failure(self):
        with patch.object(updates.osplat, 'process_alive', return_value=False):
            self.assertIsNone(updates.settle_install({'id': 73, 'ended': None}))

    def test_reused_pid_is_not_an_update_worker(self):
        with patch.object(updates.osplat, 'process_alive', return_value=True), patch.object(updates.osplat, 'process_command_line', return_value='unrelated program'):
            self.assertEqual(updates.settle_install(self.row), 'failed')
