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

    # A deferred start releases its Hive claim for the next round.
    def test_deferred_queen_start_releases_hive_claim(self):
        """A Queen that did not start leaves its Hive waiting."""
        from unittest import mock
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        with mock.patch.object(loop, '_do_start', return_value=0):
            self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')

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
        self.assertIn("Apiaries: a, b", text)
        self.assertIn("PAUSED", self.text({"run_state": "PAUSED"}))


class ProcessBeePaneTests(RoundCase):
    """A Queen and each process Bee get a pane in their Hive."""

    # Defer a Bee before creating its agent record if its Hive pane is absent.
    def test_bee_waits_before_creating_agent_without_hive_pane(self):
        """A missing Hive pane does not leave a starting Bee."""
        import os
        from unittest import mock
        from haivequeen import scheduler as sch
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        os.environ.pop('BZZ_TEST_NOSTART', None)
        action = {'name': 'Ada', 'type': 'claude', 'phase': 'bee', 'plan': REL, 'package': 'P1', 'cwd': str(self.wt), 'prompt': 'task', 'class': 'medium'}
        state = {'names': [], 'busy': [], 'plan_status': {sch.norm_plan(REL): 'IN_PROGRESS'}, 'by_path': {sch.norm_plan(REL): {'path': REL}}}
        term = mock.Mock()
        term.list_panes.return_value = []
        ctx = {'env': env, 'state': state, 'pool': loop.Pool({'claude': 5}, 0), 'report': [], 'dry': False, 'terminal': term, 'settle': 0, 'poll': 0, 'tries': 1}
        self.assertEqual(loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, 'claude')), 0)
        self.assertIsNone(runtime.get_agent(self.conn, env.name, 'Ada'))

    # Defer before creating an agent when its Hive row cannot yet be opened.
    def test_queen_waits_before_creating_agent_for_blocked_row(self):
        """A deferred row does not leave a starting Queen behind."""
        import os
        from unittest import mock
        from haivequeen import scheduler as sch
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        os.environ.pop('BZZ_TEST_NOSTART', None)
        action = {'name': 'Ada', 'type': 'claude', 'phase': 'queen', 'plan': REL, 'package': '', 'cwd': str(self.wt), 'prompt': 'task', 'class': 'medium'}
        state = {'names': [], 'busy': [], 'plan_status': {sch.norm_plan(REL): 'IN_PROGRESS'}, 'by_path': {sch.norm_plan(REL): {'path': REL}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool({'claude': 5}, 0), 'report': [], 'dry': False, 'terminal': mock.Mock(), 'settle': 0, 'poll': 0, 'tries': 1}
        with mock.patch.object(loop, 'hive_row_waits_for_bees', return_value=True, create=True):
            self.assertEqual(loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, 'claude')), 0)
        self.assertIsNone(runtime.get_agent(self.conn, env.name, 'Ada'))

    # Defer a Queen while its Apiary cannot yet be opened at full width.
    def test_queen_waits_before_creating_agent_for_blocked_apiary(self):
        """A deferred Apiary leaves no starting Queen."""
        import os
        from unittest import mock
        from haivequeen import scheduler as sch
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        env = self.env()
        os.environ.pop('BZZ_TEST_NOSTART', None)
        action = {'name': 'Ada', 'type': 'claude', 'phase': 'queen', 'plan': REL, 'package': '', 'cwd': str(self.wt), 'prompt': 'task', 'class': 'medium'}
        state = {'names': [], 'busy': [], 'plan_status': {sch.norm_plan(REL): 'IN_PROGRESS'}, 'by_path': {sch.norm_plan(REL): {'path': REL}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool({'claude': 5}, 0), 'report': [], 'dry': False, 'terminal': mock.Mock(), 'settle': 0, 'poll': 0, 'tries': 1}
        with mock.patch.object(loop, 'hive_row_waits_for_bees', return_value=False), mock.patch.object(loop, 'apiary_waits_for_bees', return_value=True, create=True):
            self.assertEqual(loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, 'claude')), 0)
        self.assertIsNone(runtime.get_agent(self.conn, env.name, 'Ada'))

    def start(self, phase):
        """Start one agent of this phase through the real start path with the pane, the Queen hand-over and the headless launch recorded; return the two call lists
        (a Queen is handed to the sub-terminal of her Hive: see self.queens)."""
        import os
        from unittest import mock
        from haivequeen import scheduler as sch
        from haivequeen.terminal.base import PaneInfo
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        env = self.env()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        panes, headless = [], []
        self.queens = []
        action = {"name": "Ada", "type": "claude", "phase": phase, "plan": REL, "package": "P1" if phase == "bee" else "", "cwd": str(self.wt), "prompt": "task", "class": "medium"}
        state = {"names": [], "busy": [], "plan_status": {sch.norm_plan(REL): "IN_PROGRESS"}, "by_path": {sch.norm_plan(REL): {"path": REL}}}
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', loop.hive_pane_title(env.name, REL, loop.is_test_runtime()), 1, True)]
        ctx = {"env": env, "state": state, "pool": loop.Pool({"claude": 5}, 0), "report": [], "dry": False, "terminal": term, "settle": 0, "poll": 0, "tries": 1}
        with mock.patch.object(loop, "launch_agent", lambda *a, **k: panes.append(a[3]["name"])), mock.patch.object(loop, "start_queen", lambda *a, **k: self.queens.append(a[3]["name"])), mock.patch.object(loop, "launch_headless", lambda *a, **k: headless.append(a[1]["name"]), create=True), mock.patch.object(loop, "start_bee_pane", lambda *a, **k: panes.append(a[3]["name"]), create=True):
            loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, "claude"))
        return panes, headless

    def test_queen_gets_a_pane(self):
        """The Queen gets no pane of her own: she is handed to the sub-terminal of her Hive."""
        self.assertEqual(self.start("queen"), ([], []))
        self.assertEqual(self.queens, ["Ada"])

    # Prepare block heights after recording the starting Queen and before starting its pane.
    def test_queen_start_prepares_blocks_before_pane(self):
        """The scheduler prepares the first Queen's block before opening her pane."""
        import os
        from unittest import mock
        from haivequeen import hives, scheduler as sch
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        env = self.env()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        action = {"name": "Ada", "type": "claude", "phase": "queen", "plan": REL, "package": "", "cwd": str(self.wt), "prompt": "task", "class": "medium"}
        state = {"names": [], "busy": [], "plan_status": {sch.norm_plan(REL): "IN_PROGRESS"}, "by_path": {sch.norm_plan(REL): {"path": REL}}}
        term = mock.Mock()
        term.list_panes.return_value = []
        ctx = {"env": env, "envs": [env], "state": state, "pool": loop.Pool({"claude": 5}, 0), "report": [], "dry": False, "terminal": term, "settle": 0, "poll": 0, "tries": 1}
        steps = []
        # Record the Queen state seen by block preparation.
        def prepare(*args):
            """Observe the recorded Queen before the pane opens."""
            steps.append(runtime.get_agent(self.conn, env.name, 'Ada')['status'])
        with mock.patch.object(hives, 'prepare_blocks_for_queen', side_effect=prepare), mock.patch.object(loop, 'start_queen', side_effect=lambda *args: steps.append('pane')):
            loop._do_start(ctx, action, forced_pick=sch.get_provider(env.providers, 'claude'))
        self.assertEqual(steps, ['starting', 'pane'])

    def test_process_bee_gets_a_pane(self):
        """A Bee process starts in its own Hive pane."""
        self.assertEqual(self.start("bee"), (["Ada"], []))

    # Check the Bee split target and its agent record.
    def test_bee_split_keeps_room_for_queen_head(self):
        """The first Bee uses only the width left after the Queen's head."""
        from unittest import mock
        from haivequeen.terminal.base import PaneInfo
        env = self.env()
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        self.agent('Cleo', phase='queen', status='running', plan_path=REL, pane_id='window.1', paneCols=80)
        spec = {'name': 'Ada', 'person': 'Ada', 'plan': REL, 'model_long': 'model'}
        lines = [[('', '')], [('│ ', ''), ('Q' * 56 + ' ' * 964, ''), (' │', '')], [('│ ', ''), ('Q' + ' ' * 1019, ''), (' │', '')]]
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']), mock.patch('haivequeen.hivepane.frame_lines', return_value=lines):
            loop.start_bee_pane(env, term, 'window', spec)
        self.assertEqual(term.split_pane.call_args.args[5], 0.25)

    # Give the Bee its name when both full headers cannot fit.
    def test_bee_split_preserves_queen_space_when_crowded(self):
        """The Bee takes only the width needed for its label in a crowded row."""
        from unittest import mock
        from haivequeen.terminal.base import PaneInfo
        env = self.env()
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        self.agent('Cleo', phase='queen', status='running', plan_path=REL, pane_id='window.1', paneCols=80)
        spec = {'name': 'Ada', 'person': 'Ada', 'plan': REL, 'model_long': 'model'}
        lines = [[('', '')], [('│ ', ''), ('Q' * 76 + ' ' * 944, ''), (' │', '')], [('│ ', ''), ('Q' + ' ' * 1019, ''), (' │', '')]]
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']), mock.patch('haivequeen.hivepane.frame_lines', return_value=lines):
            loop.start_bee_pane(env, term, 'window', spec)
        self.assertEqual(term.split_pane.call_args.args[5], len('Bee: ADA') / 80)

    # Check the Bee split target and its agent record.
    def test_bee_splits_its_own_hive(self):
        """The first process Bee starts to the right of its Hive's Queen."""
        from unittest import mock
        from haivequeen.terminal.base import PaneInfo
        env = self.env()
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True)]
        term.split_pane.return_value = 'window.2'
        spec = {'name': 'Ada', 'person': 'Ada', 'plan': REL, 'model_long': 'model'}
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']), mock.patch.object(env, 'update_agent') as update:
            self.assertEqual(loop.start_bee_pane(env, term, 'window', spec), 'window.2')
        self.assertEqual(term.split_pane.call_args.args[4:7], ('horizontal', 0.5, 'window.1'))
        update.assert_called_once_with('Ada', {'pane_id': 'window.2', 'paneClosed': False})

    # Check that later Bees stay in their own Hive row.
    def test_next_bee_splits_the_previous_bee(self):
        """A later Bee opens to the right of the preceding Bee of the same Hive."""
        from unittest import mock
        from haivequeen.terminal.base import PaneInfo
        env = self.env()
        title = loop.hive_pane_title(env.name, REL, loop.is_test_runtime())
        term = mock.Mock()
        term.list_panes.return_value = [PaneInfo('window.1', title, 1, True), PaneInfo('window.2', 'Bee', 2, True)]
        term.split_pane.return_value = 'window.3'
        spec = {'name': 'Bea', 'person': 'Bea', 'plan': REL, 'model_long': 'model'}
        agents = [{'phase': 'bee', 'plan_path': REL, 'pane_id': 'window.2'}]
        with mock.patch.object(loop, 'runner_argv', return_value=['runner']), mock.patch.object(env, 'update_agent'), mock.patch.object(runtime, 'list_agents', return_value=agents):
            loop.start_bee_pane(env, term, 'window', spec)
        self.assertEqual(term.split_pane.call_args.args[4:7], ('horizontal', 0.5, 'window.2'))


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
        """The title of the loop pane is recognised as the loop pane (the BEEKEEPER title; the old QUEEN title is accepted as well)."""
        self.assertTrue(loop.is_beekeeper_title(loop.queen_pane_title("m", True)))
        self.assertIn("BEEKEEPER", loop.queen_pane_title("m", True))
        self.assertNotIn("QUEEN", loop.queen_pane_title("m", True))


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


class StallQuestionTests(RoundCase):
    """Owner questions of the removed stop rules (STALL-*) never block a Hive (E10)."""

    def finish(self, name, final="", status="done", project="testproj"):
        """Simulate a finished runner, including its process exit."""
        super().finish(name, final=final, status=status, project=project)
        runtime.upsert_agent(self.conn, project, name, {"pid": None})

    def start_queen(self):
        """Start the Queen of the dummy plan and return her agent row."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")][0]

    def test_only_stall_question_does_not_make_the_hive_wait(self):
        """A Queen that ends with WAITING_FOR_OWNER while only a STALL question is open leaves the Hive waiting, not in 'answer'."""
        from haivequeen import db
        first = self.start_queen()
        db.add_question(self.conn, "testproj", REL, "STALL-P1", "package stopped", "")
        self.finish(first["name"], final="WAITING_FOR_OWNER")
        self.round()
        self.assertNotEqual(hives.get(self.conn, "testproj", REL)["state"], "answer")

    def test_stall_question_is_not_in_the_wait_list(self):
        """With a real and a STALL question open, the Hive waits only for the real one."""
        import json
        from haivequeen import db
        first = self.start_queen()
        real = db.add_question(self.conn, "testproj", REL, "P1", "Which?", "A")
        db.add_question(self.conn, "testproj", REL, "STALL-P1", "package stopped", "")
        self.finish(first["name"], final="WAITING_FOR_OWNER")
        self.round()
        self.assertEqual(json.loads(hives.get(self.conn, "testproj", REL)["questions_json"]), [real])


class ApiaryWordTests(RoundCase):
    """F11: CLI messages call a project an Apiary."""

    def test_all_project_option_help_calls_it_apiary(self):
        """Every CLI project option is described to the user as an Apiary."""
        import argparse
        from haivequeen import cli
        def visit(parser):
            """Check this parser and each nested command."""
            for action in parser._actions:
                if '--project' in action.option_strings:
                    self.assertEqual(action.metavar, 'APIARY')
                    self.assertIn('Apiary', action.help or '')
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        visit(child)
        visit(cli.build_parser())

    def test_legacy_import_missing_apiary_error(self):
        """The legacy import error names the missing Apiary."""
        from haivequeen import legacy
        with self.assertRaisesRegex(Exception, 'Apiary is required'):
            legacy.import_legacy(self.conn, '')

    def test_pane_and_runner_help_calls_project_apiary(self):
        """Standalone pane and runner help describe the Apiary option."""
        import subprocess
        import sys
        for module in ('agent_runner', 'hivepane', 'header'):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, '-m', 'haivequeen.' + module, '--help'], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0)
                self.assertIn('--project APIARY', result.stdout)
                self.assertIn('Apiary name', result.stdout)

    def test_display_log_and_cli_help_use_apiary_words(self):
        """Scan tool-written display, round log and CLI help for the retired terminology."""
        import argparse
        import re
        from haivequeen import cli, dashboard
        self.round()
        texts = [self.log(), dashboard.render(self.conn, width=120, height=100, cfg={},
                 sysinfo={'host': 'test', 'cores': 1, 'free_mb': 1, 'total_mb': 1, 'compiler_count': 0}, color=False)]
        def collect(parser):
            """Collect help from every command."""
            texts.append(parser.format_help())
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in action.choices.values():
                        collect(child)
        collect(cli.build_parser())
        for text in texts:
            clean = re.sub(r'--project\b|\bproject\.toml\b', '', text, flags=re.I)
            self.assertNotRegex(clean, r'(?i)\b(?:projects?|projekt)\b')

    def test_unknown_apiary_message(self):
        """A Hive whose Apiary is not configured is reported as an unknown Apiary."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        from haivequeen import cli
        with mock.patch.object(cli, "_resolve_hive", return_value={"project": "ghost", "plan_path": "x"}), contextlib.redirect_stdout(io.StringIO()) as buf:
            code = cli.cmd_requeen(argparse.Namespace(hive="x", provider=None), self.conn)
        self.assertEqual(code, 1)
        self.assertIn("Apiary", buf.getvalue())
        self.assertNotIn("project not found", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
