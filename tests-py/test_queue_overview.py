import unittest
from bzztest import RoundCase
from haivequeen import dashboard_data

class QueueOverviewTests(RoundCase):
    def test_only_pending_plans_once_not_integration_queue(self):
        self.conn.execute('DELETE FROM plans')
        for name,status in [('draft','DRAFT'),('sent','SENT'),('queued','QUEUED'),('running','RUNNING'),('waiting','WAITING'),('done','DONE')]:
            self.conn.execute('INSERT INTO plans(project,path,title,status) VALUES (?,?,?,?)',('testproj',name+'.md',name,status))
        for branch in ['old','new']:
            self.conn.execute("INSERT INTO queue(project,branch,plan_path,status) VALUES ('testproj',?,'running.md','in_run')",(branch,))
        panel=dashboard_data.queue_panel(self.conn,None)
        self.assertEqual([r['plan_path'] for r in panel['items']],['queued.md','sent.md','draft.md'])
        self.assertEqual(panel['queued'],3)
        self.assertEqual(panel['drafts'],1)
        self.assertEqual(panel['pending'],2)
        self.assertEqual(dashboard_data.queue_panel(self.conn,'other')['items'],[])

    def test_started_hive_is_not_repeated_as_pending_plan(self):
        self.conn.execute("INSERT INTO plans(project,path,title,status) VALUES ('testproj','cut.md','Cut','SENT')")
        from haivequeen import hives
        hives.create(self.conn, 'testproj', 'cut.md', 'provider', '123456')
        self.assertNotIn('cut.md', [row['plan_path'] for row in dashboard_data.queue_panel(self.conn, None)['items']])
