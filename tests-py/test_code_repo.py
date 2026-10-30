"""Tests of the project setting `codeRepo`: a project whose git main checkout is not its workspace (plans and rules live in the workspace,
the code in the code repo). Every place that took the workspace as the main checkout uses the code repo, plan paths in prompts are absolute
workspace paths, and a project without `codeRepo` behaves as before."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import isolation
from bzztest import REL, RoundCase, db, runtime
from haivequeen import agent_runner as ar
from haivequeen import brain, inbox, loop, routine
from test_agent_runner import RunnerBase, claude_stream


class CodeRepoCase(RoundCase):
    """Base: the dummy project `testproj` gets a separate code repo `dev` (worktree prefix HQ) when `with_code` is set."""

    with_code = True

    def setUp(self):
        """Create the dummy project; with `with_code` its project.toml names a code repo next to the workspace."""
        super().setUp()
        self.dev = self.base / "code" / "dev"
        self.dev.mkdir(parents=True)
        if self.with_code:
            self.set_code('codeRepo = %s\nworktreePrefix = "HQ"\nintegrationBranch = "v2"\n' % json.dumps(str(self.dev)))

    def set_code(self, text):
        """Append top-level keys to project.toml of the workspace (before any table)."""
        f = self.repo / ".haivequeen" / "project.toml"
        f.write_text(f.read_text(encoding="utf-8") + text, encoding="utf-8")

    def home(self):
        """The folder that is the main checkout of the code: the code repo, else the workspace."""
        return str(self.dev) if self.with_code else str(self.repo)

    def plan_name(self, rel=REL):
        """How prompts name the plan: absolute in the workspace with a code repo, else as it stands."""
        return str(self.repo / rel) if self.with_code else rel

    def triage_row(self):
        """A finished triage agent of the dummy plan that ran in the workspace (the only history of the plan)."""
        self.agent("DUMMY-LOCK-P0-triage", phase="triage", package="LOCK-P0", cwd=str(self.repo), started=30, ended=20)

    def prompt_of(self, name):
        """The prompt file the loop wrote for an agent."""
        return (self.env().run_dir(name) / "prompt.md").read_text(encoding="utf-8")

    def new_agents(self, before):
        """Agent rows of the dummy project that did not exist in `before` (a list of names)."""
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["name"] not in before]


class CodeRepoSettingTest(CodeRepoCase):
    """The setting itself: env.code_repo, worktree folders, plan names, the working folder of a new agent."""

    def test_env_code_repo(self):
        """env.code_repo is the codeRepo of project.toml; without the key it is the workspace."""
        self.assertEqual(self.env().code_repo, self.dev)
        self.assertEqual(self.env().workspace, self.repo)

    def test_new_worktree_next_to_code_repo(self):
        """A new worktree is a sibling of the code repo with the worktree prefix, not of the workspace."""
        self.assertEqual(routine.new_worktree(self.env(), "pilot"), str(self.dev.parent / "HQ-pilot"))

    def test_plan_ref_absolute_with_code_repo(self):
        """With a code repo a plan is named by its absolute workspace path."""
        self.assertEqual(routine.plan_ref(self.env(), REL), str(self.repo / REL))

    def test_pick_cwd_never_the_workspace(self):
        """Only a triage agent in the workspace ran before: the result is the code repo; a worktree in the history still wins."""
        hist = [{"cwd": str(self.repo), "bundle": [], "package": "LOCK-P0"}]
        self.assertEqual(routine.pick_cwd(hist, str(self.dev), "", str(self.repo)), str(self.dev))
        hist.append({"cwd": str(self.wt), "bundle": [], "package": "P1"})
        self.assertEqual(routine.pick_cwd(hist, str(self.dev), "", str(self.repo)), str(self.wt))
        hist.append({"cwd": str(self.repo), "bundle": [], "package": "LOCK-P1"})
        self.assertEqual(routine.pick_cwd(hist, str(self.dev), "P1", str(self.repo)), str(self.wt))

    # Verify that a dry-run snapshot retains separate code and workspace paths.
    def test_snapshot_names_code_repo_and_workspace(self):
        """The dry-run snapshot names the code repo and the workspace separately."""
        out = self.base / "out" / "dry.json"
        self.round(dry_run=True, out_file=str(out))
        snap = json.loads(out.with_suffix(".snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual((snap["repo"], snap["workspace"]), (str(self.dev), str(self.repo)))

    def test_inbox_withdraw_checks_the_code_repo(self):
        """A withdraw order is checked against the git checkout of the code repo, not the workspace."""
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True)
        runtime.queue_upsert(self.conn, "testproj", "fix/a", tip="abc1234", status="blocked", note="x")
        (box / "w.toml").write_text('[[queue]]\nbranch = "fix/a"\naction = "withdraw"\nreason = "bereits integriert"\n', encoding="utf-8")
        seen = []
        with mock.patch.object(inbox, "is_ancestor", lambda repo, tip, ref: seen.append((str(repo), ref)) or True):
            inbox.process_inbox(self.env())
        self.assertEqual(seen, [(str(self.dev), "origin/v2")])


class CodeRepoStartTest(CodeRepoCase):
    """Starts of a project with a code repo get the code repo as main checkout and absolute plan names."""

    def start(self, **extra):
        """Routine start actions of the dummy plan (the plan must exist in the database)."""
        ctx = {"plans": [{"path": REL, "status": "IN_PROGRESS"}], "queue": [], "all_rows": [], "questions": [], "can_start_integrator": False,
               "free": {"claude": 2, "codex": 2, "workers": 4, "int": 0}}
        ctx.update(extra)
        return [a for a in routine.get_routine(self.env(), ctx)["actions"] if a["action"] == "start_agent"]

    def test_work_after_triage_only_starts_in_the_code_repo(self):
        """Only a triage agent ran before: the work agent gets the code repo as cwd and a new worktree next to it, and names the plan absolutely."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.triage_row()
        (act,) = self.start()
        self.assertEqual((act["phase"], act["package"], act["cwd"]), ("work", "P1", self.home()))
        text = act["prompt"]
        self.assertIn("Plan: %s, package P1." % self.plan_name(), text)
        wt = str((self.dev if self.with_code else self.repo).parent / ("HQ" if self.with_code else "wt"))
        self.assertIn(wt, text)
        self.assertNotIn(str(self.repo.parent / "HQ-"), text.replace(wt, ""))

    def test_new_plan_after_the_brain_asked_starts_in_the_code_repo(self):
        """No agent history and the new_plan event is handled: the packages start in the code repo."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        routine.set_handled(self.env(), "newplan:" + REL)
        (act,) = self.start()
        self.assertEqual(act["cwd"], self.home())

    def test_answered_question_after_triage_only(self):
        """An answered question starts in the code repo (not the workspace), in a new worktree, naming the plan absolutely."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.triage_row()
        qid = db.add_question(self.conn, "testproj", REL, "P1", "Which value?", "5")
        db.answer_question(self.conn, qid, "seven", "owner")
        acts = [a for a in self.start(questions=routine.question_rows(self.env())) if a["package"].startswith("Q-")]
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["cwd"], self.home())
        self.assertIn("Plan: %s," % self.plan_name(), acts[0]["prompt"])
        self.assertIn(str((self.dev if self.with_code else self.repo).parent / ("HQ-" if self.with_code else "wt-")), acts[0]["prompt"])

    def test_fix_blocked_after_triage_only(self):
        """A blocked row with a plan whose history is only a triage agent: the fix agent starts in the code repo with a new worktree."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.triage_row()
        rows = [{"status": "blockiert x", "plan": REL, "branch": "fix/a", "tip": "abc1234def", "note": "two checks red"}]
        (act,) = [a for a in self.start(all_rows=rows) if a["package"].startswith("FIX-")]
        self.assertEqual(act["cwd"], self.home())
        self.assertIn("Plan: %s." % self.plan_name(), act["prompt"])
        self.assertIn(str((self.dev if self.with_code else self.repo).parent / ("HQ-fix-fix-a" if self.with_code else "wt-fix-fix-a")), act["prompt"])

    def test_fix_blocked_without_plan_uses_the_code_repo(self):
        """A blocked row without a plan starts in the code repo and names the new worktree next to it."""
        rows = [{"status": "blockiert x", "plan": "keiner", "branch": "fix/b", "tip": "abc1234def", "note": "red"}]
        (act,) = self.start(all_rows=rows)
        self.assertEqual(act["cwd"], self.home())
        self.assertIn(str((self.dev if self.with_code else self.repo).parent / ("HQ-fix-fix-b" if self.with_code else "wt-fix-fix-b")), act["prompt"])

    def test_verify_names_the_plan_absolutely(self):
        """The verify question names the plan by its absolute workspace path."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.agent("DUMMY-P1-work", package="P1", started=30, ended=20, last_line="ok")
        (act,) = self.start()
        self.assertEqual(act["phase"], "verify")
        self.assertIn("Plan %s " % self.plan_name(), act["prompt"])

    def test_follow_up_prompts_name_the_plan_absolutely(self):
        """The follow-up work for open requirements and the restart after a pane failure name the plan absolutely."""
        self.plan("IN_PROGRESS", [{"id": "A1", "title": "first"}, {"id": "A2", "title": "second"}])
        self.agent("TT-A1-work", package="A1", started=30, ended=29, last_line="ok")
        self.agent("TT-A1-verify", package="A1", phase="verify", started=20, ended=19, last_line="ok")
        self.finish("TT-A1-verify", "Nein, teilweise.\nOffene Anforderungen: 3")
        self.agent("TT-A2-work", package="A2", status="failed", started=10, ended=9, last_line="pane never started (no runner process)")
        prompts = {a["package"]: a["prompt"] for a in self.start()}
        self.assertIn("Plan: %s, package A1." % self.plan_name(), prompts["A1"])
        self.assertIn("Plan: %s, package A2." % self.plan_name(), prompts["A2"])

    def test_release_names_the_plan_absolutely(self):
        """The release prompt names the plan absolutely."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.agent("DUMMY-P1-work", package="P1", started=30, ended=29, last_line="ok")
        self.agent("DUMMY-P1-verify", package="P1", phase="verify", started=28, ended=27, last_line="ok")
        self.finish("DUMMY-P1-verify", "Offene Anforderungen: 0")
        self.agent("DUMMY-ALL-verify", package="ALL", phase="verify", started=26, ended=25, last_line="ok")
        self.finish("DUMMY-ALL-verify", "Offene Anforderungen: 0")
        (act,) = self.start()
        self.assertEqual(act["phase"], "release")
        self.assertIn("of plan %s " % self.plan_name(), act["prompt"])

    def test_lock_question_gets_the_plan(self):
        """The lockQuestion template can name the plan; it is the absolute workspace path with a code repo."""
        f = self.repo / ".haivequeen" / "project.toml"
        f.write_text(f.read_text(encoding="utf-8") + '\n[prompts]\nlockQuestion = "{tag} {pkg} in {plan}"\n', encoding="utf-8")
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        loop._do_lock({"env": self.env(), "report": [], "dry": False}, {"plan": REL, "package": "P1", "agent": "x", "text": "open"})
        self.assertEqual(runtime.kv_get(self.conn, "testproj", "lock:%s|P1" % REL), "LOCK-P1 P1 in %s" % self.plan_name())


class CodeRepoUnchangedTest(CodeRepoStartTest):
    """The same flows in a project without `codeRepo` give the plain plan paths and the workspace as main checkout, as before."""

    with_code = False

    def test_env_code_repo(self):
        """Without the key the code repo is the workspace."""
        self.assertEqual(self.env().code_repo, self.repo)

    def test_inbox_withdraw_checks_the_code_repo(self):
        """Without codeRepo and repo the withdraw order is checked in the workspace."""
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True)
        runtime.queue_upsert(self.conn, "testproj", "fix/a", tip="abc1234", status="blocked", note="x")
        (box / "w.toml").write_text('[[queue]]\nbranch = "fix/a"\naction = "withdraw"\nreason = "bereits integriert"\n', encoding="utf-8")
        seen = []
        with mock.patch.object(inbox, "is_ancestor", lambda repo, tip, ref: seen.append(str(repo)) or True):
            inbox.process_inbox(self.env())
        self.assertEqual(seen, [str(self.repo)])


# Verify Queen prompts preserve project rules and integration configuration.
class RunnerAndHeaderTest(CodeRepoCase):
    """Queen headers and project integration inputs."""

    # Start one Queen on the selected provider and read her prompt.
    def start_agent(self, protocol):
        """Return the prompt of the Queen started for the plan."""
        self.machine["preferredProvider"] = protocol
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        return self.prompt_of(active[0]["name"])

    # Verify a Queen receives the plan and project rules.
    def test_claude_header_without_build_flow_doc(self):
        """The Queen prompt identifies her role and absolute plan path."""
        text = self.start_agent("claude")
        self.assertIn("Queen of this Hive", text)
        self.assertIn(self.plan_name(), text)

    # Verify configured integration instructions reach the Queen.
    def test_claude_header_names_build_flow_doc(self):
        """A configured build flow document is included in the Queen task."""
        self.set_code('buildFlowDoc = "FLOW.md"\n')
        self.assertIn("FLOW.md", self.start_agent("claude"))

    # Verify the workspace rule path reaches a Codex Queen.
    def test_codex_header_names_workspace_rules(self):
        """A code-repo project identifies its workspace rules."""
        text = self.start_agent("codex")
        self.assertIn(str(self.repo / "AGENTS.md"), text)


# Verify the default project rule file remains available to Codex Queens.
class CodeRepoCodexUnchangedTest(CodeRepoCase):
    """A project without a separate code repo uses its normal rules."""

    with_code = False

    # Verify the project rule file is named in the Queen prompt.
    def test_codex_header_names_claude_md(self):
        """The Queen prompt identifies CLAUDE.md for a normal workspace."""
        self.machine["preferredProvider"] = "codex"
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertIn("CLAUDE.md", self.prompt_of(active[0]["name"]))


class RunnerVariablesTest(RunnerBase):
    """A real runner start: the provider arguments {repo} and {workspace}."""

    def run_with_args(self, project_toml):
        """Run a fake Claude agent whose arguments are '--add-dir {repo} --add-dir {workspace}'; return the recorded argv."""
        self.write_config()
        cfg = self.base / "cfg" / "config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("'{rules}']", "'{rules}', '--add-dir', '{repo}', '--add-dir', '{workspace}']", 1), encoding="utf-8")
        (self.ws / ".haivequeen").mkdir(exist_ok=True)
        (self.ws / ".haivequeen" / "project.toml").write_text(project_toml, encoding="utf-8")
        self.prepare("V-1")
        r = self.run_runner("V-1", "claude", claude_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.record()["argv"]

    def test_repo_is_the_code_repo_and_workspace_the_workspace(self):
        """With codeRepo, {repo} is the code repo and {workspace} the workspace."""
        dev = self.base / "code" / "dev"
        argv = self.run_with_args("codeRepo = '%s'" % dev)
        self.assertEqual(argv[-4:], ["--add-dir", str(dev), "--add-dir", str(self.ws)])

    def test_without_code_repo_both_are_the_workspace(self):
        """Without codeRepo {repo} stays the workspace (as before) and {workspace} is the same folder."""
        argv = self.run_with_args("")
        self.assertEqual(argv[-4:], ["--add-dir", str(self.ws), "--add-dir", str(self.ws)])


class IsolationLiveDataTest(unittest.TestCase):
    """The isolation check leaves out everything a running loop (of any project) or a self-update writes."""

    def test_live_files_are_not_counted(self):
        """logs/<any project>/..., the STOP flag and the self-update lock are left out; other files are counted."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for rel in ("logs/ODP/a.log", "logs/HaiveQueen/b.log", "STOP", "self-update.lock", "loop.lock", "runs/x/y.txt"):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_text("x", encoding="utf-8")
            for rel in ("logs/c.log", "config.toml", "other/z.txt"):
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_text("x", encoding="utf-8")
            counted = sorted(Path(p).relative_to(base).as_posix() for p in isolation.snapshot_real([base]))
        self.assertEqual(counted, ["config.toml", "logs/c.log", "other/z.txt"])


class PhaseRulesExceptionTest(unittest.TestCase):
    """The rule file of the integrate phase carries the exception section of a workspace AGENTS.md."""

    def test_integrate_rules_contain_the_exception_section(self):
        """phaseRules for integrate copy the exception section of AGENTS.md; the work phase does not get it."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / "AGENTS.md").write_text("## 1. Hard rules\nnever push\n\n## Integrate phase (exception, integrator only)\nmay push v2\n", encoding="utf-8")
            pcfg = {"ruleSources": {"main": "AGENTS.md"},
                    "phaseRules": {"work": {"main": ["## 1. Hard rules"]},
                                   "integrate": {"main": ["## 1. Hard rules", "## Integrate phase (exception"]}}}
            out = Path(tmp) / "rules.md"
            self.assertEqual(ar.write_phase_rules(pcfg, str(ws), "integrate", out), 2)
            self.assertIn("may push v2", out.read_text(encoding="utf-8"))
            ar.write_phase_rules(pcfg, str(ws), "work", out)
            self.assertNotIn("may push v2", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
