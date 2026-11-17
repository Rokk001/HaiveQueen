"""Complete test runs keep a record, never repeat with unchanged inputs and report environment failures as findings."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import bzztest  # noqa: F401  (puts this checkout first on the path)
from haivequeen import check_events, db, hives, runtime, orchestrator as orch


class FullRunRecordTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / 'test.db')
        self.addCleanup(self.conn.close)
        db.upsert_project(self.conn, 'demo', None, 'cyan', 1)
        hives.create(self.conn, 'demo', 'a.md', 'codex', 'cyan')
        hives.take_integration(self.conn, 'demo', 'a.md')
        self.env = SimpleNamespace(name='demo', settings={'compileCheck': 'run-all-tests'}, conn=self.conn)
        log = self.root / 'check.log'
        log.write_text(json.dumps({'event': 'execution', 'step': 'Check'}) + '\n', encoding='utf-8')
        self.state = dict(phase='pipeline', step='Check', placed=True, ran=[], steps={'Check': 'running'}, log_file=str(log))
        orch.save(self.conn, 'demo', 'a.md', self.state)
        for name, value in (('_replay_followers', None), ('_coordinate_repair', None), ('_log', None),
                            ('_finish_log', None)):
            patcher = mock.patch.object(orch, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, head='abc'):
        run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, head)
        if run:
            self.state['run'] = dict(run, run='run-1')
        return run, action

    def test_infrastructure_failures_are_separated_from_product_failures(self):
        self.assertTrue(check_events.infrastructure_only({'failures': {'protocol': 'bad record', 'command': 'exit 1'}}))
        self.assertTrue(check_events.infrastructure_only({'failures': {'install': 'exit 1'}, 'infrastructure': ['install']}))
        self.assertTrue(check_events.infrastructure_only(
            {'lost': True, 'failures': {'menu': 'no final result (command exit 143)', 'command': 'exit 143'}}))
        self.assertFalse(check_events.infrastructure_only({'failures': {'menu': 'exit 1', 'protocol': 'x'}}))
        self.assertFalse(check_events.infrastructure_only({'lost': True, 'failures': {'menu': 'exit 1'}}))
        self.assertFalse(check_events.infrastructure_only({'failures': {'command': 'exit 1'}}))

    def test_runner_cause_must_be_known(self):
        events = self.root / 'checks.jsonl'
        rows = [dict(event='suite', checks=['install'], run='r', commit='abc'),
                dict(event='result', check='install', exit=1, final=True, cause='infrastructure', run='r', commit='abc')]
        events.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        state = check_events.consume({'token': 'r', 'head': 'abc', 'check_events': str(events)})
        self.assertEqual(state['infrastructure'], ['install'])
        with events.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event='result', check='other', exit=1, final=True, cause='weather', run='r', commit='abc')) + '\n')
        self.assertIn('protocol', check_events.consume({'token': 'r', 'head': 'abc', 'check_events': str(events)})['failures'])

    def test_environment_failure_is_a_finding_without_fix_agent(self):
        self.start()
        self.state['run']['infrastructure'] = True
        self.assertIsNone(orch._red(self.conn, self.env, 'a.md', self.state, 'Check', 'Required check failures:\nprotocol: bad'))
        self.assertEqual(self.state['phase'], 'blocked')
        self.assertEqual(self.state['blocked']['kind'], 'environment')
        self.assertIsNone(self.state.get('fix'))
        question = runtime.list_questions(self.conn, 'demo', status='open', plan='a.md', item='integration-environment-check')
        self.assertEqual(len(question), 1)
        self.assertEqual(orch.last_run(self.conn, 'demo', 'a.md', 'Check')['result'], 'infrastructure')
        db.answer_question(self.conn, self.state['blocked']['question'], 'environment corrected', by='owner')
        orch._advance_blocked(self.conn, self.env, 'a.md', self.state)
        self.assertEqual((self.state['phase'], self.state['steps']['Check']), ('pipeline', 'todo'))
        run, action = self.start()
        self.assertIsNone(action)   # an environment finding gave no verdict: the run may happen again
        self.assertIn('no product verdict', run['reason'])

    def test_each_run_records_reason_scope_commit_duration_and_result(self):
        run, _ = self.start()
        self.assertEqual(run['reason'], 'first run')
        orch._red(self.conn, self.env, 'a.md', self.state, 'Check', 'assertion failed in menu')
        record = orch.last_run(self.conn, 'demo', 'a.md', 'Check')
        self.assertEqual((record['step'], record['command'], record['commit'], record['result']),
                         ('Check', 'run-all-tests', 'abc', 'product-failure'))
        self.assertIn('assertion failed in menu', record['failure'])
        self.assertIsInstance(record['duration_s'], float)
        self.assertTrue(record['started_at'] and record['finished_at'])
        self.assertEqual(self.state['phase'], 'fix')
        run, _ = self.start(head='def')
        self.assertEqual(run['reason'], 'changed commit')

    def test_failed_run_with_unchanged_inputs_is_not_repeated(self):
        self.start()
        orch._red(self.conn, self.env, 'a.md', self.state, 'Check', 'assertion failed in menu')
        run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, 'abc')
        self.assertIsNone(run)
        with mock.patch.object(orch, '_red', return_value='red') as red:
            self.assertEqual(action(self.conn, self.env, 'a.md', self.state, 'Check'), 'red')
        self.assertIn('Not repeated', red.call_args[0][5])
        self.assertIn('assertion failed in menu', red.call_args[0][5])
        self.env.settings = dict(self.env.settings, compileCheck='run-all-tests --fixed')
        run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, 'abc')
        self.assertIsNone(action)
        self.assertIn('command', run['reason'])

    def test_pass_is_reused_only_with_verified_artifacts(self):
        artifact = self.root / 'game.bin'
        artifact.write_bytes(b'one')
        import hashlib
        self.start()
        self.state['run']['artifacts'] = {'game.bin': hashlib.sha256(b'one').hexdigest()}
        with mock.patch('haivequeen.check_events._clean_checkout', return_value=True):
            orch._done(self.conn, self.env, 'a.md', self.state, 'Check')
            self.assertEqual(orch.last_run(self.conn, 'demo', 'a.md', 'Check')['result'], 'passed')
            run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, 'abc')
            self.assertIsNone(run)
            artifact.write_bytes(b'two')
            run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, 'abc')
        self.assertIsNone(action)
        self.assertIn('previous pass not reusable', run['reason'])

    def test_paused_plan_keeps_the_verdict_of_a_completed_run(self):
        self.start()
        self.state['job'] = {'step': 'Check', 'token': 'run-1'}
        with mock.patch.object(orch, '_harvest', return_value=(1, 'assertion failed')), \
             mock.patch.object(orch, '_observe_checks', return_value=False):
            orch._harvest_only(self.conn, self.env, 'a.md', self.state)
        self.assertEqual(orch.last_run(self.conn, 'demo', 'a.md', 'Check')['result'], 'product-failure')
        run, action = orch._plan_run(self.conn, self.env, 'a.md', 'Check', self.root, 'abc')
        self.assertIsNone(run)

    def test_run_records_are_readable_with_the_cli(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        from haivequeen import cli
        self.start()
        orch._red(self.conn, self.env, 'a.md', self.state, 'Check', 'assertion failed in menu')
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(cli.cmd_integration(argparse.Namespace(action='runs', project='demo', plan='a.md'), self.conn), 0)
        records = json.loads(stream.getvalue())
        self.assertEqual((records['Check']['result'], records['Build']), ('product-failure', None))
