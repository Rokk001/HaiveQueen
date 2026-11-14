from bzztest import RoundCase
from haivequeen import dashboard_data

class QueueListTests(RoundCase):
    def test_all_open_entries_include_plan_branch_and_status(self):
        for i in range(6):
            self.conn.execute("INSERT INTO queue(project,branch,tip,plan_path,status,note,updated_at) VALUES(?,?,?,?,?,?,?)",
                              ('testproj','branch-'+str(i),'abc','plans/PLAN-'+str(i)+'.md','in_run' if i==0 else 'ready','note','2026-01-01'))
        self.conn.execute("INSERT INTO queue(project,branch,tip,plan_path,status,note,updated_at) VALUES(?,?,?,?,?,?,?)",
                          ('testproj','finished','abc','','integrated','','2026-01-01'))
        result=dashboard_data.queue_panel(self.conn,'testproj')
        self.assertEqual(result['queued'],6)
        self.assertEqual(len(result['items']),6)
        self.assertEqual(result['items'][0]['status'],'in_run')
        self.assertEqual(result['items'][-1]['branch'],'branch-5')
        self.assertEqual(result['items'][-1]['plan_path'],'plans/PLAN-5.md')
        self.assertEqual(dashboard_data.queue_panel(self.conn,'another')['items'],[])
