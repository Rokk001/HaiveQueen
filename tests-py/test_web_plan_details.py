from bzztest import REL, RoundCase
from haivequeen import bees, web_api


class PlanDetailsTests(RoundCase):
    def test_packages_explain_progress_and_body_excludes_header(self):
        self.plan(packages=[{'id':'P1','title':'First task'}, {'id':'P2','title':'Second task','depends':['P1']}])
        bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'done', 'Worker')
        bees.set_package_state(self.conn, 'testproj', REL, 'P2', 'waiting', reason='question:12')
        result = web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':REL})
        self.assertEqual([p['id'] for p in result['packages']], ['P1','P2'])
        self.assertEqual(result['packages'][0]['status'], 'done')
        self.assertEqual(result['packages'][0]['title'], 'First task')
        self.assertEqual(result['packages'][1]['reason'], 'question:12')
        self.assertEqual(result['packages'][1]['depends_on'], ['P1'])
        self.assertNotIn('+++', result['body'])
        self.assertIn('+++', result['text'])
