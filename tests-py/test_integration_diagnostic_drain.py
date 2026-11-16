import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
from unittest import TestCase, mock

from haivequeen import check_events, db, runtime, hives, orchestrator as orch, dashboard_data, buildjobs


class DiagnosticDrainTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / 'test.db')
        self.addCleanup(self.conn.close)
        db.upsert_project(self.conn, 'demo', None, 'cyan', 1)
        for plan in ('a.md', 'b.md'):
            hives.create(self.conn, 'demo', plan, 'codex', 'cyan')
            hives.take_integration(self.conn, 'demo', plan)
        self.env = SimpleNamespace(name='demo', settings={}, conn=self.conn)
        self.job = dict(step='Build', token='run-a', head='abc', cwd=str(self.root),
                        check_events=str(self.root / 'checks.jsonl'), buildJobs=4, buildParallel=1, pid=os.getpid())
        self.state = dict(phase='pipeline', step='Build', placed=True, steps={'Build': 'running'}, job=self.job)
        orch.save(self.conn, 'demo', 'a.md', self.state)
        self.head = mock.patch('haivequeen.integration.head_commit', return_value='abc')
        self.head.start()
        self.addCleanup(self.head.stop)
        self.clean = mock.patch('haivequeen.check_events._clean_checkout', return_value=True)
        self.clean.start()
        self.addCleanup(self.clean.stop)

    def event(self, event, **fields):
        with open(self.job['check_events'], 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(fields, event=event, run='run-a', commit='abc')) + '\n')

    def suite(self, **fields):
        self.event('suite', checks=['first', 'second', 'third'], artifacts={},
                   isolated_worktree=True, resources=['game:one', 'port:1234'], **fields)

    def result(self, name, code, final=True):
        self.event('result', check=name, exit=code, final=final, output='full check output')

    def observe(self):
        return orch._observe_checks(self.conn, self.env, 'a.md', self.state)

    def test_resumed_diagnosis_without_job_enters_interruption_recovery(self):
        self.state.update(phase='diagnosing', step='Build', check_report='retained-results')
        self.state.pop('job')
        def red(conn, env, plan, state, step, reason):
            state.update(phase='fix', fix={'step': step, 'error': reason})
            return 'red'
        with mock.patch.object(orch, '_red', side_effect=red) as fail, \
             mock.patch.dict(orch.JOBS, {}, clear=True):
            self.assertEqual(orch._advance_pipeline(self.conn, self.env, 'a.md', self.state, 16000), 'red')
        self.assertEqual(self.state['phase'], 'fix')
        self.assertTrue(self.state['fix']['review_required'])
        self.assertIn('retained-results', self.state['fix']['error'])
        fail.assert_called_once()

    def test_missing_record_never_releases_a_still_running_diagnostic_worker(self):
        self.state.update(phase='diagnosing', step='Build')
        self.state.pop('job')
        worker = mock.Mock()
        worker.alive.return_value = True
        with mock.patch.dict(orch.JOBS, {('demo', 'a.md'): worker}, clear=True), \
             mock.patch.object(orch, '_red') as fail:
            self.assertIsNone(orch._drain_checks(self.conn, self.env, 'a.md', self.state))
        fail.assert_not_called()
        self.assertEqual(self.state['phase'], 'diagnosing')

    def test_first_final_failure_is_red_while_worker_and_reservations_remain(self):
        self.suite()
        self.result('first', 1)
        self.assertTrue(self.observe())
        self.assertEqual(self.state['phase'], 'diagnosing')
        self.assertEqual(self.state['steps']['Build'], 'red')
        self.assertEqual(orch._ahead(self.conn, 'demo', 'b.md'), ['a.md'])
        self.assertIn('shared integration/deployment writes', self.state['diagnosis_release_reason'])
        self.assertIs(self.state['job'], self.job)
        info = dashboard_data.orch_info(self.conn, None, dict(project='demo', plan_path='a.md'))
        self.assertIn('1 checks red', info['task'])
        self.assertIn('1/3', info['task'])

    def test_retry_is_not_final_and_success_does_not_latch_failure(self):
        self.suite()
        self.result('first', 1, final=False)
        self.assertFalse(self.observe())
        self.result('first', 0)
        self.assertFalse(self.observe())
        self.assertEqual(len(self.job['checks']['attempts']), 2)
        self.result('second', 1, final=False)
        self.assertFalse(self.observe())
        self.result('second', 1)
        self.assertTrue(self.observe())

    def test_later_failures_append_and_restart_preserves_failure(self):
        self.suite()
        self.result('first', 1)
        self.observe()
        self.state = orch.load(self.conn, 'demo', 'a.md')
        self.result('second', 2)
        self.result('third', 0)
        self.observe()
        checks = self.state['job']['checks']
        self.assertEqual(set(checks['failures']), {'first', 'second'})
        self.assertEqual(len(checks['attempts']), 3)

    def test_release_unblocks_queue_but_not_game_or_unknown_resources(self):
        self.suite()
        self.result('first', 1)
        self.event('shared_writes_finished')
        self.observe()
        self.assertEqual(orch._ahead(self.conn, 'demo', 'b.md'), [])
        self.assertTrue(hives.take_integration(self.conn, 'demo', 'b.md'))
        self.assertIn('game:one', orch._diagnostic_resource_wait(self.conn, self.env, 'b.md', 'Build'))
        self.env.settings['integrationStepResources'] = {'Check': ['game:one'], 'Build': ['independent-output']}
        self.assertTrue(orch._diagnostic_resource_wait(self.conn, self.env, 'b.md', 'Check'))
        self.assertEqual(orch._diagnostic_resource_wait(self.conn, self.env, 'b.md', 'Build'), '')
        self.assertEqual(buildjobs.allocated_cores(buildjobs.running_builds(self.conn)), 4)
        self.assertIn('job', orch.load(self.conn, 'demo', 'a.md'))

    def test_exit_zero_after_failure_never_pushes_or_deploys(self):
        self.suite()
        self.result('first', 1)
        self.result('second', 0)
        self.result('third', 0)
        self.observe()
        with mock.patch.object(orch, '_harvest', return_value=(0, 'all output')), \
             mock.patch.object(orch, '_red', return_value='red') as red, \
             mock.patch.object(orch, '_push') as push, mock.patch.object(orch, '_command') as command:
            self.assertEqual(orch._advance_pipeline(self.conn, self.env, 'a.md', self.state, 16000), 'red')
        push.assert_not_called()
        command.assert_not_called()
        self.assertIn('first: exit 1', red.call_args.args[-1])
        report = json.loads(runtime.kv_get(self.conn, 'demo', self.state['check_report']))
        self.assertTrue(report['complete'])
        self.assertEqual(report['command_exit'], 0)
        self.assertEqual(report['commit'], 'abc')

    def test_crash_timeout_and_missing_results_cannot_turn_green(self):
        for code in (124, 125, 143):
            with self.subTest(code=code):
                job = dict(self.job)
                self.suite()
                report = check_events.finish(job, code, 'timeout or interrupted')
                self.assertFalse(report.get('ok', False))
                self.assertIn('second', report['failures'])
                self.assertEqual(report['command_exit'], code)
                Path(self.job['check_events']).unlink()
        self.suite()
        self.result('first', 1)
        self.observe()
        with mock.patch.object(orch, '_harvest', return_value='lost'), mock.patch.object(orch, '_red') as red:
            orch._drain_checks(self.conn, self.env, 'a.md', self.state)
        self.assertIn('second: no final result', red.call_args.args[-1])

    def test_changed_commit_or_artifact_prevents_release(self):
        artifact = self.root / 'game.bin'
        artifact.write_bytes(b'original')
        self.event('suite', checks=['first'], artifacts={'game.bin': hashlib.sha256(b'original').hexdigest()},
                   isolated_worktree=True, resources=[])
        self.result('first', 1)
        self.event('shared_writes_finished')
        check_events.consume(self.job)
        self.assertIn('commit changed', check_events.release_reason(self.job, self.root, 'other'))
        artifact.write_bytes(b'replaced')
        self.observe()
        self.assertFalse(self.state['diagnosis_released'])
        self.assertIn('artifact changed', self.state['diagnosis_release_reason'])

    def test_final_result_cannot_be_replaced_and_partial_record_waits(self):
        self.suite()
        self.result('first', 1)
        self.observe()
        self.result('first', 0)
        self.observe()
        self.assertIn('first', self.job['checks']['failures'])
        self.assertIn('protocol', self.job['checks']['failures'])
        with open(self.job['check_events'], 'ab') as stream:
            stream.write(b'{"event":')
        offset = self.job['checks']['offset']
        self.observe()
        self.assertEqual(self.job['checks']['offset'], offset)

    def test_ordinary_console_error_text_has_no_authority(self):
        self.assertFalse(self.observe())
        self.assertEqual(self.state['phase'], 'pipeline')

    def test_events_arriving_with_receipt_still_prevent_success(self):
        self.env.settings['buildCommand'] = 'unused existing worker'
        def harvest(*unused):
            self.suite()
            self.result('first', 1)
            return 0, 'runner incorrectly returned zero'
        with mock.patch.object(orch, '_harvest', side_effect=harvest), mock.patch.object(orch, '_red') as red:
            orch._command(self.conn, self.env, 'a.md', self.state, self.root, 'Build', 16000)
        self.assertIn('first: exit 1', red.call_args.args[-1])

    def test_deleted_and_truncated_journals_fail_closed(self):
        self.suite()
        self.result('first', 0)
        self.observe()
        Path(self.job['check_events']).unlink()
        self.assertTrue(self.observe())
        self.assertIn('protocol', self.job['checks']['failures'])

    def test_tracked_worktree_changes_are_not_a_frozen_commit(self):
        self.clean.stop()
        def git(*args):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        git('init', '-q')
        source = self.root / 'source.txt'
        source.write_text('original')
        git('add', 'source.txt')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
        self.assertTrue(check_events._clean_checkout(self.root))
        source.write_text('changed while checked')
        self.assertFalse(check_events._clean_checkout(self.root))

    def test_live_worker_drains_while_waiting_plan_reaches_push(self):
        # A real child waits on a pipe, not a timed sleep. Only this test owns it.
        receipt = self.root / 'receipt.json'
        script = '''import json, os, sys
from haivequeen.check_events import report
report('suite', checks=['first','second'], artifacts={}, isolated_worktree=True, resources=['game:one'])
report('result', check='first', exit=1, final=True, output='first error')
report('shared_writes_finished')
print('ready', flush=True)
sys.stdin.readline()
report('result', check='second', exit=2, final=True, output='second error')
with open(sys.argv[1], 'w') as f: json.dump({'token':'run-a','code':1,'output':'both errors'}, f)
'''
        env = {**os.environ, **check_events.environment(self.job['check_events'], 'run-a', 'abc'),
               'PYTHONPATH': str(Path(check_events.__file__).resolve().parent.parent)}
        proc = subprocess.Popen([sys.executable, '-c', script, str(receipt)], env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), 'ready')
            self.job.update(pid=proc.pid, file=str(receipt))
            self.observe()
            self.state = orch.load(self.conn, 'demo', 'a.md')  # restart boundary
            self.assertIsNone(orch._drain_checks(self.conn, self.env, 'a.md', self.state))
            second = dict(phase='pipeline', step='Push', placed=True, steps={}, ran=[])
            with mock.patch.object(orch, '_worktree', return_value=self.root), \
                 mock.patch.object(orch, '_push', return_value=None) as push:
                orch._advance_pipeline(self.conn, self.env, 'b.md', second, 16000)
            push.assert_called_once()
            self.assertIsNone(proc.poll())
            self.assertTrue(orch._diagnostic_resource_wait(self.conn, self.env, 'b.md', 'Deploy'))
            self.env.settings['buildJobs'] = 4
            self.assertEqual(buildjobs.plan_build(self.env, free_mb=16000, cores=8)['Jobs'], 0)
            proc.communicate('\n', timeout=10)
            with mock.patch.object(orch, '_red', return_value='red') as red:
                self.assertEqual(orch._drain_checks(self.conn, self.env, 'a.md', self.state), 'red')
            self.assertIn('first: exit 1', red.call_args.args[-1])
            self.assertIn('second: exit 2', red.call_args.args[-1])
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
