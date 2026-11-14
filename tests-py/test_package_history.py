"""Targeted retrieval of package evidence without copying full histories."""
import contextlib
import io
import json
from bzztest import REL, RoundCase
from haivequeen import bees, cli


class PackageHistory(RoundCase):
    def history(self, *extra):
        args = cli.build_parser().parse_args([
            'agent', 'history', '--project', 'testproj', '--plan', REL,
            '--package', 'P1', *extra])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0)
        return json.loads(output.getvalue())

    def record(self, name, project='testproj', plan=REL, package='P1'):
        bees.record_attempt(self.conn, project, plan, package, 'medium', 'failed',
                            name, ['R1'], 'failure-' + name, force_failure=True)

    def test_history_is_read_only_scoped_and_paginated(self):
        for i in range(7):
            self.record('attempt-' + str(i))
        self.record('foreign-project', project='other')
        self.record('foreign-plan', plan='other.md')
        self.record('foreign-package', package='P2')
        changes = self.conn.total_changes
        first = self.history()
        self.assertEqual(first['total'], 7)
        self.assertEqual([r['attempt'] for r in first['attempts']],
                         ['attempt-' + str(i) for i in range(6, 1, -1)])
        self.assertEqual(first['next_before'], first['attempts'][-1]['sequence'])
        second = self.history('--before', str(first['next_before']))
        self.assertEqual([r['attempt'] for r in second['attempts']], ['attempt-1', 'attempt-0'])
        self.assertIsNone(second['next_before'])
        exact = self.history('--attempt', 'attempt-3')
        self.assertEqual(len(exact['attempts']), 1)
        self.assertEqual(exact['attempts'][0]['error'], 'failure-attempt-3')
        self.assertEqual(exact['attempts'][0]['open_requirements'], ['R1'])
        self.assertEqual(self.conn.total_changes, changes)

    def test_long_error_is_previewed_and_retrieved_only_on_request(self):
        error = 'important evidence ' * 1000
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'failed',
                            'long-error', ['R1'], error, force_failure=True)
        preview = self.history()['attempts'][0]
        self.assertEqual(len(preview['error']), 500)
        self.assertTrue(preview['error_truncated'])
        exact = self.history('--attempt', 'long-error')['attempts'][0]
        self.assertEqual(exact['error'], error)
        self.assertFalse(exact['error_truncated'])
        with self.assertRaises(ValueError):
            bees.attempt_history(self.conn, 'testproj', REL, 'P1', limit=1000)

    def test_empty_history_does_not_create_attempt_tables(self):
        # The fixture may initialize them; explicitly remove only fixture tables.
        self.conn.execute('DROP TABLE IF EXISTS bee_attempt_signals')
        changes = self.conn.total_changes
        self.assertEqual(self.history()['attempts'], [])
        self.assertEqual(self.conn.total_changes, changes)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='bee_attempt_signals'").fetchone())

    def test_cli_rejects_missing_scope_and_invalid_page_size(self):
        for extra in (['--package', ''], ['--plan', ''], ['--limit', '0'], ['--before', '-1']):
            with self.subTest(extra=extra):
                args = cli.build_parser().parse_args([
                    'agent', 'history', '--project', 'testproj', '--plan', REL,
                    '--package', 'P1', *extra])
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertNotEqual(cli.cmd_bee(args, self.conn), 0)
