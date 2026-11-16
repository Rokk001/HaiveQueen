from bzztest import REL, RoundCase
from haivequeen import dashboard_data, plan_view


class PlanTitleTests(RoundCase):
    def test_real_heading_is_exposed_without_changing_filename_identity(self):
        self.plan(status='DRAFT', title='Improve the customer onboarding flow')
        row = plan_view.plan_rows(self.conn)[0]
        self.assertEqual(row['title'], 'Improve the customer onboarding flow')
        self.assertEqual(row['name'], dashboard_data.hive_name(REL))
        self.assertEqual(row['plan_path'], REL)

    def test_missing_registered_plan_uses_existing_name_without_invented_title(self):
        self.plan(status='DRAFT', title='Previously visible heading')
        (self.repo / REL).unlink()
        row = plan_view.plan_rows(self.conn)[0]
        self.assertNotIn('title', row)
        self.assertEqual(row['name'], dashboard_data.hive_name(REL))
        self.assertEqual(row['plan_path'], REL)

    def test_discovered_plan_without_heading_has_no_synthetic_title(self):
        self.plan(status='DRAFT')
        (self.repo / REL).write_text('+++\nstatus="DRAFT"\n+++\nPlain requirement text.\n', encoding='utf-8')
        row = plan_view.plan_rows(self.conn)[0]
        self.assertNotIn('title', row)
        self.assertEqual(row['name'], dashboard_data.hive_name(REL))

    def test_database_only_plan_outside_discovery_has_no_title(self):
        self.plan(status='DRAFT', title='Excluded heading')
        settings = self.repo / '.haivequeen/project.toml'
        settings.write_text('name="testproj"\nplans=["other/*.md"]\n', encoding='utf-8')
        row = plan_view.plan_rows(self.conn)[0]
        self.assertNotIn('title', row)
        self.assertEqual(row['name'], dashboard_data.hive_name(REL))
