import json
import re
import unittest
import bzztest
from haivequeen import web_ui, settings, dashboard_data

class FindingsUiTests(unittest.TestCase):
    def test_general_advanced_labels_and_help_are_scoped(self):
        data=json.loads(re.search(r'<script id="bootstrap" type="application/json">(.*?)</script>',web_ui.page('test','English')).group(1))
        self.assertEqual(data['fieldLabels']['General'].get('hives'), 'Plans')
        self.assertEqual(data['defaultHelp']['General'], settings.GENERAL_OTHER_HELP)

    def test_cpu_label_describes_shared_budget(self):
        field=next(row for row in settings.BUILD_FIELDS if row[0]=='cores')
        self.assertEqual(field[1], 'Total CPU budget for builds and checks')
