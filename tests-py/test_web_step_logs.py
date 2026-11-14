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
