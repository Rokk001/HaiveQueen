"""Durable native handoff and one-use repair authorization."""
import contextlib
import io
import json
import os
from pathlib import Path
from unittest import mock
import test_repair_contract_flow as fixtures
from haivequeen import bees, cli, hives, runtime, package_diagnosis as diagnosis
from bzztest import REL, RoundCase

class Handoff(RoundCase):
    report = fixtures.RepairContractFlow.report
    accept = fixtures.RepairContractFlow.accept

    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()
        (self.base / 'assigned-agent-worktree' / 'workflow.py').write_text('# isolated workflow fixture\n')
        (self.repo / 'workflow.py').write_text('# isolated workflow fixture\n')
        self.plan('IN_PROGRESS', [{'id': 'P1', 'class': 'medium'}, {'id': 'P2', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.env().settings['escalateAfter'] = 1
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed',
                            'old-attempt', ['R1'], 'Admission remains unproven', force_failure=True)

    def reserve(self, name='worker'):
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'):
            return bees.start_process(self.env(), REL, 'P1', name, 'medium', 'repair', ['repair'], [], [])

    def test_lost_response_cancel_and_retry_preserve_grant_until_launch(self):
        scope, report = self.report(); self.accept(scope, report)
        first = self.reserve()
        self.assertIsNotNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertEqual(Path(first['prompt_file']).read_text(encoding='utf-8'), first['prompt'])
        queen = runtime.get_agent(self.conn, 'testproj', 'queen')
        self.conn.commit()
        recovered = bees.handoff(self.env(), REL, 'P1', queen)
        self.assertEqual(first, {k:v for k,v in recovered.items() if k != 'launched'})
        self.assertFalse(recovered['launched'])
        self.assertIsNotNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertFalse(self.reserve('duplicate'))
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'interrupted', first['name'])
        second = self.reserve('replacement')
        self.assertEqual(second['kind'], 'subagent')
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'):
            bees.handoff(self.env(), REL, 'P1', queen, launched=True)
            bees.handoff(self.env(), REL, 'P1', queen, launched=True)
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))
        self.assertTrue(bees.handoff(self.env(), REL, 'P1', queen)['launched'])
        self.assertEqual(diagnosis._load(self.conn, 'testproj', REL, 'P1')['assigned']['attempt'], second['name'])
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P1', 'medium'), 1)
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'interrupted', second['name'])
        self.assertFalse(self.reserve('after-start'))

    def test_changed_scope_and_foreign_queen_cannot_acknowledge(self):
        scope, report = self.report(); self.accept(scope, report); self.reserve()
        with self.assertRaises(ValueError):
            bees.handoff(self.env(), REL, 'P1', {'name':'foreign'})
        runtime.add_decision(self.conn, 'testproj', 'owner', 'Changed requirement', plan=REL)
        with self.assertRaises(ValueError):
            bees.handoff(self.env(), REL, 'P1', runtime.get_agent(self.conn, 'testproj', 'queen'), launched=True)

    def test_result_without_ack_consumes_grant(self):
        scope, report = self.report(); self.accept(scope, report); first=self.reserve()
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'failed', first['name'], ['R1'], 'still fails')
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))

    def test_cli_returns_compact_retrievable_payload_and_acknowledges_start(self):
        scope, report = self.report(); self.accept(scope, report)
        def call(action):
            args = cli.build_parser().parse_args(['agent', action, '--project', 'testproj', '--plan', REL,
                '--package', 'P1', '--class', 'medium'] + (['--name', 'worker', '--task', 'repair', '--steps', '["repair"]', '--subagents'] if action == 'start' else []))
            output = io.StringIO()
            with mock.patch.dict(os.environ, {'BZZ_AGENT': 'queen'}), mock.patch('haivequeen.scheduler.load_projects', return_value=[self.env()]), mock.patch('haivequeen.package_diagnosis.current_commit', return_value='head-1'), contextlib.redirect_stdout(output):
                self.assertEqual(cli.cmd_bee(args, self.conn), 0)
            return json.loads(output.getvalue())
        first = call('start')
        self.assertNotIn('prompt', first)
        self.assertIn('DIAGNOSED REPAIR APPROACH', Path(first['prompt_file']).read_text(encoding='utf-8'))
        self.assertEqual(call('handoff')['name'], first['name'])
        self.assertTrue(call('launched')['launched'])
        self.assertIsNone(diagnosis.authorization(self.conn, 'testproj', REL, 'P1', scope))


class RecoveredHandoffFlow(Handoff):
    receipt = fixtures.RepairContractFlow.receipt
    result = fixtures.RepairContractFlow.result

    def start(self, name, head='head-1'):
        from haivequeen import db
        with mock.patch('haivequeen.package_diagnosis.current_commit', return_value=head):
            first = bees.start_process(self.env(), REL, 'P1', name + '-unstarted', 'medium', 'repair', ['repair'], [], [])
            self.assertTrue(first)
            self.conn.commit()
            database = self.conn.execute('PRAGMA database_list').fetchone()[2]
            self.conn.close()
            self.conn = db.connect(database)
            queen = runtime.get_agent(self.conn, 'testproj', 'queen')
            recovered = bees.handoff(self.env(), REL, 'P1', queen)
            self.assertEqual(recovered['name'], first['name'])
            self.assertFalse(recovered['launched'])
            bees.report_attempt(self.env(), REL, 'P1', 'medium', 'interrupted', first['name'])
            result = fixtures.RepairContractFlow.start(self, name, head)
            self.assertTrue(result)
            return result

    def test_restarted_handoff_reaches_real_repair_verification_and_next_step(self):
        fixtures.RepairContractFlow.test_real_behavior_fix_runs_through_authorization_receipt_and_next_step(self)

    def test_invalid_receipt_preserves_reservation_then_corrected_result_advances(self):
        self.accept(*self.report())
        self.start('repair')
        path, data = self.receipt('repair')
        data['checks'][0]['exit'] = 1
        path.write_text(json.dumps(data))
        self.assertNotEqual(self.result('repair', 'progress', path)[0], 0)
        self.assertTrue(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1'))
        data['checks'][0]['exit'] = 0
        path.write_text(json.dumps(data))
        status, output = self.result('repair', 'progress', path)
        self.assertEqual(status, 0, output)
        self.assertFalse(bees.unconfirmed_embedded(self.conn, 'testproj', REL, 'P1'))
        self.assertIn('repair', diagnosis._load(self.conn, 'testproj', REL, 'P1')['progress'])
