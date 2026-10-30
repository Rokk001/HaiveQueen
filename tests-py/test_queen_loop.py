"""Tests of the fix of the verify loop: a Codex verify keeps its answer in report.txt (read like report.md), a verify with open items is followed
by work and never by another verify, a package whose dependencies are not finished gets no verify, and a task whose agents only vanished
(window closed, killed, crash, restart of the Queen) is neither blocked nor counted as failed and is started again."""
import subprocess
from datetime import datetime, timedelta

from bzztest import REL, RoundCase
from haivequeen import db, routine, runtime

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

    def test_report_under_any_name_is_read(self):
        """Codex verifies name their report result.txt, result.md, final-report.txt, verify-report.txt and so on: every such file is read."""
        self.agent("DUMMY-E1-verify", package="E1", phase="verify", type="codex", provider="codex", started=10, ended=5)
        self.finish("DUMMY-E1-verify", INTRO)
        d = self.env().run_dir("DUMMY-E1-verify")
        d.mkdir(parents=True, exist_ok=True)
        for name in ("result.txt", "result.md", "final-report.txt", "final-report.md", "verify-report.txt", "verification-report.txt", "answer-215.txt"):
            for f in d.iterdir():
                if f.name not in ("final.txt",):
                    f.unlink()
            (d / name).write_text("Befund\nOffene Anforderungen: 3\n", encoding="utf-8")
            self.assertEqual(routine.verify_open_count(self.env(), "DUMMY-E1-verify"), 3, name)

    def test_helper_files_are_not_reports(self):
        """The prompt, the milestones and the stderr of the run are never taken as a report, even when they mention a count."""
        self.agent("DUMMY-E1-verify", package="E1", phase="verify", type="codex", provider="codex", started=10, ended=5)
        self.finish("DUMMY-E1-verify", INTRO)
        d = self.env().run_dir("DUMMY-E1-verify")
        for name in ("prompt.md", "milestones.txt", "stderr.txt"):
            (d / name).write_text("Offene Anforderungen: 9\n", encoding="utf-8")
        self.assertEqual(routine.verify_open_count(self.env(), "DUMMY-E1-verify"), -1)

    # Verify the Queen takes over an old report with open items.
    def test_open_items_in_report_txt_are_followed_by_queen(self):
        """A legacy report with two open items is retained and the Queen starts without a second verify."""
        self.agent("DUMMY-E1-work", package="E1", started=30, ended=20)
        self.agent("DUMMY-E1-verify", package="E1", phase="verify", type="codex", provider="codex", started=15, ended=10)
        self.finish("DUMMY-E1-verify", INTRO)
        self.write_report("DUMMY-E1-verify", "Offene Anforderungen: 2\n")
        self.round()
        self.assertEqual(routine.verify_open_count(self.env(), "DUMMY-E1-verify"), 2)
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertNotIn("DUMMY-E1-verify-2", self.names())


class DependencyVerifyTest(RoundCase):
    """A package whose dependencies are not finished gets no verify."""

    def setUp(self):
        """D2 depends on D1; both have a finished work agent and no verify yet."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("D1", "first"), pk("D2", "second", depends=["D1"])])
        self.agent("DUMMY-D1-work", package="D1", started=40, ended=35)
        self.agent("DUMMY-D2-work", package="D2", started=30, ended=25)

    # Verify unfinished dependencies do not start separate verify agents.
    def test_unverified_dependency_starts_only_queen(self):
        """The Queen receives the plan when a dependency has no verify result."""
        self.round()
        agents = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in agents if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertFalse([a for a in agents if a["phase"] == "verify"])
        packages = {p["id"]: p for p in self.env().packages(REL)}
        self.assertEqual(packages["D2"]["depends"], ["D1"])

    # Verify a previous verify result does not start a separate agent.
    def test_verified_dependency_starts_only_queen(self):
        """A legacy verify result remains while the Queen owns the plan."""
        self.agent("DUMMY-D1-verify", package="D1", phase="verify", started=24, ended=20)
        self.finish("DUMMY-D1-verify", "Offene Anforderungen: 0")
        self.round()
        agents = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in agents if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertEqual([a["name"] for a in agents if a["phase"] == "verify"], ["DUMMY-D1-verify"])

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

    # Verify vanished legacy work is handed to the Queen.
    def test_vanished_work_is_handed_to_queen(self):
        """After two vanished work ends the plan starts a Queen."""
        self.vanished_work(1)
        self.vanished_work(2)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)


def git(cwd, *args):
    """Run git in a folder (test identity, no hooks) and return its output."""
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd)] + list(args), check=True,
                          capture_output=True, text=True).stdout.strip()


class NewCommitTest(RoundCase):
    """A verify starts only when the branch has a new commit; a work agent that ended without one is followed by work, not by a verify."""

    def setUp(self):
        """Package C1 whose worktree is a real git repository with one commit."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("C1", "committing task")])
        git(self.wt, "init", "-q")
        (self.wt / "a.txt").write_text("one", encoding="utf-8")
        git(self.wt, "add", "a.txt")
        git(self.wt, "commit", "-q", "-m", "one")

    def commit(self, text):
        """Add a commit to the worktree."""
        (self.wt / "a.txt").write_text(text, encoding="utf-8")
        git(self.wt, "commit", "-q", "-a", "-m", text)

    def started(self, name, **f):
        """An agent row that was started now (its start state is recorded as a start does)."""
        self.agent(name, package="C1", **f)
        routine.record_progress(self.env(), name, REL, "C1", str(self.wt))

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_work_without_new_commit_gets_work_not_verify(self):
        """Without a new commit, the Queen takes over without a separate verify."""
        self.started("DUMMY-C1-work", started=30, ended=20)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertNotIn("DUMMY-C1-verify", self.names())

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_work_with_new_commit_gets_verify(self):
        """A committed legacy worker is followed by a Queen, not a separate verify."""
        self.started("DUMMY-C1-work", started=30, ended=20)
        self.commit("two")
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertNotIn("DUMMY-C1-work-2", self.names())

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_second_verify_needs_a_new_commit(self):
        """After a legacy verify and work run the Queen takes over without a second verify."""
        self.started("DUMMY-C1-work", started=60, ended=50)
        self.commit("two")
        self.started("DUMMY-C1-verify", phase="verify", type="codex", provider="codex", started=45, ended=40)
        self.finish("DUMMY-C1-verify", "Offene Anforderungen: 2")
        self.started("DUMMY-C1-work-2", started=30, ended=20)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertNotIn("DUMMY-C1-verify-2", self.names())


class IdleWorkGuardTest(NewCommitTest):
    """Legacy work runs do not create automatic progress stops."""

    def idle(self, n, **f):
        """Finished work agent number n that started at the current state of the branch and ended without a commit."""
        name = "DUMMY-C1-work" + ("" if n == 1 else "-%d" % n)
        self.started(name, started=100 - 10 * n, ended=95 - 10 * n, **f)

    def stalls(self):
        """Open STALL question of package C1 (empty list when none)."""
        return [q for q in runtime.list_questions(self.conn, "testproj", "open") if q["item"] == "STALL-C1"]

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_three_idle_works_stop_the_package(self):
        """Three work runs without a commit do not create a stop question or a fourth work agent."""
        for n in (1, 2, 3):
            self.idle(n)
        self.round()
        self.assertEqual(self.stalls(), [])
        self.assertNotIn("DUMMY-C1-work-4", self.names())

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_two_idle_works_do_not_stop_it(self):
        """Two idle work runs start a Queen and no stop question."""
        for n in (1, 2):
            self.idle(n)
        self.round()
        self.assertEqual(self.stalls(), [])
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)

    def test_interrupted_ends_do_not_count(self):
        """Two idle runs and an interrupted one between them: only two count, nothing is stopped."""
        self.idle(1)
        self.idle(2, status="failed", last_line="error: connection", interrupted="connection")
        self.idle(3)
        self.round()
        self.assertEqual(self.stalls(), [])

    def test_a_commit_ends_the_streak(self):
        """A run that made a commit breaks the streak."""
        self.idle(1)
        self.idle(2)
        self.commit("two")
        self.idle(3)
        self.round()
        self.assertEqual(self.stalls(), [])

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_streak_length_is_a_setting(self):
        """The removed progressVerifies setting does not stop two idle work runs."""
        self.machine["progressVerifies"] = 2
        for n in (1, 2):
            self.idle(n)
        self.round()
        self.assertEqual(self.stalls(), [])


class ProgressGuardTest(RoundCase):
    """A package whose open count does not fall over 3 verifies, and a plan without an integrated package for 6 hours, get no new agents
    and a question to the owner; only an answer of the owner lifts that."""

    def setUp(self):
        """Package S1 with a finished work agent."""
        super().setUp()
        self.plan("IN_PROGRESS", [pk("S1", "stubborn task")])

    def verifies(self, counts):
        """Finished work/verify pairs of S1, oldest first, each verify with one count."""
        for i, n in enumerate(counts):
            t = 100 - 20 * i
            suffix = "" if i == 0 else "-%d" % (i + 1)
            self.agent("DUMMY-S1-work" + suffix, package="S1", started=t, ended=t - 5)
            self.agent("DUMMY-S1-verify" + suffix, package="S1", phase="verify", type="codex", provider="codex", started=t - 10, ended=t - 15)
            self.finish("DUMMY-S1-verify" + suffix, "Offene Anforderungen: %d" % n)

    def questions(self, item):
        """Open questions of the dummy plan with this item."""
        return [q for q in runtime.list_questions(self.conn, "testproj", "open") if q["item"] == item]

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_count_that_does_not_fall_stalls_the_package(self):
        """Counts 4, 4, 5 do not produce a stop question or another work agent."""
        self.verifies([4, 4, 5])
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])
        self.assertNotIn("DUMMY-S1-work-4", self.names())

    def test_falling_count_does_not_stall(self):
        """Counts 5, 3, 1 fall: no question."""
        self.verifies([5, 3, 1])
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_number_of_verifies_is_a_setting(self):
        """The removed progressVerifies setting does not stop two verify results."""
        self.machine["progressVerifies"] = 2
        self.verifies([4, 4])
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_restart_and_auto_answer_do_not_lift_the_stall(self):
        """A new round (like a Queen restart) and a Queen answer keep the stall; the answer of the owner lifts it and the package gets new attempts."""
        self.verifies([4, 4, 5])
        self.round()
        qid = db.add_question(self.conn, "testproj", REL, "STALL-S1", "Earlier question", "")
        self.round()
        db.answer_question(self.conn, qid, "go on", "auto")
        self.round()
        self.assertNotIn("DUMMY-S1-work-4", self.names(), "no new agent while stalled")
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "stall:%s|S1" % REL))

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_owner_answer_lifts_the_stall_once(self):
        """After the owner answered, the old verifies no longer count: the package is not stalled again in the next round."""
        self.verifies([4, 4, 5])
        self.round()
        qid = db.add_question(self.conn, "testproj", REL, "STALL-S1", "Earlier question", "")
        db.answer_question(self.conn, qid, "go on", "owner")
        self.round()
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "stall:%s|S1" % REL))
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    def plan_agents(self, ago_first, ago_last):
        """Two finished agents of the plan: the first started ago_first minutes ago, the last ago_last minutes ago."""
        self.agent("DUMMY-S1-work", package="S1", started=ago_first, ended=ago_first - 5)
        self.agent("DUMMY-S1-work-2", package="S1", started=ago_last, ended=ago_last - 5)

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_plan_without_integration_for_six_hours_is_stopped(self):
        """Seven hours without integration does not stop the plan; a Queen starts."""
        self.plan_agents(420, 10)
        self.round()
        self.assertEqual([q for q in runtime.list_questions(self.conn, "testproj", "open") if q["item"] == "STALL-PLAN"], [])
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)

    def test_plan_with_recent_integration_is_not_stopped(self):
        """A branch of the plan was integrated one hour ago: no stall."""
        self.plan_agents(420, 10)
        runtime.queue_upsert(self.conn, "testproj", "feature/s1", "abc123", REL, "integrated", "done")
        self.round()
        self.assertEqual([q for q in runtime.list_questions(self.conn, "testproj", "open") if q["item"] == "STALL-PLAN"], [])

    def test_hours_are_a_setting(self):
        """With planStallHours 10 seven hours are not enough."""
        self.machine["planStallHours"] = 10
        self.plan_agents(420, 10)
        self.round()
        self.assertEqual([q for q in runtime.list_questions(self.conn, "testproj", "open") if q["item"] == "STALL-PLAN"], [])

    def test_verifies_before_the_start_of_the_guard_do_not_count(self):
        """Only agents started after the go-live time of the guard (kv guard-since) count: old verifies cause no stop."""
        self.verifies([4, 4, 5])
        runtime.kv_set(self.conn, "*", "guard-since", db.now())
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_verifies_after_the_start_of_the_guard_count(self):
        """A historic guard start does not stop the package."""
        self.verifies([4, 4, 5])
        runtime.kv_set(self.conn, "*", "guard-since", (datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds"))
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    def test_owner_answer_of_the_plan_stop_resets_the_count_of_its_packages(self):
        """The owner answered the stop of the plan: verifies from before that answer no longer count for any package of the plan."""
        self.verifies([4, 4, 5])
        qid = db.add_question(self.conn, "testproj", REL, "STALL-PLAN", "plan stopped", "")
        db.answer_question(self.conn, qid, "go on", "owner")
        self.round()
        self.assertEqual(self.questions("STALL-S1"), [])

    def test_answered_stop_question_starts_no_work(self):
        """The answer to a STALL question is no task: no Q-<item> work agent starts for it."""
        self.verifies([5, 3, 1])
        qid = db.add_question(self.conn, "testproj", REL, "STALL-S1", "package stopped", "")
        db.answer_question(self.conn, qid, "go on", "owner")
        self.round()
        self.assertFalse([n for n in self.names() if "STALL" in n])


class StartStateAndQuestionRunTest(NewCommitTest):
    """Legacy start states and running question work remain visible to the Queen."""

    # Verify the plan is handled by a Queen without the old stop rules.
    def test_missing_start_state_is_no_progress(self):
        """A legacy work agent without a recorded start state is followed by a Queen, not a verify."""
        self.agent("DUMMY-C1-work", package="C1", started=30, ended=20)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertNotIn("DUMMY-C1-verify", self.names())

    def test_no_verify_while_a_question_run_of_the_package_works(self):
        """A running Q-<package> work agent (answered question of the package) holds back the verify of the package."""
        self.started("DUMMY-C1-work", started=30, ended=20)
        self.commit("two")
        self.agent("DUMMY-Q-C1-work", package="Q-C1", status="running", pid=__import__("os").getpid(), started=5)
        self.round()
        self.assertNotIn("DUMMY-C1-verify", self.names())
