"""Tests of the package progress a Queen reports with `bzz package` and the window shows."""
import argparse
import contextlib
import io
import json
import unittest
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, cli, gui_data, hives


class PackageProgressTest(RoundCase):
    """A Queen reports the state of her packages; the Hive progress counts the done ones."""

    # A Hive with three packages and a running Queen.
    def setUp(self):
        """Plan with packages P1 to P3 and a running Queen."""
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}, {'id': 'P2'}, {'id': 'P3'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.allow_ready()

    # Run the real `bzz package` handler as the Queen (or as another agent).
    def report(self, package, state, agent='queen'):
        """Return the exit code of `bzz package` called by `agent`."""
        args = cli.build_parser().parse_args(['package', '--project', 'testproj', '--plan', REL, '--package', package, '--state', state])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': agent}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.cmd_package(args, self.conn)

    # The Hive row of the window.
    def hive(self):
        """Progress row of the Hive from the window's snapshot data."""
        assigned, completed = gui_data.bee_tables(self.conn)
        agents = [a for a in cli.runtime.list_agents(self.conn, 'testproj', 'all')]
        packages = {('testproj', REL): 3}
        hive = next(h for h in hives.hive_data(self.conn, None, agents) if h['plan_path'] == REL)
        return gui_data.hive_row(hive, agents, packages, gui_data.datetime.now(), assigned, completed, None, agents, gui_data.implemented_counts(self.conn))

    # Done packages count, implemented ones show separately.
    def test_queen_reports_drive_the_progress(self):
        """Two packages reported done and one implemented give 2/3, 67 percent and one implemented package."""
        self.assertEqual(self.report('P1', 'done'), 0)
        self.assertEqual(self.report('P2', 'done'), 0)
        self.assertEqual(self.report('P3', 'implemented'), 0)
        row = self.hive()
        self.assertEqual((row['done'], row['total'], row['progress'], row['implemented']), (2, 3, 67, 1))
        self.assertEqual(self.report('P3', 'done'), 0)
        self.assertEqual((self.hive()['done'], self.hive()['implemented']), (3, 0))

    # Only the Queen of the Hive may report, only for packages of her plan, only with a known state.
    def test_refuses_foreign_unknown_and_invalid(self):
        """Another agent and an unknown package are refused and store nothing; an invalid state is rejected by the parser and by the function."""
        self.agent('bee-1', phase='bee', status='running', provider='claude', pid=43)
        bees.ensure_attempts(self.conn)
        self.assertEqual(self.report('P1', 'done', agent='bee-1'), 1)
        self.assertEqual(self.report('NOPE', 'done'), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM package_progress').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'finished')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(['package', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--state', 'finished'])

    # A Bee that succeeded completes its package in the same table.
    def test_bee_success_marks_the_package_done(self):
        """record_attempt with outcome succeeded stores state done."""
        bees.record_attempt(self.conn, 'testproj', REL, 'P1', 'medium', 'succeeded', 'one')
        self.assertEqual(self.conn.execute("SELECT state FROM package_progress WHERE package='P1'").fetchone()[0], 'done')

    # Ready by the Queen means every package is done.
    def test_ready_completes_all_packages(self):
        """After the Queen registered her branch ready, all packages of the plan count as done."""
        self.report('P1', 'working')
        args = argparse.Namespace(project='testproj', branch='branch', tip='abc', plan=REL, note='')
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_ready(args, self.conn), 0)
        states = dict(self.conn.execute('SELECT package,state FROM package_progress').fetchall())
        self.assertEqual(states, {'P1': 'done', 'P2': 'done', 'P3': 'done'})

    # A step the owner did not accept is not done and keeps the Queen from reporting ready.
    def test_rejected_package_is_not_done_and_blocks_ready(self):
        """Rejected P2 is left out of the done count and shown as rejected; bzz ready is refused until P2 is reworked, then it goes through."""
        for package in ('P1', 'P2', 'P3'):
            self.report(package, 'done')
        bees.record_attempt(self.conn, 'testproj', REL, 'P2', 'medium', 'succeeded', 'one')   # also a finished Bee attempt must not count it done
        bees.set_package_state(self.conn, 'testproj', REL, 'P2', 'rejected', 'owner')
        assigned, completed = gui_data.bee_tables(self.conn)
        agents = [a for a in cli.runtime.list_agents(self.conn, 'testproj', 'all')]
        hive = next(h for h in hives.hive_data(self.conn, None, agents) if h['plan_path'] == REL)
        row = gui_data.hive_row(hive, agents, {('testproj', REL): 3}, gui_data.datetime.now(), assigned, completed, None, agents, {}, None, gui_data.rejected_map(self.conn))
        self.assertEqual((row['done'], row['rejected'], row['progress']), (2, 1, 67))
        self.assertEqual(bees.rejected_packages(self.conn, 'testproj', REL), ['P2'])
        args = argparse.Namespace(project='testproj', branch='branch', tip='abc', plan=REL, note='')
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_ready(args, self.conn), 1)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM queue').fetchone()[0], 0)
            self.assertEqual(self.report('P2', 'implemented'), 0)
            self.assertEqual(cli.cmd_ready(args, self.conn), 0)

    def test_reopened_package_progress_overrides_historical_completion(self):
        """A reopened package is not shown as done from its old Bee completion or Queen state."""
        self.assertEqual(self.report('P1', 'done'), 0)
        self.conn.execute("INSERT INTO bee_completed(project,plan_path,package) VALUES ('testproj',?,'P2')", (REL,))
        self.conn.commit()
        self.assertEqual(self.report('P1', 'working'), 0)
        self.assertEqual(self.report('P2', 'implemented'), 0)
        assigned, completed = gui_data.bee_tables(self.conn)
        self.assertNotIn(('testproj', REL, 'P1'), completed)
        self.assertNotIn(('testproj', REL, 'P2'), completed)
        self.assertEqual(gui_data.implemented_counts(self.conn)[('testproj', REL)], 1)

    # The Queen instruction names the command.
    def test_queen_instruction_names_the_package_command(self):
        """The Queen is told to report package progress."""
        self.assertIn('bzz package', bees.queen_instructions('medium', 3, 3, 300000))


if __name__ == '__main__':
    unittest.main()
