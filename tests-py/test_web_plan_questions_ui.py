"""Focused executable UI regression for plan authoring and central Questions."""
import shutil
import subprocess
import unittest
from pathlib import Path

from bzztest import ROOT
from haivequeen import i18n


class PlanQuestionsUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for UI regression')
    def test_plan_questions_controls(self):
        script = Path(__file__).with_name('web_plan_questions_test.cjs')
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for UI regression')
    def test_questions_messenger(self):
        script = Path(__file__).with_name('web_questions_messenger_test.cjs')
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node required for UI regression')
    def test_plans_filters(self):
        script = Path(__file__).with_name('web_plans_filters_test.cjs')
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_localized_new_plan_template_is_valid_draft(self):
        from haivequeen import plans, web_drafts
        template = next(key for key in i18n.GERMAN if key.startswith('# Plan title\n'))
        for language in ('English', 'German'):
            text = i18n.tr(template, language=language)
            draft = plans.parse_plan_text(web_drafts.draft_text(text))
            self.assertTrue(draft.title)
            self.assertEqual(draft.status, 'DRAFT')
            self.assertEqual(draft.errors, [])

    def test_assets_are_utf8(self):
        for name in ('web-client.js', 'web-client.css', 'i18n.py'):
            (ROOT / 'haivequeen' / name).read_text(encoding='utf-8')

    def test_new_workflow_labels_are_translated(self):
        for label in ('New plan', 'Save draft', 'Submit plan', 'Acceptance',
                      'Answered history', 'Release readiness gate', 'Saved note',
                      'Search plans', 'All projects', 'All statuses', 'Clear filters',
                      'View result', 'View plan', 'Drafts', 'Ready for review'):
            self.assertNotEqual(i18n.tr(label, language='German'), label)


if __name__ == '__main__':
    unittest.main()
