from unittest.mock import patch
from bzztest import RoundCase
from haivequeen import updates, dashboard_data

class StatusDisplayTests(RoundCase):
    def test_obsolete_answer_blocker_is_not_displayed(self):
        text = dashboard_data.status_text({'state': 'answer', 'pending_questions': [], 'blocker': 'Plan state "answer" (questions 756)'})
        self.assertEqual(text, 'Waiting to start')

    def test_superseded_failure_is_not_current_status(self):
        state = {'failed': '0.2.45', 'available': {'version': '0.2.48'}}
        with patch.object(updates, 'load_state', return_value=state), patch.object(updates, 'installed_version', return_value='0.2.51'):
            self.assertNotIn('failed', updates.display_state())
            self.assertIsNone(updates.display_state()['available'])
        self.assertEqual(state['failed'], '0.2.45')

    def test_current_failure_remains_visible(self):
        with patch.object(updates, 'load_state', return_value={'failed': '0.2.52'}), patch.object(updates, 'installed_version', return_value='0.2.51'):
            self.assertEqual(updates.display_state()['failed'], '0.2.52')
