"""Tests of what counts as a failed attempt: waiting for an answer, a build or the owner is no failure and never escalates a package."""
import contextlib
import io
import unittest
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, cli, db, hives, plans, routine


class AttemptCountingTest(RoundCase):
    """Attempts of a package closed by code and of packages closed by build or owner."""

    # A Hive with a code package, a build package and an owner package, plus a running Queen.
    def setUp(self):
        """Plan with C1 (code), B1 (verify build) and O1 (verify owner) and a running Queen."""
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'C1'}, {'id': 'B1', 'verify': 'build'}, {'id': 'O1', 'verify': 'owner'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)

    # Run the real `bzz bee result` handler as the Queen and return the exit code.
    def result(self, package, outcome, attempt, *extra):
        """Exit code of `bzz agent result` for one attempt."""
        args = cli.build_parser().parse_args(['bee', 'result', '--project', 'testproj', '--plan', REL, '--package', package,
                                              '--class', 'medium', '--outcome', outcome, '--attempt', attempt, *extra])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.cmd_bee(args, self.conn)

    # Read the persisted failure count of a package at class medium.
    def failures(self, package):
        """Counted failures of the package at class medium."""
        return bees.failure_count(self.conn, 'testproj', REL, package, 'medium')

    # The old rule still holds for a package closed by code.
    def test_code_package_counts_a_repeated_open_requirement(self):
        """The same open requirement after two attempts counts once; the first attempt never counts."""
        for n in range(3):
            self.assertEqual(self.result('C1', 'failed', 'a%d' % n, '--open-requirements', '["R-1"]', '--error', 'err %d' % n), 0)
        self.assertEqual(self.failures('C1'), 2)

    # The QS-7 reports: work finished, requirements stay open until the build and the owner check.
    def test_open_requirements_of_an_owner_package_never_count_and_a_build_package_is_a_code_package(self):
        """Three attempts with the same open requirements and changing text do not count for a package closed by the owner; a package marked build is closed like code (O-4)."""
        for package, expected in (('B1', 2), ('O1', 0)):
            for n in range(3):
                self.assertEqual(self.result(package, 'failed', '%s-%d' % (package, n), '--open-requirements', '["T-1","T-2"]', '--error', 'text %d' % n), 0)
            self.assertEqual(self.failures(package), expected)

    # A package the Bee has finished (implemented) never escalates.
    def test_implemented_package_never_counts(self):
        """Repeated same error and requirements do not count once the package is implemented."""
        bees.set_package_state(self.conn, 'testproj', REL, 'C1', 'implemented', 'queen')
        for n in range(4):
            self.result('C1', 'failed', 'i%d' % n, '--open-requirements', '["R-1"]', '--error', 'same')
        self.assertEqual(self.failures('C1'), 0)

    # A Bee that waits for an answer is no failure, and the answer makes the package work again.
    def test_waiting_for_a_question_is_no_failure_and_resumes_after_the_answer(self):
        """blocked-by question sets the package to waiting without counting; answering the question sets it to working."""
        qid = db.add_question(self.conn, 'testproj', REL, 'C1', 'Which colour?', 'Red.')
        for n in range(4):
            self.assertEqual(self.result('C1', 'failed', 'w%d' % n, '--blocked-by', 'question', '--blocked-ref', str(qid)), 0)
        self.assertEqual(self.failures('C1'), 0)
        state = self.conn.execute("SELECT state, reason FROM package_progress WHERE package='C1'").fetchone()
        self.assertEqual((state['state'], state['reason']), ('waiting', 'question:%d' % qid))
        db.answer_question(self.conn, qid, 'Red.', 'owner')
        state = self.conn.execute("SELECT state, reason FROM package_progress WHERE package='C1'").fetchone()
        self.assertEqual((state['state'], state['reason']), ('working', ''))

    # Blocked by a build or the owner needs no question.
    def test_waiting_for_the_owner_and_never_for_a_build(self):
        """blocked-by owner sets waiting with that reason and counts nothing; the Queen never waits for a build (the Build step comes after bzz ready)."""
        self.assertNotEqual(self.result('B1', 'failed', 'b1', '--blocked-by', 'build'), 0)
        self.assertEqual(self.result('O1', 'failed', 'o1', '--blocked-by', 'owner'), 0)
        reasons = {r['package']: r['reason'] for r in self.conn.execute("SELECT package, reason FROM package_progress WHERE state='waiting'")}
        self.assertEqual(reasons, {'O1': 'owner'})
        self.assertEqual((self.failures('B1'), self.failures('O1')), (0, 0))

    # Wrong input is refused and stores nothing.
    def test_refusals(self):
        """A failed result without evidence, a blocked question without an open question and blocked together with succeeded are refused."""
        self.assertNotEqual(self.result('C1', 'failed', 'x1'), 0)
        self.assertNotEqual(self.result('C1', 'failed', 'x2', '--blocked-by', 'question', '--blocked-ref', '999'), 0)
        self.assertNotEqual(self.result('C1', 'succeeded', 'x3', '--blocked-by', 'build'), 0)
        bees.ensure_attempts(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM package_progress").fetchone()[0], 0)

    # The window shows waiting packages with their reason.
    def test_window_row_shows_waiting_packages(self):
        """hive_row counts the waiting packages and names each with its reason; without any the count is zero."""
        from haivequeen import dashboard_data
        hive = {'project': 'testproj', 'plan_path': REL, 'name': 'Hive', 'status': 'active', 'bees': []}
        self.assertEqual(dashboard_data.waiting_map(self.conn), {})
        bees.set_package_state(self.conn, 'testproj', REL, 'B1', 'waiting', 'queen', 'build')
        bees.set_package_state(self.conn, 'testproj', REL, 'C1', 'waiting', 'queen', 'question:7')
        waiting = dashboard_data.waiting_map(self.conn)
        self.assertEqual(waiting[('testproj', REL)], [('B1', 'build'), ('C1', 'question:7')])
        row = dashboard_data.hive_row(hive, [], {('testproj', REL): 3}, dashboard_data.datetime.now(), waiting=waiting)
        self.assertEqual((row['waiting'], row['waiting_reasons']), (2, 'B1 build, C1 question:7'))
        self.assertEqual(dashboard_data.hive_row(hive, [], {('testproj', REL): 3}, dashboard_data.datetime.now())['waiting'], 0)

    # The verify count of the second counter ignores packages closed by build or owner.
    def test_open_verify_items_count_only_for_code_packages(self):
        """A finished verify with open items is one failure for a code package (a package marked build is one) and none for an owner package."""
        agents = [{'plan': REL, 'package': p, 'bundle': [], 'phase': 'verify', 'status': 'done', 'name': 'V-' + p} for p in ('C1', 'O1')]
        with mock.patch.object(routine, 'verify_open_count', return_value=2):
            self.assertEqual(routine.package_failures(self.env(), agents, REL, 'C1'), 1)
            self.assertEqual(routine.package_failures(self.env(), agents, REL, 'O1'), 0)


class PlanVerifyTest(unittest.TestCase):
    """The plan reader knows the verify field."""

    # A missing verify is code; known values are kept; an unknown value is code with a warning.
    def test_verify_values_and_warning(self):
        """verify defaults to code, build and owner are kept, and a wrong value warns and falls back to code."""
        text = ('+++\nstatus = "READY"\n[[packages]]\nid = "A"\n[[packages]]\nid = "B"\nverify = "build"\n'
                '[[packages]]\nid = "C"\nverify = "Owner"\n[[packages]]\nid = "D"\nverify = "soon"\n+++\n# T\n')
        plan = plans.parse_plan_text(text)
        self.assertEqual([p.verify for p in plan.packages], ['code', 'build', 'owner', 'code'])
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn('package D', plan.warnings[0])


if __name__ == "__main__":
    unittest.main()
