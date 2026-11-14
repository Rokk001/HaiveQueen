"""Repair decisions require evidence rather than an agent merely ending."""
from bzztest import REL
from haivequeen import action_logs, orchestrator
from test_o1_orchestrator import OrchCase


class RepairEvidence(OrchCase):
    def setUp(self):
        super().setUp()
        self.check = 'echo original-failure && exit 7'
        self.project_toml()
        self.ready_plan()

    def test_no_change_does_not_repeat_the_failed_pipeline(self):
        self.round()
        first = self.fixers()[0]
        log = action_logs.latest('testproj', REL, 'Check')['path']
        self.end_fixer(first, 'No repair was made; inspect the shared fixture next.')
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], log)
        self.assertEqual(self.state()['phase'], 'fix')
        self.round()
        prompt = (self.env().run_dir(self.fixers()[-1]['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('No repair was made; inspect the shared fixture next.', prompt)
        self.assertIn(log, prompt)

    def test_empty_commit_is_not_repair_evidence(self):
        self.round()
        log = action_logs.latest('testproj', REL, 'Check')['path']
        self.git('commit', '--allow-empty', '-qm', 'empty attempt', cwd=self.pwt)
        self.end_fixer(self.fixers()[0])
        self.round()
        self.assertEqual(action_logs.latest('testproj', REL, 'Check')['path'], log)

    def test_run_records_tested_commit_and_command(self):
        self.round()
        records = action_logs.read(action_logs.latest('testproj', REL, 'Check')['path'])
        evidence = next(r for r in records if r['event'] == 'execution')
        self.assertEqual(evidence['commit'], self.git('rev-parse', 'HEAD', cwd=self.pwt))
        self.assertEqual(evidence['command'], self.check)
        self.assertEqual(next(r for r in records if r['event'] == 'end')['exit_code'], 7)

    def test_cancelled_worker_is_not_reported_as_project_failure(self):
        path = action_logs.start('testproj', REL, 'Build')
        job = orchestrator.Job('test-job')
        job.log_file = path
        job.cancelled = True
        orchestrator._work({}, job, 'Build', 'unused', self.pwt, None, self.base / 'result.json')
        end = action_logs.read(path)[-1]
        self.assertEqual(end['result'], 'cancelled')

    def test_second_failure_keeps_history_until_full_pipeline_passes(self):
        self.check = 'git ls-files --error-unmatch first-fix.txt'
        self.build = 'git ls-files --error-unmatch second-fix.txt'
        self.project_toml()
        self.round()
        first = self.fixers()[0]
        self.commit(self.pwt, 'first-fix.txt')
        self.end_fixer(first, 'First failure repaired and targeted check passed.')
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        self.assertFalse(any(x['event'] == 'verified' for x in history))
        self.assertEqual([x['step'] for x in history if x['event'] == 'failure'], ['Check', 'Build'])
        second = self.fixers()[-1]
        prompt = (self.env().run_dir(second['name']) / 'prompt.md').read_text(encoding='utf-8')
        self.assertIn('First failure repaired and targeted check passed.', prompt)
        self.commit(self.pwt, 'second-fix.txt')
        self.end_fixer(second, 'Second failure repaired.')
        self.round()
        history = orchestrator.repair_history(self.conn, 'testproj', REL)
        verified = [x for x in history if x['event'] == 'verified']
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]['commit'], self.remote_head())
        self.assertIn('Check', verified[0]['steps'])
        self.assertIn('Build', verified[0]['steps'])

