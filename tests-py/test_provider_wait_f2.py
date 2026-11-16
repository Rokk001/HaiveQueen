"""Wait target rules of `bzz agent wait` (F-7), the end of wait rows with their plan (F-8) and the not-ready wait text (F-6)."""
import contextlib
import io
import json
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, cli, hives


class ProviderWaitF2(RoundCase):
    def setUp(self):
        super().setUp()
        from bzztest import verified_tool_evidence
        verified_tool_evidence(self, {"codex": ["Image generation"]})
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)

    def wait(self, name):
        """Run `bzz agent wait` for a provider; return (exit code, parsed output)."""
        args = cli.build_parser().parse_args(['bee', 'wait', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'medium', '--provider', name])
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(output):
            code = cli.cmd_bee(args, self.conn)
        return code, output.getvalue()

    def prov(self, name):
        return next(p for p in self.machine['providers'] if p['name'] == name)

    def waiting_row(self):
        bees.ensure_attempts(self.conn)
        self.conn.execute('CREATE TABLE IF NOT EXISTS bee_provider_wait (project TEXT NOT NULL, plan_path TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path))')
        return self.conn.execute('SELECT provider FROM bee_provider_wait WHERE project=? AND plan_path=?', ('testproj', REL)).fetchone()

    # F-7: a disabled provider and one with 0 configured slots are no wait target.
    def test_disabled_and_slotless_provider_are_refused(self):
        self.prov('codex')['enabled'] = False
        code, text = self.wait('codex')
        self.assertNotEqual(code, 0)
        self.assertIn('disabled', text)
        self.prov('codex')['enabled'] = True
        self.prov('codex')['slots'] = 0
        code, text = self.wait('codex')
        self.assertNotEqual(code, 0)
        self.assertIn('0 configured slots', text)
        self.assertIsNone(self.waiting_row())

    # F-7: a needed capability only such providers offer: the refusal names it and the providers and tells the Queen to ask the owner.
    def test_refusal_names_capability_and_providers(self):
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        self.prov('codex')['slots'] = 0
        code, text = self.wait('codex')
        self.assertNotEqual(code, 0)
        self.assertIn('Image generation', text)
        self.assertIn('codex', text)
        self.assertIn('bzz ask', text)
        self.assertIsNone(self.waiting_row())

    # F-7: busy, limited, not ready and temporarily offline providers stay valid wait targets.
    def test_busy_not_ready_and_offline_are_accepted(self):
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        self.agent('bee-a', phase='bee', provider='codex', type='codex', status='running', pid=41)
        self.agent('bee-b', phase='bee', provider='codex', type='codex', status='running', pid=43)
        self.assertEqual(self.wait('codex')[0], 0)   # busy
        self.assertEqual(self.waiting_row()[0], 'codex')
        with mock.patch('haivequeen.servers.readiness', return_value=(False, 'the login is missing')):
            self.assertEqual(self.wait('codex')[0], 0)   # not ready
        self.assertEqual(self.wait('codex')[0], 0)   # offline for the moment keeps its configured slots

    def test_offline_provider_keeps_configured_slots_and_is_accepted(self):
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        env = self.env()
        codex = next(p for p in env.providers if p['name'] == 'codex')
        codex.update({'slots': 0, 'unavailable': 'offline', 'configuredSlots': 2})
        self.assertEqual(bees.wait_refusal(env, REL, codex), '')

    # F-8: wait rows end with the plan.
    def test_wait_row_ends_with_done_cancelled_and_paused_plan(self):
        for status in ('DONE', 'CANCELLED', 'PAUSED'):
            self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
            bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
            self.assertTrue(self.waiting_row())
            runtime.set_plan_status(self.conn, 'testproj', REL, status, str(self.repo))
            self.assertIsNone(self.waiting_row(), status)
            self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')

    def test_stale_row_is_removed_in_the_first_round_and_resume_has_none(self):
        bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
        self.plan('DONE', [{'id': 'P1', 'title': 'First'}])   # a stale row left by an older version
        self.round(alive=lambda pid: True)
        self.assertIsNone(self.waiting_row())
        runtime.set_plan_status(self.conn, 'testproj', REL, 'PAUSED', str(self.repo))
        runtime.set_plan_status(self.conn, 'testproj', REL, 'RUNNING', str(self.repo))
        self.assertIsNone(self.waiting_row())

    # F-6: the wait text of a plan that waits for a not-ready provider.
    def test_wait_text_for_not_ready_provider(self):
        env = self.env()
        hive = hives.get(self.conn, 'testproj', REL)
        claude = dict(next(p for p in env.providers if p['name'] == 'claude'), login=False, notReady='the login is missing')
        can, detail = hives.provider_wait_check(self.conn, env, hive, claude, 2, REL)
        self.assertFalse(can)
        self.assertEqual(detail, 'not ready: the login is missing')

    def test_round_blocker_says_not_ready(self):
        self.prov('codex')['slots'] = 0
        self.conn.execute("UPDATE agents SET status='done',pid=NULL WHERE name='queen'")
        bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
        with mock.patch('haivequeen.servers.readiness', return_value=(False, 'the login is missing')):
            self.round()
        self.assertEqual(runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + REL), 'waiting for provider claude: not ready: the login is missing')


if __name__ == '__main__':
    unittest.main()
