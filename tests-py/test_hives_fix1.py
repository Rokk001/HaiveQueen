"""Tests of the live defects found after the first resume: a Queen never waits on an owner question for branch and worktree (A3, A13), every
non-start of a Hive and every round is logged with its reason, migrated project files that still say `repo` name the code repo, and the
Beekeeper header shows nothing about a language model (D1, D18)."""
import sys
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


class ProcessBeePaneTests(RoundCase):
    """A Queen gets her own pane; a Bee that runs as its own process gets none and shows only as a line in its Hive (A3, D3)."""

    def start(self, phase):
        """Start one agent of this phase through the real start path with the pane and the headless launch recorded; return the two call lists."""
        import os
        from unittest import mock
        from haivequeen import scheduler as sch
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        env = self.env()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        panes, headless = [], []
        action = {"name": "Ada", "type": "claude", "phase": phase, "plan": REL, "package": "P1" if phase == "bee" else "", "cwd": str(self.wt), "prompt": "task", "class": "medium"}
        state = {"names": [], "busy": [], "plan_status": {sch.norm_plan(REL): "IN_PROGRESS"}, "by_path": {sch.norm_plan(REL): {"path": REL}}}
        ctx = {"env": env, "state": state, "pool": loop.Pool({"claude": 5}, 0), "report": [], "dry": False, "terminal": None, "settle": 0, "poll": 0, "tries": 1}
        with mock.patch.object(loop, "launch_agent", lambda *a, **k: panes.append(a[3]["name"])),                 mock.patch.object(loop, "launch_headless", lambda *a, **k: headless.append(a[1]["name"]), create=True):
            loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, "claude"))
        return panes, headless

    def test_queen_gets_a_pane(self):
        """The Queen is opened in a pane of the HaiveQueen window."""
        self.assertEqual(self.start("queen"), (["Ada"], []))

    def test_process_bee_gets_no_pane(self):
        """A Bee process is started without a pane."""
        self.assertEqual(self.start("bee"), ([], ["Ada"]))


class DeadBrainCodeTests(RoundCase):
    """The Beekeeper calls no language model, so the brain and triage code of the old round is gone (D18, A10)."""

    def test_old_round_code_is_gone(self):
        """No old per-project round, no triage start, no brain module."""
        self.assertFalse(hasattr(loop, "_project_round"))
        self.assertFalse(hasattr(loop, "_start_triage"))
        self.assertFalse((Path(loop.__file__).parent / "brain.py").exists())

    def test_dashboard_shows_no_brain_text(self):
        """Even a stored 'brain' loop state shows no 'brain call running' and the dashboard has no brain statistic."""
        from datetime import datetime
        from haivequeen import dashboard
        runtime.kv_set(self.conn, "*", "loop-state", '{"status": "brain", "since": "2026-10-30T15:00:00"}')
        sysinfo = {"cores": 4, "free_mb": 4000, "total_mb": 8000, "host": "test", "compiler_count": None}
        text = dashboard.render(self.conn, width=100, height=60, now=datetime.now(), cfg={}, sysinfo=sysinfo, color=False)
        self.assertNotIn("brain", text.lower())


class QueenStartTests(RoundCase):
    """Queens start in a pane with a short female name; the Beekeeper is never called Queen (A2, A3, D1)."""

    def test_runner_command_has_no_empty_argument(self):
        """wt drops an empty argument such as --package "", so the runner died at once; the command never holds one."""
        spec = {"name": "Ada", "provider": "claude", "protocol": "claude", "plan": REL, "package": "", "phase": "queen", "cwd": str(self.wt), "prompt_file": "p.md"}
        argv = loop.runner_argv(self.env(), spec)
        self.assertNotIn("", argv)
        self.assertNotIn("--package", argv)

    def test_queen_is_named_by_her_female_name(self):
        """The agent row of a Queen carries her first name from the name list, not Queen-<n>."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        self.assertIn(queen["name"], loop.AGENT_NAMES)
        self.assertEqual(queen["name"], queen["person"])

    def test_second_queen_gets_another_name(self):
        """Two Queens never share a name."""
        self.plan("READY", [{"id": "P1", "title": "First"}], rel="docs/plans/dummy/DUMMY2-PLAN.md")
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        names = [a["name"] for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(names), len(set(names)))

    def test_loop_start_names_the_beekeeper(self):
        """The loop start line says Beekeeper, not Queen."""
        loop.run_loop(self.conn, self.machine, rounds=1, sleep=lambda s: None, round_kwargs={"free_ram": lambda: 16.0, "settle": 0})
        text = (runtime.mirror_dir("testproj") / runtime.MIRRORS["events"]).read_text(encoding="utf-8")
        self.assertIn("Beekeeper started (round every", text)
        self.assertNotIn("Queen started", text)

    def test_chief_pane_is_found_as_the_loop_pane(self):
        """The title of the loop pane is recognised as the loop pane (it keeps the QUEEN word until the update worker knows the new one)."""
        self.assertTrue(loop.is_beekeeper_title(loop.queen_pane_title("m", True)))


class RunnerCrashTests(RoundCase):
    """A runner that crashes before its first row write leaves its reason behind instead of vanishing with its pane."""

    def test_crash_is_written_to_the_run_folder_and_the_row(self):
        """The traceback goes to runner-error.txt and the agent row is failed with the reason."""
        from unittest import mock
        from haivequeen import agent_runner
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        self.agent("Ada", phase="queen", status="starting")
        runs = self.base / "data" / "runs" / "testproj"
        argv = ["--project", "testproj", "--name", "Ada", "--provider", "claude", "--cwd", str(self.wt), "--prompt-file", "p.md", "--runs-dir", str(runs)]
        with mock.patch.object(agent_runner, "run_agent", side_effect=ValueError("boom")):
            self.assertNotEqual(agent_runner.main(argv), 0)
        self.assertIn("boom", (runs / "Ada" / "runner-error.txt").read_text(encoding="utf-8"))
        row = runtime.get_agent(self.conn, "testproj", "Ada")
        self.assertEqual(row["status"], "failed")
        self.assertIn("boom", row["last_line"])


class BeekeeperPaneTests(RoundCase):
    """The pane of the loop is found by its Beekeeper title (the self-update counts it), and panes opened with the old QUEEN title still count."""

    def chief_ids(self, title):
        """Ids the self-update finds for a window whose first pane has this title."""
        from unittest import mock
        from haivequeen import selfupdate
        from haivequeen.terminal.headless import Headless
        term = Headless(self.base / "panes")
        term.open_window("HaiveQueen", title, [sys.executable, "-c", "import time;time.sleep(30)"], name="chief")
        try:
            with mock.patch("haivequeen.terminal.get_terminal", return_value=term), mock.patch.object(config, "load_config", return_value=self.machine):
                return selfupdate.queen_panes()
        finally:
            for p in term.list_panes("HaiveQueen"):
                term.close_pane(p.id)

    def test_new_title_is_found(self):
        """The Beekeeper pane counts as the one loop pane."""
        self.assertEqual(len(self.chief_ids(loop.queen_pane_title("m", True))), 1)

    def test_old_title_is_still_found(self):
        """A pane opened before the update still carries the QUEEN title and counts."""
        from haivequeen.terminal import pane_title
        self.assertEqual(len(self.chief_ids(pane_title("all projects", "QUEEN", "Queen", "m", True))), 1)


if __name__ == "__main__":
    unittest.main()
