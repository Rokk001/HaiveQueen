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
        self.agent('existing', phase='bee', status='running', pid=55)
        for extra in (('--no-subagents',), ('--parallel',), ()):
            code, output = self.start('P2', extra)
            self.assertNotEqual(code, 0)
            self.assertIn('dependency P1 of package P2 not done', output)
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'existing')['status'], 'running')
        self.assertEqual(bees.failure_count(self.conn, 'testproj', REL, 'P2', 'medium'), 0)
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', plan=REL), [])
        self.assertIn('dependency P1 of package P2 not done', (self.base / 'logs' / 'ROUNDS.log').read_text())
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'succeeded', 'done')
        self.assertEqual(self.start('P2', ('--no-subagents',))[0], 0)

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
