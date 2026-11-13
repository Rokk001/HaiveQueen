import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation
from haivequeen import bees, gui_config, scheduler


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
        for invalid in (0, 101, 1.5, True, '90'):
            data = {'providers': [{**self.raw, 'capabilityThresholds': {'Coding': invalid}}]}
            self.assertTrue(gui_config.check(data), invalid)
        for valid in (1, 100):
            self.assertFalse(gui_config.check({'providers': [{**self.raw, 'capabilityThresholds': {'Coding': valid}}]}))
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


if __name__ == '__main__':
    unittest.main()
