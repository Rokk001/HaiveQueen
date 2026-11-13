import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_provider_page import PageCase
from haivequeen import gui_config


class ThresholdFields(PageCase):
    def test_fields_follow_capabilities_and_preserve_values_on_reopen(self):
        win = self.window()
        self.provider(win, 'claude')
        self.assertEqual(win.threshold_vars['Coding'].get(), '100')
        self.assertEqual(win.threshold_entries['Coding'].cget('state'), 'normal')
        self.assertEqual(win.threshold_entries['Build'].cget('state'), 'disabled')
        win.threshold_vars['Coding'].set('75')
        win.capability_vars['Coding'].set(False)
        self.assertEqual(win.threshold_entries['Coding'].cget('state'), 'disabled')
        self.assertTrue(win.collect())
        self.assertEqual(win.target()['capabilityThresholds']['Coding'], 75)
        win.save()
        win.win.destroy()
        reopened = self.window()
        self.provider(reopened, 'claude')
        self.assertEqual(reopened.threshold_vars['Coding'].get(), '75')
        self.assertEqual(reopened.threshold_entries['Coding'].cget('state'), 'disabled')
        reopened.capability_vars['Coding'].set(True)
        self.assertEqual(reopened.threshold_entries['Coding'].cget('state'), 'normal')

    def test_invalid_threshold_prevents_save(self):
        win = self.window()
        self.provider(win, 'claude')
        original = (self.folder / 'config.toml').read_bytes()
        for value in ('0', '101', '1.5', 'abc'):
            win.threshold_vars['Coding'].set(value)
            win.save()
            self.assertEqual((self.folder / 'config.toml').read_bytes(), original)
        win.threshold_vars['Coding'].set('1')
        win.save()
        self.assertEqual(gui_config.load_migrated(self.folder / 'config.toml')['providers'][0]['capabilityThresholds']['Coding'], 1)
