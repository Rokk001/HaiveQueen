"""Explicit plan prerequisites must finish integration before successors start."""
import unittest
from haivequeen import plans

class PlanDependencyTests(unittest.TestCase):
    def parse(self, name, deps=''):
        return plans.parse_plan_text('+++\nstatus="QUEUED"\n'+deps+'\n+++\n# Plan',name)

    def test_requires_integrated_predecessor(self):
        first=self.parse('first.md')
        second=self.parse('second.md','depends_on=["first.md"]')
        mapping={'first.md':first,'second.md':second}
        self.assertIn('first.md',plans.dependency_blocker(second,mapping,set()))
        self.assertEqual(plans.dependency_blocker(second,mapping,{'first.md'}),'')
        self.assertEqual(plans.dependency_blocker(first,mapping,set()),'')

    def test_missing_and_cycle_are_blocked(self):
        first=self.parse('first.md','depends_on=["second.md"]')
        second=self.parse('second.md','depends_on=["first.md"]')
        self.assertIn('missing',plans.dependency_blocker(first,{'first.md':first},set()))
        self.assertIn('cycle',plans.dependency_blocker(first,{'first.md':first,'second.md':second},set()))

    def test_invalid_dependencies_are_not_silently_discarded(self):
        self.assertTrue(self.parse('first.md','depends_on=42').errors)


from bzztest import RoundCase

class PersistedDependencies(RoundCase):
    def test_project_plan_waits_for_latest_successful_integration(self):
        from haivequeen import runtime, hives
        first = self.repo / 'first.md'
        second = self.repo / 'second.md'
        first.write_text('+++\nstatus="QUEUED"\n+++\n# First', encoding='utf-8')
        second.write_text('+++\nstatus="QUEUED"\ndepends_on=["first.md"]\n+++\n# Second', encoding='utf-8')
        for path in (first, second):
            self.conn.execute('INSERT INTO plans(project,path,title,status) VALUES (?,?,?,?)', ('testproj',path.name,path.stem,'QUEUED'))
        env = self.env()
        self.assertIn('first.md', hives.plan_dependency_blocker(self.conn,env,'second.md'))
        self.conn.execute("INSERT INTO queue(project,branch,plan_path,status) VALUES ('testproj','first','first.md','integrated')")
        self.assertEqual(hives.plan_dependency_blocker(self.conn,env,'second.md'),'')
        self.conn.execute("INSERT INTO queue(project,branch,plan_path,status) VALUES ('testproj','first-again','first.md','in_run')")
        self.assertIn('first.md', hives.plan_dependency_blocker(self.conn,env,'second.md'))
