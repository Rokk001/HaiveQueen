"""Stop, pause and a PAUSED plan block the start of every kind of Agent (Queen, Agent process, embedded subagent, ready-check agent);
running Agents are not ended, and starts work again after resume or start."""
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, hives, loop, readygate, runstate


class StartBlockTests(RoundCase):
    """A start is refused with a reason under each of the three blocks and allowed again afterwards."""

    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        self.agent('Runner', phase='bee', status='running', provider='codex', pid=43, plan_path=REL)

    def blocks(self):
        """The three blocks as (label, set, lift)."""
        flag = loop.stop_flag()

        def stop():
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text('', encoding='utf-8')

        def unstop():
            flag.unlink(missing_ok=True)

        def plan_pause():
            runtime.set_plan_status(self.conn, 'testproj', REL, 'PAUSED')

        def plan_resume():
            runtime.set_plan_status(self.conn, 'testproj', REL, 'IN_PROGRESS')

        self.addCleanup(unstop)
        return [('stop flag', stop, unstop),
                ('global pause', lambda: runstate.pause(self.conn), lambda: runstate.resume(self.conn)),
                ('plan paused', plan_pause, plan_resume)]

    def start_embedded(self):
        """Try to start an embedded subagent."""
        return bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [])

    def start_process_agent(self):
        """Try to start an own-process Agent with the runner replaced."""
        wt = self.base / 'bee-wt'
        wt.mkdir(exist_ok=True)
        self.machine['providers'][0]['beeMode'] = 'process'
        self.addCleanup(self.machine['providers'][0].pop, 'beeMode', None)
        with mock.patch('haivequeen.loop.ram_gb', return_value=1000), mock.patch('haivequeen.loop._min_ram', return_value=0), \
                mock.patch('haivequeen.loop._do_start', return_value=True):
            return bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt))

    def test_every_kind_of_start_is_refused(self):
        """Embedded subagent, Agent process and can_start_bee are refused with a reason under each block."""
        for label, block, lift in self.blocks():
            with self.subTest(label):
                block()
                bees.LAST_REFUSAL = ''
                self.assertFalse(self.start_embedded())
                self.assertTrue(bees.LAST_REFUSAL)
                bees.LAST_REFUSAL = ''
                self.assertFalse(self.start_process_agent())
                self.assertTrue(bees.LAST_REFUSAL)
                for own in (True, False):
                    self.assertFalse(hives.can_start_bee(self.conn, 'testproj', REL, own_process=own))
                self.assertTrue(hives.start_block_reason(self.conn, 'testproj', REL))
                lift()
                self.assertEqual(hives.start_block_reason(self.conn, 'testproj', REL), '')
                self.assertTrue(hives.can_start_bee(self.conn, 'testproj', REL, own_process=False))

    def test_embedded_start_allowed_again_after_resume(self):
        """The refusal names the block; after resume the same start gives a subagent."""
        runstate.pause(self.conn)
        self.assertFalse(self.start_embedded())
        self.assertIn('paused', bees.LAST_REFUSAL)
        runstate.resume(self.conn)
        self.assertEqual(self.start_embedded()['kind'], 'subagent')

    def test_running_agents_are_kept(self):
        """A block ends no running Queen or Agent."""
        for label, block, lift in self.blocks():
            block()
            self.start_embedded()
            names = {a['name'] for a in runtime.list_agents(self.conn, 'testproj', 'active') if a['status'] == 'running'}
            self.assertTrue({'Ada', 'Runner'} <= names, label)
            lift()

    def test_do_start_refuses_queen_and_ready_check(self):
        """The common start path returns 0 for a Queen and a ready-check agent under each block and changes no state."""
        for label, block, lift in self.blocks():
            block()
            for phase in ('queen', 'verify'):
                ctx = {'env': self.env(), 'state': {'names': [], 'busy': [], 'plan_status': {}, 'by_path': {}}, 'pool': loop.Pool({'claude': 1}, 0),
                       'report': [], 'dry': False, 'settle': 0}
                action = {'name': 'X', 'type': 'claude', 'phase': phase, 'plan': REL, 'package': '', 'cwd': str(self.base), 'prompt': 'p', 'class': 'medium'}
                self.assertEqual(loop._do_start(ctx, action, forced_pick={'name': 'claude', 'protocol': 'claude'}), 0, (label, phase))
                self.assertEqual(ctx['state']['names'], [])
            lift()

    def test_ready_check_agent_is_unavailable_under_block(self):
        """The ready check reports unavailable with a reason and starts nothing."""
        queen = {'name': 'Ada', 'cwd': str(self.base)}
        request = {'prompt': 'x', 'plan': REL, 'cwd': str(self.base)}
        for label, block, lift in self.blocks():
            block()
            with mock.patch('haivequeen.loop._do_start') as start:
                verdict, text = readygate.start_check_agent(self.env(), queen, request, sleep=lambda _: None)
            start.assert_not_called()
            self.assertEqual(verdict, 'unavailable', label)
            self.assertIn('may not start', text)
            lift()

    def test_queen_is_not_started_under_pause_and_starts_after_resume(self):
        """No new Queen while paused; the next round after resume starts one."""
        self.conn.execute("UPDATE agents SET status='done',pid=NULL,ended_at='2026-01-01T00:00:00' WHERE name='Ada'")
        self.conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", ('testproj', REL))
        runstate.pause(self.conn)
        self.round()
        self.assertFalse(self.live_queens())
        runstate.resume(self.conn)
        self.round()
        self.assertTrue(self.live_queens())

    def live_queens(self):
        """Queen rows that run or start."""
        return [a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen' and a['status'] in ('running', 'starting')]


if __name__ == '__main__':
    unittest.main()
