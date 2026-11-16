"""One browser pattern field preserves legacy discovery and migration."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation  # noqa: F401
from haivequeen import config, plans, projsettings, settings


class PlanPatternSettingsTest(unittest.TestCase):
    def test_one_field_round_trip_preserves_discovery(self):
        cases = [
            {},
            {'planGlobs': [], 'planExclude': []},
            {'planGlobs': ['custom/*.md'], 'planExclude': ['custom/skip.md']},
            {'plans': [], 'planGlobs': ['custom/*.md'], 'planExclude': ['custom/skip.md']},
            {'plans': '', 'planExclude': ['docs/plans/skip.md']},
            {'plans': 'custom/*.md\n!custom/skip.md', 'planGlobs': ['docs/plans/*.md']},
            {'plans': ['!docs/plans/skip.md']},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / '.haivequeen' / 'project.toml'
            path.parent.mkdir()
            for name in ('custom/keep.md', 'custom/skip.md', 'docs/plans/keep.md', 'docs/plans/skip.md'):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('+++\nstatus="DRAFT"\n+++\n# Example', encoding='utf-8')
            for source in cases:
                with self.subTest(source=source):
                    original = copy.deepcopy(source)
                    settings.save_file(path, source)
                    before = config.load_project(root)
                    shown = projsettings.plan_field_settings(source)
                    self.assertEqual(source, original)
                    self.assertNotIn('planGlobs', shown)
                    self.assertNotIn('planExclude', shown)
                    self.assertIsInstance(shown['plans'], list)
                    settings.save_file(path, json.loads(json.dumps(shown)))
                    after = config.load_project(root)
                    self.assertEqual(plans.discover_plans(root, before['planGlobs'], before['planExclude']),
                                     plans.discover_plans(root, after['planGlobs'], after['planExclude']))

    def test_migration_preserves_empty_modern_field_legacy_fallback(self):
        for empty in ([], ''):
            source = {'name': 'Example', 'plans': empty, 'planGlobs': ['custom/*.md'],
                      'planExclude': ['custom/skip.md']}
            with self.subTest(empty=empty), mock.patch.object(projsettings, 'derive_remote', return_value=('origin', False)):
                migrated, removed = projsettings.migrate_data(source, '.', {})
            self.assertEqual(migrated['plans'], ['custom/*.md', '!custom/skip.md'])
            self.assertEqual(projsettings.plans_to_globs(migrated), projsettings.plans_to_globs(source))
            self.assertTrue({'planGlobs', 'planExclude'} <= {entry[0] for entry in removed})


if __name__ == '__main__':
    unittest.main()
