from bzztest import RoundCase
from haivequeen import dashboard_data

class QueueListTests(RoundCase):
    def test_integration_entries_are_not_pending_dashboard_plans(self):
        for i, status in enumerate(('in_run', 'ready', 'integrated')):
            self.conn.execute("INSERT INTO queue(project,branch,tip,plan_path,status,note,updated_at) VALUES(?,?,?,?,?,?,?)",
                              ('testproj', 'branch-'+str(i), 'abc', 'plans/PLAN-'+str(i)+'.md', status, 'note', '2026-01-01'))
        result = dashboard_data.queue_panel(self.conn, 'testproj')
        self.assertEqual(result['queued'], 0)
        self.assertEqual(result['items'], [])
        self.assertEqual(dashboard_data.queue_panel(self.conn, 'another')['items'], [])
