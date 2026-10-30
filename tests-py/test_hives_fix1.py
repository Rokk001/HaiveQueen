"""Tests of the live defects found after the first resume: a Queen never waits on an owner question for branch and worktree (A3, A13), every
non-start of a Hive and every round is logged with its reason, migrated project files that still say `repo` name the code repo, and the
Beekeeper header shows nothing about a language model (D1, D18)."""
import tempfile
import unittest
from pathlib import Path

from bzztest import REL, RoundCase
from haivequeen import bees, config, hives, loop, runtime


class PlanBranchTests(RoundCase):
    """The branch and worktree of a plan come from the plan and its state file, never from the owner."""

    def add_plan_text(self, text):
        """Append text to the dummy plan."""
        with (self.repo / REL).open("a", encoding="utf-8") as plan:
            plan.write(text)

    def test_git_line_of_the_plan_names_the_branch(self):
        """A plan that says '**Git:** branch `X` from `origin/...`' names branch X (the form of the INGAME-QS plans)."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.add_plan_text("\n- **Git:** branch `fix/ingame-qs-5` from `origin/integration/all`. Push normally.\n")
        self.assertEqual(bees.plan_worktree(self.env(), REL)[0], "fix/ingame-qs-5")

    def test_state_file_names_the_branch_when_the_plan_does_not(self):
        """Without a branch in the plan, the 'Plan: X; branch Y; baseline Z' line of the state file next to it names the branch."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        (self.repo / "docs/plans/dummy/DUMMY-STATE.md").write_text("# State\n\nPlan: DUMMY-PLAN.md; branch fix/dummy; baseline origin/integration/all (3af31cbe2).\n", encoding="utf-8")
        self.assertEqual(bees.plan_worktree(self.env(), REL)[0], "fix/dummy")

    def test_answered_question_still_names_the_branch(self):
        """Old 'worktree' questions that were answered stay readable."""
        from haivequeen import db
        self.plan("READY", [{"id": "P1", "title": "First"}])
        qid = db.add_question(self.conn, "testproj", REL, "worktree", "Which branch and worktree should this plan use?", "")
        db.answer_question(self.conn, qid, "feature/test\n" + str(self.wt), "owner")
        self.assertEqual(bees.plan_worktree(self.env(), REL), ("feature/test", self.wt))


class NoStartReasonTests(RoundCase):
    """Every Hive that does not start gets a reason in the log, and no technical question goes to the owner."""

    def test_unknown_branch_asks_nobody_and_logs_the_reason(self):
        """A plan without a branch in plan or state file stays waiting, creates no owner question and says why."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertEqual(runtime.list_questions(self.conn, "testproj", plan=REL, item="worktree"), [])
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "waiting")
        self.assertFalse(any(a["phase"] == "queen" for a in runtime.list_agents(self.conn, "testproj", "all")))
        self.assertIn("DUMMY-PLAN not started: no branch or worktree named in the plan or its state file", self.log())

    def test_worktree_that_cannot_be_prepared_is_logged(self):
        """A named branch whose worktree cannot be prepared stays waiting and the log names branch, worktree and plan."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        occupied = self.base / "occupied"
        occupied.mkdir()
        with (self.repo / REL).open("a", encoding="utf-8") as plan:
            plan.write("\n- Branch `feat/x` in worktree `%s`.\n" % occupied)
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "waiting")
        self.assertIn("DUMMY-PLAN not started: worktree for branch feat/x could not be prepared", self.log())

    def test_hive_limit_is_logged(self):
        """With the Hive limit reached, the waiting Hive says so."""
        self.machine["hives"] = 0
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        self.assertIn("DUMMY-PLAN not started: Hive limit 0 reached", self.log())

    def test_low_ram_is_logged(self):
        """Too little free RAM is a logged reason, not a silent skip."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(free_ram=lambda: 1.0)
        self.assertIn("round skipped (free RAM 1.00 GB is below the minimum 2.00 GB)", self.log())

    def test_every_round_is_logged(self):
        """A round that starts nothing still leaves a line, so a standing loop is told from a working one."""
        self.plan("DONE", [{"id": "P1", "title": "First"}])
        self.round()
        self.assertIn("round done: 0 Queen(s) started", self.log())


class LegacyRepoKeyTests(unittest.TestCase):
    """Project files written by the v1 migration name the code repo `repo`."""

    def load(self, text):
        """Load a project.toml with this text."""
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".haivequeen").mkdir()
            (Path(directory) / ".haivequeen" / "project.toml").write_text(text, encoding="utf-8")
            return config.load_project(directory)

    def test_repo_is_read_as_code_repo(self):
        """`repo` without `codeRepo` is the code repo."""
        self.assertEqual(self.load('repo = "C:\\\\game"\n')["codeRepo"], "C:\\game")

    def test_code_repo_wins_over_repo(self):
        """An explicit `codeRepo` is never replaced by `repo`."""
        self.assertEqual(self.load('repo = "a"\ncodeRepo = "b"\n')["codeRepo"], "b")

    def test_no_repo_keeps_the_workspace(self):
        """A project with neither key has no code repo setting."""
        self.assertNotIn("codeRepo", self.load('name = "x"\n'))


class HeaderTests(unittest.TestCase):
    """The Beekeeper header makes no claim about a language model: the Beekeeper calls none (D1, D18)."""

    def text(self, ui):
        """All header lines as one text."""
        return "\n".join(line for line, _ in loop.head_lines(100, ui))

    def test_header_has_no_model_or_brain_text(self):
        """No model name, no 'Brain calls', no 'brain' wording and no call statistics."""
        text = self.text({"busy": True, "projects": ["a", "b"]})
        for word in ("Claude", "Brain", "brain", "Decides", "Last call", "Today", "Total input"):
            self.assertNotIn(word, text)

    def test_header_shows_state_and_projects(self):
        """The header still shows the Beekeeper, its state and its projects."""
        text = self.text({"busy": True, "projects": ["a", "b"]})
        self.assertIn("Beekeeper | round running", text)
        self.assertIn("Projects: a, b", text)
        self.assertIn("PAUSED", self.text({"run_state": "PAUSED"}))


if __name__ == "__main__":
    unittest.main()
