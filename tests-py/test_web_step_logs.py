import json
from pathlib import Path

from bzztest import REL, RoundCase
from haivequeen import action_logs, orchestrator, runtime, web_api


class StepLogsTests(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan()

    def request(self, step='Build'):
        return web_api.execute(self.conn, 'plan/step-log', {'project':'testproj','plan':REL,'step':step})

    def state(self, **extra):
        value = {'phase':'pipeline','step':'Build','steps':{'Build':'running'}}
        value.update(extra)
        orchestrator.save(self.conn, 'testproj', REL, value)

    def test_live_journal_appends_and_terminal_result(self):
        path = action_logs.start('testproj', REL, 'Build')
        self.state(log_file=path, job={'step':'Build'})
        action_logs.append(path, 'first\n')
        self.assertEqual(self.request()['output'], 'first\n')
        self.assertEqual(self.request()['status'], 'running')
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write('{"event":"output","text":"second')
        self.assertEqual(self.request()['output'], 'first\n')
        with Path(path).open('a', encoding='utf-8') as stream:
            stream.write('"}\n')
        action_logs.finish(path, 1)
        result = self.request()
        self.assertEqual(result['output'], 'first\nsecond')
        self.assertEqual(result['status'], 'red')
        self.assertEqual(result['exit_code'], 1)
        self.assertFalse(result['historical'])

    def test_every_step_and_project_is_isolated(self):
        for step in orchestrator.STEPS:
            path = action_logs.start('testproj', REL, step)
            action_logs.append(path, step)
            action_logs.finish(path, 0)
            other = action_logs.start('other', REL, step)
            action_logs.append(other, 'wrong project')
            other = action_logs.start('testproj', 'other.md', step)
            action_logs.append(other, 'wrong plan')
            self.assertEqual(self.request(step)['output'], step)
            self.assertEqual(self.request(step)['status'], 'done')

    def test_waiting_build_has_reason_and_previous_output_is_marked(self):
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'previous build')
        action_logs.finish(path, 0)
        self.state()
        runtime.kv_set(self.conn, 'testproj', 'hive-blocker:'+REL, 'waiting for the build: waiting for build cores')
        result = self.request()
        self.assertEqual(result['status'], 'waiting')
        self.assertIn('build cores', result['reason'])
        self.assertTrue(result['historical'])
        self.assertEqual(result['output'], 'previous build')

    def test_new_run_does_not_show_old_output_or_stale_blocker(self):
        old = action_logs.start('testproj', REL, 'Build')
        action_logs.append(old, 'old')
        action_logs.finish(old, 0)
        new = action_logs.start('testproj', REL, 'Build')
        self.state(log_file=new, job={'step':'Build'})
        runtime.kv_set(self.conn, 'testproj', 'hive-blocker:'+REL, 'waiting for build cores')
        result = self.request()
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['output'], '')
        self.assertFalse(result['historical'])

    def test_old_compile_check_fallback(self):
        runtime.kv_set(self.conn, 'testproj', 'orch-check:'+REL, json.dumps({'exit':0,'output':'existing check'}))
        self.assertEqual(self.request('Check')['output'], 'existing check')

    def test_cancelled_journal(self):
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.finish(path, 143, 'cancelled', 'command stopped')
        self.assertEqual(self.request()['status'], 'cancelled')
        self.assertEqual(self.request()['reason'], 'command stopped')

    def test_dashboard_chain_reports_waiting_instead_of_running(self):
        from haivequeen import dashboard_data
        self.state()
        hive = {'project':'testproj', 'plan_path':REL, 'blocker':'waiting for build cores'}
        info = dashboard_data.orch_info(self.conn, None, hive)
        self.assertEqual(dict(info['chain'])['Build'], 'waiting')
        self.assertEqual(info['status'], 'waiting')
        self.state(job={'step':'Build'})
        info = dashboard_data.orch_info(self.conn, None, hive)
        self.assertEqual(dict(info['chain'])['Build'], 'running')

    def test_referenced_old_log_is_loaded_and_refreshed(self):
        self.plan(branch='test', worktree=str(self.wt))
        folder = self.wt / 'out'
        folder.mkdir(exist_ok=True)
        log = folder / 'checks-old.txt'
        log.write_text('first failure\n', encoding='utf-8')
        runtime.kv_set(self.conn, 'testproj', 'orch-log:'+REL+':Build',
                       json.dumps({'exit_code':1, 'output':r'checks failed (exit 1), see out\checks-old.txt'}))
        result = self.request()
        self.assertIn('first failure', result['output'])
        self.assertEqual(result['exit_code'], 1)
        log.write_text('first failure\nsecond failure\n', encoding='utf-8')
        self.assertIn('second failure', self.request()['output'])

    def test_journal_reference_is_deduplicated_and_missing_file_explained(self):
        self.plan(branch='test', worktree=str(self.wt))
        folder = self.wt / 'out'
        folder.mkdir(exist_ok=True)
        (folder / 'build-old.log').write_text('compiler error\n', encoding='utf-16')
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'log out/build-old.log\nlog out/build-old.log\nsee out/missing.txt')
        action_logs.finish(path, 1)
        output = self.request()['output']
        self.assertEqual(output.count('compiler error'), 1)
        self.assertIn('Referenced log unavailable: out/missing.txt', output)

    def test_reference_cannot_escape_plan_worktree(self):
        self.plan(branch='test', worktree=str(self.wt))
        (self.wt.parent / 'private.txt').write_text('PRIVATE DATA', encoding='utf-8')
        path = action_logs.start('testproj', REL, 'Build')
        action_logs.append(path, 'see out/../../private.txt')
        output = self.request()['output']
        self.assertNotIn('PRIVATE DATA', output)
