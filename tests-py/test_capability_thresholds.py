import os
import sys
import unittest
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation
from haivequeen import bees, scheduler, web_settings


class CapabilityThresholdTest(unittest.TestCase):
    def setUp(self):
        self.raw = {'name': 'test', 'protocol': 'codex', 'slots': 2,
                    'capabilitiesVersion': 1, 'capabilities': ['Coding', 'Queen', 'Image generation'],
                    'classes': {'medium': {'model': 'test-model'}},
                    'capabilityThresholds': {'Coding': 90, 'Queen': 80, 'Image generation': 100}}
        self.provider = scheduler.get_providers({'providers': [self.raw]})[0]
        self.free = {'test': 2}

    def pick(self, needs, weekly=0, five=0, reset=1, provider=None):
        limits = {'test': {'usedPercent': weekly, 'resetHours': 1,
                           'windows': [{'usedPercent': five, 'resetHours': reset}]}}
        return scheduler.select_provider([provider or self.provider], 'medium', needs, self.free, limits=limits)

    def test_validation_defaults_and_retention(self):
        self.assertEqual(self.provider['capabilityThresholds']['Coding'], 90)
        legacy = scheduler.get_providers({'providers': [{'name': 'codex', 'protocol': 'codex'}]})[0]
        self.assertEqual(legacy['capabilityThresholds']['Coding'], 100)
        self.assertEqual(legacy['capabilityThresholds']['Queen'], 100)
        off = {**self.raw, 'capabilities': ['Queen']}
        self.assertEqual(scheduler.get_providers({'providers': [off]})[0]['capabilityThresholds']['Coding'], 90)

    def test_both_windows_capabilities_and_reset(self):
        self.assertIsNone(self.pick([], weekly=90))
        self.assertIsNone(self.pick([], five=90))
        self.assertIsNotNone(self.pick([], weekly=89, five=89))
        self.assertIsNotNone(self.pick([], five=90, reset=-1))
        self.assertIsNotNone(self.pick(['Image generation'], weekly=90))
        self.assertIsNone(self.pick(['Image generation', 'Coding'], weekly=90))
        self.assertIsNone(self.pick(['Queen'], weekly=80))
        self.assertIsNotNone(self.pick([], weekly=99, provider={**self.provider, 'limit': 'none'}))
        self.assertIsNotNone(scheduler.select_provider([self.provider], 'medium', [], self.free, limits={}))

    def test_preference_fallback_and_direct_route_obey_threshold(self):
        with mock.patch.object(scheduler, 'provider_limit', return_value={'FreePct': 10, 'Hours': 1}), \
             mock.patch.object(scheduler, '_five_hour_window', return_value=None):
            self.assertIsNone(scheduler.select_provider([self.provider], 'medium', [], self.free, prefer='test'))
            self.assertIsNone(scheduler.select_provider([{**self.provider, 'use': 'fallback'}], 'medium', [], self.free))
            self.assertIsNone(bees.select_route([self.provider], self.free, 'test', 'medium', [], [], False))
            self.assertIsNone(bees.select_route([self.provider], self.free, 'test', 'medium', [], [], True))
            self.assertTrue(scheduler.capability_available([self.provider], ['Coding']))

    def test_changed_threshold_applies_to_next_choice(self):
        self.assertIsNone(self.pick([], weekly=90))
        changed = scheduler.get_providers({'providers': [{**self.raw, 'capabilityThresholds': {'Coding': 100}}]})[0]
        self.assertIsNotNone(self.pick([], weekly=90, provider=changed))


    def test_invalid_save_preserves_file_and_valid_values_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            initial = web_settings.load(path)
            saved = web_settings.save(path, {'providers': [self.raw]}, initial['revision'])
            original = path.read_bytes()
            for invalid in (0, 101, 1.5, True, '90'):
                data = web_settings.load(path)['settings']
                data['providers'][0]['capabilityThresholds']['Coding'] = invalid
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    web_settings.save(path, data, saved['revision'])
                self.assertEqual(path.read_bytes(), original)
            for valid in (1, 100, 75):
                loaded = web_settings.load(path)
                loaded['settings']['providers'][0]['capabilityThresholds']['Coding'] = valid
                web_settings.save(path, loaded['settings'], loaded['revision'])
                result = web_settings.load(path)['settings']
                self.assertEqual(result['providers'][0]['capabilityThresholds'],
                                 {'Coding': valid, 'Queen': 80, 'Image generation': 100})
                self.assertEqual(scheduler.get_providers(result)[0]['capabilityThresholds']['Coding'], valid)



from test_live_config import LiveConfigTest
from bzztest import REL, runtime
from haivequeen import hives
from haivequeen import scheduler as sch


class ThresholdReloadTest(LiveConfigTest):
    def test_invalid_reload_then_valid_reload_keeps_running_work(self):
        self.agent('busy', status='running', pid=1234, model='running-model', started=1)
        before = runtime.get_agent(self.conn, 'testproj', 'busy')
        def write_threshold(value):
            self.write_machine(slots=2)
            with self.machine_file.open('a', encoding='utf-8') as f:
                f.write('\n[providers.capabilityThresholds]\nCoding = %s\n' % value)
        def change(poll):
            if poll == 1:
                write_threshold(0)
            elif poll == 2:
                self.assertEqual(len(self.rounds_seen), 1)
                write_threshold(75)
        self.drive(change)
        self.assertEqual(len(self.rounds_seen), 2)
        provider = sch.get_providers(self.rounds_seen[1][0])[0]
        self.assertEqual(provider['capabilityThresholds']['Coding'], 75)
        self.assertIsNone(sch.select_provider([provider], 'medium', [], {'claude': 1},
                          limits={'claude': {'usedPercent': 75, 'resetHours': 1}}))
        after = runtime.get_agent(self.conn, 'testproj', 'busy')
        for field in ('pid', 'model', 'status', 'name'):
            self.assertEqual(after[field], before[field])


from bzztest import RoundCase


class ThresholdQuestionTest(RoundCase):
    def test_threshold_waits_without_owner_question_but_missing_capability_asks(self):
        self.machine['providers'] = self.machine['providers'][:1]
        self.machine['providers'][0]['capabilityThresholds'] = {'Coding': 50}
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'Coding', 'class': 'medium'},
                                  {'id': 'P2', 'title': 'Image', 'class': 'medium', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        with mock.patch.object(scheduler, 'provider_limit', return_value={'FreePct': 50, 'Hours': 1}):
            self.assertFalse(bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'task', [], [], []))
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', plan=REL, item='P1'), [])
        for _ in range(2):
            self.assertFalse(bees.start_process(env, REL, 'P2', 'Ada', 'medium', 'task', [], [], []))
        self.assertEqual(len(runtime.list_questions(self.conn, 'testproj', plan=REL, item='P2')), 1)


if __name__ == '__main__':
    unittest.main()
