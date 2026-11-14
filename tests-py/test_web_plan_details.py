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

    def test_individual_steps_use_prompt_and_reported_completion(self):
        from haivequeen import web_plan_details
        prompt = "Steps:\n1. Inspect input\n2. 2. Implement handling\n3. Verify result\nPaths: module.py\n"
        result = web_plan_details.substeps(prompt, '2/3')
        self.assertEqual([s['text'] for s in result], ['Inspect input','Implement handling','Verify result'])
        self.assertEqual([s['status'] for s in result], ['done','current','pending'])
        self.assertEqual([s['status'] for s in web_plan_details.substeps(prompt, '2/3', completed=True)],
                         ['done','done','pending'])
        self.assertTrue(all(s['status']=='unknown' for s in web_plan_details.substeps(prompt, '2/4')))
        self.assertEqual(web_plan_details.substeps('No instructions', '2/3'), [])

    def test_replacement_prompt_does_not_inherit_previous_attempt_progress(self):
        from haivequeen import web_plan_details, runtime
        self.plan(packages=[{'id':'P1','title':'First task'}])
        self.agent('Old', phase='bee', package='P1')
        self.agent('New', phase='bee', package='P1', status='running')
        folder = self.env().run_dir('New')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'prompt.md').write_text('Steps:\n1. New first\n2. New second\nPaths: test\n', encoding='utf-8')
        runtime.append_event(self.conn, 'testproj', 'P1 step 2/2 done', kind='note', agent='Old', mirror='none')
        item = web_plan_details.packages(self.conn, 'testproj', REL, (self.repo / REL).read_text())[0]
        self.assertEqual([s['status'] for s in item['substeps']], ['unknown', 'unknown'])
        self.assertNotIn('step', item)
        runtime.append_event(self.conn, 'testproj', 'P1 step 1/2 starting', kind='note', agent='New', mirror='none')
        item = web_plan_details.packages(self.conn, 'testproj', REL, (self.repo / REL).read_text())[0]
        self.assertEqual([s['status'] for s in item['substeps']], ['current', 'pending'])
