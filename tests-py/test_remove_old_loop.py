import unittest
from haivequeen import hives, loop, routine, scheduler, usage


class RemovedOldLoopTests(unittest.TestCase):
    def test_old_entry_points_and_templates_are_absent(self):
        for module, names in ((routine, ('get_routine',)), (loop, ('_reject_reason', '_make_pool')),
                              (usage, ('get_usage_table', 'scan_zst', 'start_zst_helper')),
                              (hives, ('state_label', 'blocker_label'))):
            for name in names:
                self.assertFalse(hasattr(module, name), name)
        for name in ('lockQuestion', 'fixBlocked', 'fixBlockedNoPlan', 'release', 'workInMainCheckout', 'integrator'):
            self.assertNotIn(name, scheduler.DEFAULT_PROMPTS)
        for key in ('bundle', 'progressVerifies', 'planStallHours', 'integratorSlots', 'minClass', 'openMarker'):
            self.assertNotIn(key, scheduler.build_settings({key: 'obsolete'}, {}))

    def test_configured_integrator_remains_available(self):
        settings = scheduler.build_settings({}, {'prompts': {'integrator': 'Configured {plan}'}})
        self.assertEqual(scheduler.expand_template(settings['prompts']['integrator'], {'plan': 'P'}), 'Configured P')
