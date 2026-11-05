import unittest
from types import SimpleNamespace
from unittest.mock import patch
from haivequeen import hives

class OwnerCheckSettingsTests(unittest.TestCase):
    def test_configured_text_is_question(self):
        env = SimpleNamespace(name='p', settings={'ownerCheckText': 'Review the result', 'language': 'English'})
        with patch.object(hives.runtime, 'list_agents', return_value=[]), patch.object(hives.db, 'add_question', return_value=7) as add, patch.object(hives, 'wait_for_owner_check'):
            hives.file_owner_check(None, env, 'plan.md')
        self.assertEqual(add.call_args.args[4], 'Review the result')
