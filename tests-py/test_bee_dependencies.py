"""Regression tests for persisted package completion and Bee start gates."""
import contextlib
import io
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, cli, config, db, hives


class BeeDependencies(RoundCase):
    # Set up a Queen and a plan with a dependency and an all-dependency.
    def setUp(self):
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2', 'depends': ['P1']},
                                  {'id': 'FINAL', 'depends': ['AlLe']}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='running', provider='codex', pid=42)

    # Call the real CLI handler and capture its result.
    def start(self, package, extra=()):
        args = cli.build_parser().parse_args(['bee', 'start', '--project', 'testproj', '--plan', REL,
            '--package', package, '--class', 'medium', '--name', 'Ada', '--task', 'task',
            '--steps', '["one"]', '--cwd', str(self.wt), *extra])
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(output):
            code = cli.cmd_bee(args, self.conn)
        return code, output.getvalue()

    def test_cli_negotiates_capability_before_recording_a_start(self):
        self.machine['providers'][1]['beeMode'] = 'subagent'
        for extra, fallback in (((), True), (('--subagents',), False), (('--no-subagents',), True)):
            with self.subTest(extra=extra), mock.patch.object(bees, 'start_process', return_value={'kind': 'process'}) as start:
                code, output = self.start('P1', extra)
                self.assertEqual(code, 0, output)
                self.assertEqual(start.call_args.kwargs['no_subagents'], fallback)

    def test_supported_and_unsupported_sessions_start_the_expected_kind(self):
        import json
        self.mock_agent_worktrees()
        self.machine['providers'] = [self.machine['providers'][1]]
        self.machine['providers'][0]['beeMode'] = 'subagent'
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), \
                mock.patch('haivequeen.loop.ram_gb', return_value=16.0), \
                mock.patch('haivequeen.loop._do_start', return_value=True) as process:
            code, output = self.start('P1')
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output)['kind'], 'process')
            process.assert_called_once()
            self.assertEqual(bees.open_embedded_names(self.conn), set())
            code, output = self.start('P1', ('--subagents',))
            self.assertEqual(code, 0, output)
            self.assertEqual(json.loads(output)['kind'], 'subagent')
            self.assertEqual(len(bees.open_embedded_names(self.conn)), 1)
            self.assertEqual(self.machine['providers'][0]['beeMode'], 'subagent')

    def test_queen_prompt_negotiates_available_tools_without_cli_names(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000)
        self.assertIn('--subagents', prompt)
        self.assertIn('available', prompt.lower())
        self.assertNotIn('Subagent is available only for Claude Code', prompt)
        self.assertNotIn('If Codex exec cannot start subagents', prompt)

    # A completed dependency remains completed after reopening the database and later failure.
    def test_success_persists_and_is_not_undone(self):
        args = cli.build_parser().parse_args(['bee', 'result', '--project', 'testproj', '--plan', REL,
            '--package', 'P1', '--class', 'medium', '--outcome', 'succeeded', '--attempt', 'one'])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0)
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'failed', 'two')
        other = db.connect(config.db_path())
        try:
            self.assertTrue(bees.package_done(other, 'testproj', REL, 'P1'))
        finally:
            other.close()

    # Every routing result is gated before side effects and the refusal is logged.
    def test_refusal_and_completion_for_queen_and_subagent(self):
        self.mock_agent_worktrees()
        self.agent('existing', phase='bee', status='running', pid=55)
        for extra in (('--no-subagents',), ()):
            code, output = self.start('P2', extra)
            self.assertNotEqual(code, 0)
            self.assertIn('dependency P1 of package P2 not done', output)
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'existing')['status'], 'running')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P2', 'medium'), 0)
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', plan=REL), [])
        self.assertIn('dependency P1 of package P2 not done', (self.base / 'logs' / 'ROUNDS.log').read_text())
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'succeeded', 'done')
        self.machine['providers'][1]['beeMode'] = 'subagent'   # a Codex Queen that can start embedded subagents
        # Routing can choose a process provider even for a subagent-capable Queen.
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), \
                mock.patch('haivequeen.loop.ram_gb', return_value=16.0):
            code, output = self.start('P2')
        self.assertEqual(code, 0, output)

    # All and unknown dependencies are checked explicitly.
    def test_all_and_unknown_dependencies(self):
        self.assertIn('dependency P1 of package FINAL not done', self.start('FINAL')[1])
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'succeeded', 'one')
        self.assertIn('dependency P2 of package FINAL not done', self.start('FINAL')[1])
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2', 'depends': ['missing']}])
        self.assertIn('dependency missing of package P2 not in plan', self.start('P2')[1])
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2', 'depends': ['ALL', 'missing']}])
        self.assertIn('dependency missing of package P2 not in plan', self.start('P2')[1])

    # A combined package string cannot start any package as a bundle.
    def test_bundle_is_not_supported(self):
        self.assertNotEqual(self.start('P1,P2')[0], 0)
        self.assertFalse(any(a['name'] == 'Ada' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    # The Queen is told how to handle historic completions and unknown IDs.
    def test_queen_instructions_cover_migration_and_unknown_dependency(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000)
        self.assertIn('completed before success recording was available', prompt)
        self.assertIn('dependency is not in the plan, ask the owner', prompt)


if __name__ == '__main__':
    unittest.main()
