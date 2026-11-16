import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from haivequeen import db, runtime, hives, dashboard_data, package_diagnosis, i18n


class DiagnosisStatusTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.conn=db.connect(Path(self.tmp.name)/'test.db');self.addCleanup(self.conn.close)
        db.upsert_project(self.conn,'demo',None,'cyan',1)
        hives.create(self.conn,'demo','plan.md','codex','cyan')
        self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES ('demo','plan.md','switch','Switch')")
        runtime.upsert_agent(self.conn,'demo','diagnosis',{'plan':'plan.md','phase':'fix','status':'running','pid':123})
        self.save('running')
        runtime.kv_set(self.conn,'demo','hive-blocker:plan.md','remaining packages await diagnosis or its required answer')

    def save(self,phase):
        runtime.kv_set(self.conn,'demo',package_diagnosis._key('plan.md','switch'),json.dumps({'phase':phase,'agent':'diagnosis'}))

    def test_actual_hive_data_replaces_generic_blocker_with_package(self):
        with mock.patch('haivequeen.agent_process.is_live',return_value=True):
            hive=hives.hive_data(self.conn,'demo',[],'plan.md')[0]
        self.assertEqual(dashboard_data.status_text(hive),'Diagnosis running: switch')
        with mock.patch.object(i18n,'machine_language',return_value='German'):
            self.assertEqual(dashboard_data.status_text(hive),'Diagnose l\u00e4uft: switch')
        self.assertEqual(hives.get(self.conn,'demo','plan.md')['state'],'waiting')

    def test_pending_completed_and_dead_agents_never_claim_running(self):
        for phase in ('pending','completed','authorized','owner','running'):
            self.save(phase)
            with mock.patch('haivequeen.agent_process.pid_reused',return_value=False):
                self.assertEqual(package_diagnosis.running_packages(self.conn,'demo','plan.md',['switch'],alive=lambda pid:False),[])
        self.save('pending')
        with mock.patch('haivequeen.agent_process.is_live',return_value=True):
            self.assertEqual(package_diagnosis.running_packages(self.conn,'demo','plan.md',['switch']),[])

    def test_wrong_plan_agent_does_not_count(self):
        runtime.upsert_agent(self.conn,'demo','diagnosis',{'plan':'other.md'})
        with mock.patch('haivequeen.agent_process.is_live',return_value=True):
            self.assertEqual(package_diagnosis.running_packages(self.conn,'demo','plan.md',['switch']),[])

    def test_owner_question_keeps_priority_and_multiple_packages_are_named(self):
        hive={'state':'waiting','running_diagnoses':['core','switch'],'scheduler_state':'STOPPED'}
        self.assertEqual(dashboard_data.status_text(hive),'Diagnosis running: core, switch')
        hive.update(state='answer',pending_questions=[42])
        self.assertEqual(dashboard_data.status_text(hive),'Waiting for answer (#42)')
