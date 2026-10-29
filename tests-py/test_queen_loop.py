"""Tests of the fix of the verify loop: a Codex verify keeps its answer in report.txt (read like report.md), a verify with open items is followed
by work and never by another verify, a package whose dependencies are not finished gets no verify, and a task whose agents only vanished
(window closed, killed, crash, restart of the Queen) is neither blocked nor counted as failed and is started again."""
from bzztest import REL, RoundCase
from haivequeen import routine

INTRO = "Ich lese den vollstaendigen Auftrag und fuehre anschliessend die beschriebenen Schritte aus."
VANISHED = "runner process vanished (ended by executor or crashed)"


def pk(pid, title, depends=(), cls="medium"):
    """Package row of the dummy plan."""
    return {"id": pid, "title": title, "class": cls, "depends": list(depends)}


class ReportTxtTest(RoundCase):
    """The answer of a verify agent is final.txt, completed by report.txt or report.md of its run folder."""

    def setUp(self):
        """One finished verify agent of package E1."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("E1", "single task")])

    def write_report(self, name, text):
        """Write report.txt into the run folder of an agent."""
        d = self.env().run_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.txt").write_text(text, encoding="utf-8")

    def test_report_txt_supplies_missing_count_line(self):
        """final.txt holds only the first sentence; the count line of report.txt decides (2 open requirements)."""
        self.agent("DUMMY-E1-verify", package="E1", phase="verify", type="codex", provider="codex", started=10, ended=5)
        self.finish("DUMMY-E1-verify", INTRO)
        self.write_report("DUMMY-E1-verify", "1. A is missing\n2. B is missing\n\nOffene Anforderungen: 2\n")
        env = self.env()
        self.assertEqual(routine.verify_open_count(env, "DUMMY-E1-verify"), 2)
        self.assertFalse(routine.verify_zero(env, "DUMMY-E1-verify"))

    def test_open_items_in_report_txt_are_followed_by_work_not_by_verify(self):
        """The verify ended with a count of 2 in report.txt: the next agent of the package is a work agent, no second verify is started."""
        self.agent("DUMMY-E1-work", package="E1", started=30, ended=20)
        self.agent("DUMMY-E1-verify", package="E1", phase="verify", type="codex", provider="codex", started=15, ended=10)
        self.finish("DUMMY-E1-verify", INTRO)
        self.write_report("DUMMY-E1-verify", "Offene Anforderungen: 2\n")
        self.round()
        self.assertIn("DUMMY-E1-work-2", self.names())
        self.assertNotIn("DUMMY-E1-verify-2", self.names())


class DependencyVerifyTest(RoundCase):
    """A package whose dependencies are not finished gets no verify."""

    def setUp(self):
        """D2 depends on D1; both have a finished work agent and no verify yet."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("D1", "first"), pk("D2", "second", depends=["D1"])])
        self.agent("DUMMY-D1-work", package="D1", started=40, ended=35)
        self.agent("DUMMY-D2-work", package="D2", started=30, ended=25)

    def test_no_verify_while_a_dependency_is_not_verified(self):
        """D1 is not verified yet: D1 gets its verify, D2 gets none."""
        self.round()
        self.assertIn("DUMMY-D1-verify", self.names())
        self.assertNotIn("DUMMY-D2-verify", self.names())

    def test_verify_when_the_dependency_is_verified(self):
        """D1 ended with 'Offene Anforderungen: 0': D2 gets its verify."""
        self.agent("DUMMY-D1-verify", package="D1", phase="verify", started=24, ended=20)
        self.finish("DUMMY-D1-verify", "Offene Anforderungen: 0")
        self.round()
        self.assertIn("DUMMY-D2-verify", self.names())


class VanishedEndsTest(RoundCase):
    """Window closed, killed, crashed or lost by a Queen restart: not a failure, no block, no lock, started again."""

    def setUp(self):
        """Package R1 without dependencies."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("R1", "first task")])

    def vanished_work(self, n, why=VANISHED):
        """A work agent of R1 that vanished; n numbers it."""
        self.agent("DUMMY-R1-work" + ("" if n == 1 else "-%d" % n), package="R1", started=100 - 10 * n, ended=95 - 10 * n,
                   status="failed", last_line=why)

    def test_two_vanished_ends_do_not_block(self):
        """Two vanished work ends with the same text block nothing; two real errors with the same text still block."""
        self.vanished_work(1)
        self.vanished_work(2)
        self.assertEqual(routine.blocked_tasks(routine.all_agents(self.env())), [])
        self.agent("DUMMY-R2-work", package="R2", started=50, ended=45, status="failed", last_line="boom")
        self.agent("DUMMY-R2-work-2", package="R2", started=40, ended=35, status="failed", last_line="boom")
        self.assertEqual(len(routine.blocked_tasks(routine.all_agents(self.env()))), 1)

    def test_lost_by_queen_restart_does_not_block(self):
        """The text a Queen restart writes ('process gone after orchestrator restart') is a vanished end as well."""
        self.vanished_work(1, "process gone after orchestrator restart")
        self.vanished_work(2, "process gone after orchestrator restart")
        self.assertEqual(routine.blocked_tasks(routine.all_agents(self.env())), [])

    def test_vanished_ends_are_no_failures(self):
        """Three vanished work ends count as no failure, so they cause no escalation and no lock."""
        for n in (1, 2, 3):
            self.vanished_work(n)
        env = self.env()
        self.assertEqual(routine.package_failures(env, routine.all_agents(env), REL, "R1"), 0)

    def test_vanished_work_is_started_again(self):
        """After two vanished ends the next round starts a new work agent for the package."""
        self.vanished_work(1)
        self.vanished_work(2)
        self.round()
        self.assertIn("DUMMY-R1-work-3", self.names())
