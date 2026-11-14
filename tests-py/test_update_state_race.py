"""Update checks must preserve concurrent installation state changes."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from haivequeen import updates


class UpdateStateRaceTests(unittest.TestCase):
    def test_check_preserves_install_started_during_lookup(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
                updates, 'state_path', return_value=Path(folder) / 'state.json'):
            updates.save_state({'available': {'version': '2.0.2'}})

            def lookup(*args):
                updates.save_state({'available': {'version': '2.0.2'},
                                    'installing': {'version': '2.0.2', 'after_row': 3}})
                return {'version': '2.0.3'}

            updates.check({'update': {'repository': 'example/tool'}}, manual=True,
                          lookup=lookup, current='2.0.1')
            self.assertEqual(updates.load_state()['installing']['after_row'], 3)

    def test_older_check_cannot_replace_newer_result(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
                updates, 'state_path', return_value=Path(folder) / 'state.json'):
            machine = {'update': {'repository': 'example/tool'}}

            def lookup(*args):
                updates.check(machine, manual=True, now=datetime(2026, 1, 2),
                              lookup=lambda *args: {'version': '2.0.3'}, current='2.0.1')
                return {'version': '2.0.2'}

            updates.check(machine, manual=True, now=datetime(2026, 1, 1),
                          lookup=lookup, current='2.0.1')
            self.assertEqual(updates.offered()['version'], '2.0.3')
