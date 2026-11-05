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
from haivequeen import inbox, loop, routine
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




class CodeRepoUnchangedTest(CodeRepoCase):
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
    def test_codex_queen_loads_the_rules_of_the_code_repo_not_the_workspace(self):
        """A code-repo project: the project rules are the checked-in instruction files of the code repo; an internal workspace file is no rule."""
        from test_loading import git_repo
        git_repo(self.dev, {"AGENTS.md": "CODE-REPO-RULE"})
        (self.repo / "AGENTS.md").write_text("WORKSPACE-INTERNAL", encoding="utf-8")
        text = self.start_agent("codex")
        self.assertIn("CODE-REPO-RULE", text)
        self.assertNotIn("WORKSPACE-INTERNAL", text)


# Verify the default project rule file remains available to Codex Queens.
class CodeRepoCodexUnchangedTest(CodeRepoCase):
    """A project without a separate code repo uses its normal rules."""

    with_code = False

    # Verify the project rule file is named in the Queen prompt.
    def test_codex_queen_loads_the_checked_in_claude_md(self):
        """The Queen prompt contains the CLAUDE.md checked in to a normal workspace repository."""
        from test_loading import git_repo
        git_repo(self.repo, {"CLAUDE.md": "CHECKED-IN-RULE"})
        self.machine["preferredProvider"] = "codex"
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertIn("CHECKED-IN-RULE", self.prompt_of(active[0]["name"]))


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
