import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from haivequeen import orchestrator, dashboard_data, action_logs

class OrphanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = SimpleNamespace(name='project')
        self.state = dict(phase='pipeline', step='Check', steps={'Merge':'done', 'Check':'running'}, ran=['Merge'], job=dict(step='Check', compile=True, token='token', pid=4242, file=str(Path(self.tmp.name)/'missing.json')))

    def test_interrupted_check_is_not_a_project_failure(self):
        with mock.patch.object(orchestrator.integration, 'poll_compile_check', return_value={'exit':143, 'aborted':True, 'output':'supervisor vanished'}), mock.patch.object(orchestrator, 'save_step_log'):
            self.assertEqual(orchestrator._harvest(None, self.env, 'plan', self.state), 'lost')

    def test_interrupted_check_retries_only_current_stage_without_fixer(self):
        with mock.patch.object(orchestrator,'commands',return_value={'Check':'check'}), mock.patch.object(orchestrator.integration,'poll_compile_check',return_value={'exit':143,'aborted':True}), mock.patch.object(orchestrator,'save_step_log'), mock.patch.object(orchestrator,'save'), mock.patch.object(orchestrator,'_red') as repair:
            result=orchestrator._command(None,self.env,'plan',self.state,self.tmp.name,'Check',16000)
        self.assertEqual(result,'next')
        self.assertEqual(self.state['steps'],{'Merge':'done'})
        self.assertEqual(self.state['ran'],['Merge'])
        self.assertNotIn('job',self.state)
        repair.assert_not_called()

    def test_real_check_failure_remains_failure(self):
        with mock.patch.object(orchestrator.integration, 'poll_compile_check', return_value={'exit':1, 'output':'assertion failed'}), mock.patch.object(orchestrator, 'save_step_log'):
            self.assertEqual(orchestrator._harvest(None, self.env, 'plan', self.state), (1,'assertion failed'))

    def test_restart_during_check_or_partial_deploy_keeps_completed_steps(self):
        for step in ('Check','Deploy'):
            with self.subTest(step=step):
                state = dict(phase='pipeline', step=step, steps={'Merge':'done',step:'running'}, ran=['Merge'], deployed='', job=dict(step=step, token='dead', pid=4242, file=str(Path(self.tmp.name)/'missing.json')))
                with mock.patch.object(orchestrator.agent_process,'recorded_alive', return_value=False), mock.patch.object(orchestrator,'save'), mock.patch.object(orchestrator,'_finish_log'):
                    orchestrator._harvest_only(None,self.env,'plan',state)
                self.assertNotIn('job',state)
                self.assertNotIn(step,state['steps'])
                self.assertEqual(state['ran'],['Merge'])
                self.assertEqual(state['deployed'],'')
                self.assertEqual(state['step'],step)

    def test_receipt_after_restart_preserves_real_exit_code(self):
        for code in (0, 1):
            with self.subTest(code=code):
                log = Path(self.tmp.name) / 'deploy.jsonl'
                log.write_text('{"event":"output","text":"copied executable"}\n')
                log.with_suffix('.exit').write_text(str(code))
                state = dict(self.state, log_file=str(log), job=dict(self.state['job'], compile=False, step='Deploy'))
                with mock.patch.object(orchestrator.agent_process,'recorded_alive',return_value=False), mock.patch.object(orchestrator,'save_step_log'), mock.patch('haivequeen.log_snapshots.capture'):
                    self.assertEqual(orchestrator._harvest(None,self.env,'plan',state), (code,'copied executable'))

    def test_live_job_is_preserved(self):
        self.state['job'].pop('compile')
        with mock.patch.object(orchestrator.agent_process,'recorded_alive',return_value=True):
            self.assertIsNone(orchestrator._harvest(None,self.env,'plan',self.state))
        self.assertIn('job',self.state)

    def test_dead_job_is_not_displayed_as_running(self):
        hive={'project':'project','plan_path':'plan'}
        with mock.patch.object(orchestrator,'load',return_value=self.state), mock.patch.object(orchestrator.agent_process,'recorded_alive',return_value=False):
            info=dashboard_data.orch_info(None,None,hive)
        self.assertEqual(info['status'],'waiting')
        self.assertIn('recovery',info['task'].lower())
        self.assertNotIn(('Check','running'),info['chain'])

    def test_action_boundaries_are_flushed_to_disk(self):
        with mock.patch.dict(os.environ,{'BZZ_DATA_DIR':self.tmp.name}), mock.patch('os.fsync') as sync, mock.patch('haivequeen.log_snapshots.capture'):
            path=action_logs.start('project','plan','Deploy')
            self.assertEqual(sync.call_count,1)
            action_logs.append(path,'copy complete; validation pending')
            self.assertEqual(sync.call_count,1)
            action_logs.finish(path,143,'aborted')
            self.assertEqual(sync.call_count,2)

if __name__=='__main__': unittest.main()
