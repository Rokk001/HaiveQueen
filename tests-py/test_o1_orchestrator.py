"""O1: the Orchestrator integrates a plan after an accepted `bzz ready` (Merge, Check, Build, Push, Deploy, Close), a fix agent repairs a red step,
the owner check names commit, steps and the fix agent report, a rejection starts a fix agent and the whole pipeline again (no Queen)."""
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

from bzztest import REL, RoundCase, write_plan
from haivequeen import bees, cli, db, hives, integration, owner, orchestrator, plans, runtime

OTHER = "docs/plans/other/OTHER-PLAN.md"


def sh(*args, cwd=None, check=True):
    """Run git (or another program) and return its output."""
    return subprocess.run(list(args), cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


class OrchCase(RoundCase):
    """A real remote (bare repository), the project repository with origin and one plan worktree on its own branch."""

    check = ""
    build = ""
    deploy = ""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, orchestrator, "ASYNC", None)
        self.remote = self.base / "remote.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".haivequeen/\ndocs/\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "-u", "origin", "main")
        self.order = self.base / "order.txt"
        self.project_toml()
        self.pwt = self.make_worktree("plan-br", "planwt")

    def project_toml(self, extra=""):
        """Write the project settings with the pipeline commands of this test."""
        lines = ""
        for key, value in (("compileCheck", self.check), ("buildCommand", self.build), ("deployCommand", self.deploy)):
            if value:
                lines += "%s = %s\n" % (key, json.dumps(value))
        (self.repo / ".haivequeen" / "project.toml").write_text('name = "testproj"\nplanGlobs = ["docs/plans/**/*.md"]\ndeleteFinishedWorktrees = false\n' + lines + extra, encoding="utf-8")

    def git(self, *args, cwd=None):
        return sh("git", "-C", str(cwd or self.repo), *args)

    def make_worktree(self, branch, folder):
        path = self.base / folder
        self.git("worktree", "add", "-q", "-b", branch, str(path))
        self.git("config", "user.name", "Test", cwd=path)
        self.git("config", "user.email", "test@example.invalid", cwd=path)
        return path

    def commit(self, folder, name, text="x\n"):
        (folder / name).write_text(text, encoding="utf-8")
        self.git("add", "-A", cwd=folder)
        self.git("commit", "-qm", "add " + name, cwd=folder)
        return self.git("rev-parse", "HEAD", cwd=folder)

    def remote_commit(self, name, text="remote\n"):
        """A commit of someone else on the remote integration branch."""
        other = self.base / ("other-%s" % name)
        sh("git", "clone", "-q", str(self.remote), str(other))
        self.git("config", "user.name", "Other", cwd=other)
        self.git("config", "user.email", "other@example.invalid", cwd=other)
        self.commit(other, name, text)
        self.git("push", "-q", "origin", "HEAD:main", cwd=other)
        return other

    def remote_head(self):
        return sh("git", "-C", str(self.remote), "rev-parse", "main")

    def ready_plan(self, rel=REL, branch="plan-br", folder=None, feature="feature.txt", status="RUNNING", header=""):
        """A plan whose Queen is done: a commit on the plan branch, an accepted ready row, the hive linked to it, no live agent."""
        folder = folder or self.pwt
        self.plan(status, [{"id": "P1", "title": "First"}], rel=rel, title=rel, branch=branch, worktree=str(folder))
        if header:
            path = self.repo / rel
            path.write_text(path.read_text(encoding="utf-8").replace('status = "%s"\n' % status, 'status = "%s"\n%s' % (status, header), 1), encoding="utf-8")
            runtime.sync_plans(self.conn, "testproj", str(self.repo))
        tip = self.commit(folder, feature)
        hives.create(self.conn, "testproj", rel, "claude", "white")
        ready = db.add_ready(self.conn, "testproj", branch, tip, rel, "")
        self.conn.execute("UPDATE hives SET ready_id=?, state='running' WHERE project='testproj' AND plan_path=?", (ready["id"], rel))
        return tip

    def run_order(self):
        return self.order.read_text(encoding="utf-8").split() if self.order.exists() else []

    def echo(self, word):
        return "echo %s>> %s" % (word, self.order)

    def fixers(self):
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "fix"]

    def queens(self):
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]

    def plan_status(self, rel=REL):
        return plans.parse_plan_text((self.repo / rel).read_text(encoding="utf-8")).status

    def state(self, rel=REL):
        return orchestrator.load(self.conn, "testproj", rel)

    def end_fixer(self, agent, report="fixed it"):
        self.finish(agent["name"], final=report)
        runtime.upsert_agent(self.conn, "testproj", agent["name"], {"pid": None})

    def question(self, item):
        return [q for q in runtime.list_questions(self.conn, "testproj", plan=REL) if q["item"] == item]


class PipelineOrder(OrchCase):
    """O-2: the steps run in order in the plan's worktree; an empty command skips its step."""

    def setUp(self):
        super().setUp()
        self.check, self.build, self.deploy = self.echo("Check"), self.echo("Build"), self.echo("Deploy")
        self.project_toml()

    def test_all_steps_in_order_and_close(self):
        self.remote_commit("elsewhere.txt")
        tip = self.ready_plan()
        self.round()
        self.assertEqual(self.run_order(), ["Check", "Build", "Deploy"])
        head = self.git("rev-parse", "HEAD", cwd=self.pwt)
        self.assertEqual(self.remote_head(), head)   # pushed (the remote commit was merged into the plan first)
        self.assertTrue((self.pwt / "elsewhere.txt").exists())
        self.assertNotEqual(head, tip)
        row = self.conn.execute("SELECT status, tip FROM queue WHERE project='testproj' AND plan_path=? ORDER BY id DESC LIMIT 1", (REL,)).fetchone()
        self.assertEqual((row["status"], row["tip"]), ("integrated", head))
        self.assertEqual(self.queens(), [])
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "owner_check")
        text = self.question("owner-check")[0]["question"]
        self.assertIn(head[:12], text)
        self.assertIn("Merge, Check, Build, Push, Deploy", text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)

    def test_empty_commands_skip_their_steps(self):
        self.check = self.deploy = ""
        self.build = self.echo("Build")
        self.project_toml()
        self.ready_plan()
        self.round()
        self.assertEqual(self.run_order(), ["Build"])
        self.assertIn("Merge, Build, Push", self.question("owner-check")[0]["question"])
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))

    def test_owner_check_none_gives_done_after_close(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan(header='owner_check = "none"\n')
        self.round()
        self.assertEqual(self.plan_status(), "DONE")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))

    def test_accept_gives_done(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan()
        self.round()
        owner.apply(self.conn, "accept", "testproj", REL)
        self.round()
        self.assertEqual(self.plan_status(), "DONE")
        self.assertEqual(self.queens(), [])
        self.assertEqual(self.fixers(), [])

    def test_only_the_plan_worktree_changes(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.remote_commit("elsewhere.txt")
        self.ready_plan()
        before = (self.git("rev-parse", "HEAD"), self.git("status", "--short"))
        self.round()
        self.assertEqual((self.git("rev-parse", "HEAD"), self.git("status", "--short")), before)   # the main checkout stays as it was
        self.assertFalse((self.repo / "elsewhere.txt").exists())

    def test_moved_remote_ends_integrated_without_force(self):
        self.build = "git -C %s push -q origin HEAD:main" % (self.base / "other-late")
        self.check = self.echo("Check")
        self.deploy = ""
        self.project_toml()
        other = self.base / "other-late"
        sh("git", "clone", "-q", str(self.remote), str(other))
        self.git("config", "user.name", "Other", cwd=other)
        self.git("config", "user.email", "other@example.invalid", cwd=other)
        late = self.commit(other, "late.txt")
        self.ready_plan()
        self.round()
        self.assertEqual(self.run_order(), ["Check", "Check"])   # the whole pipeline ran again onto the moved remote
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        sh("git", "-C", str(self.remote), "merge-base", "--is-ancestor", late, "main")   # fails when the late commit was lost
        self.assertEqual(self.plan_status(), "REVIEW")


class FixAgent(OrchCase):
    """O-5: a red step starts a fix agent; the pipeline starts again at step 1 after its fix."""

    def setUp(self):
        self.check = "git ls-files --error-unmatch fixed.txt"
        super().setUp()

    def test_red_check_starts_a_fix_agent_and_the_pipeline_restarts(self):
        self.build = ""
        self.ready_plan()
        remote = self.remote_head()
        self.round()
        fixers = self.fixers()
        self.assertEqual(len(fixers), 1)
        self.assertEqual(self.queens(), [])
        self.assertEqual(self.remote_head(), remote)   # nothing was pushed
        self.assertEqual(self.plan_status(), "RUNNING")
        prompt = (self.env().run_dir(fixers[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Check", prompt)
        self.assertIn("fixed.txt", prompt)             # the error of the step
        self.assertIn(REL, prompt)                     # the plan
        self.assertIn("Do not push", prompt)
        self.assertEqual(fixers[0]["cwd"], str(self.pwt))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)   # the place is free while the fix agent works
        self.round()
        self.assertEqual(len(self.fixers()), 1)        # still working: no second agent
        self.commit(self.pwt, "fixed.txt")
        self.end_fixer(fixers[0])
        self.round()
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertIn("fixed it", self.question("owner-check")[0]["question"])   # the report of the fix agent, not an outdated one
        self.assertIn("fix agent", self.question("owner-check")[0]["question"])

    def test_merge_conflict_starts_a_fix_agent_that_keeps_both_sides(self):
        self.check = ""
        self.project_toml()
        self.commit(self.pwt, "same.txt", "plan side\n")
        self.remote_commit("same.txt", "remote side\n")
        self.ready_plan(feature="own.txt")
        self.round()
        fixers = self.fixers()
        self.assertEqual(len(fixers), 1)
        prompt = (self.env().run_dir(fixers[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("both sides", prompt)
        self.assertIn("merge", prompt.lower())
        (self.pwt / "same.txt").write_text("plan side\nremote side\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.pwt)
        self.git("commit", "-qm", "merge", cwd=self.pwt)
        self.end_fixer(fixers[0])
        self.round()
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        self.assertEqual(len(self.fixers()), 1)

    def test_fix_agent_stopped_by_the_owner_is_not_restarted(self):
        self.ready_plan()
        self.round()
        agent = self.fixers()[0]
        self.assertTrue(orchestrator.owner_stop(self.conn, "testproj", agent["name"]))
        self.end_fixer(agent)
        self.round()
        self.assertEqual(self.state()["phase"], "blocked")
        self.round()
        self.round()
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.plan_status(), "WAITING")
        questions = self.question("fix-stopped")
        self.assertEqual(len(questions), 1)
        db.answer_question(self.conn, questions[0]["id"], "Try again, add fixed.txt", "owner")
        self.round()
        self.assertEqual(len(self.fixers()), 2)
        prompt = (self.env().run_dir(self.fixers()[1]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Try again, add fixed.txt", prompt)

    def test_failed_fix_attempts_escalate_up_to_one_owner_question(self):
        self.settings = {"escalateAfter": 1}
        self.machine.update(self.settings)
        self.ready_plan()
        for _ in range(8):
            self.round()
            for agent in self.fixers():
                if agent["status"] in ("running", "starting"):
                    self.end_fixer(agent)
        self.assertEqual([a["class"] for a in self.fixers()], ["medium", "heavy"])   # then no higher class is offered: the owner is asked once
        self.assertEqual(len(self.question("fix-escalation")), 1)
        self.assertEqual(self.plan_status(), "WAITING")

    def test_waiting_plan_does_not_block_another_plan(self):
        second = self.make_worktree("other-br", "otherwt")
        self.ready_plan()
        self.commit(second, "fixed.txt")
        self.ready_plan(rel=OTHER, branch="other-br", folder=second, feature="other.txt")
        self.round()
        self.assertEqual(self.plan_status(OTHER), "REVIEW")           # integrated while the first plan waits for its fix agent
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.plan_status(REL), "RUNNING")
        self.assertIsNotNone(self.state(REL))


class WaitingAndRam(OrchCase):
    def test_waits_for_the_integration_place(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan()
        hives.take_integration(self.conn, "testproj", OTHER)   # another plan holds the place
        self.round()
        self.assertEqual(self.plan_status(), "WAITING")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "origin/main"))
        hives.release_integration(self.conn, "testproj", OTHER)
        self.round()
        self.assertEqual(self.plan_status(), "REVIEW")

    def test_build_waits_for_the_free_ram_minimum(self):
        self.check = self.echo("Check")
        self.build = self.echo("Build")
        self.project_toml()
        self.ready_plan()
        self.round(free_ram=lambda: 0.5)
        self.assertEqual(self.run_order(), ["Check"])
        self.assertEqual(self.plan_status(), "WAITING")
        self.assertIn("free RAM", runtime.kv_get(self.conn, "testproj", "hive-blocker:" + REL))
        self.round(free_ram=lambda: 16.0)
        self.assertEqual(self.run_order(), ["Check", "Build"])
        self.assertEqual(self.plan_status(), "REVIEW")

    def test_push_and_deploy_of_the_same_head_are_not_repeated(self):
        self.deploy = self.echo("Deploy")
        self.project_toml()
        self.ready_plan()
        head = self.git("rev-parse", "HEAD", cwd=self.pwt)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.pwt)   # an earlier run pushed and deployed this commit
        state = orchestrator._new_state(self.conn, self.env(), REL, hives.get(self.conn, "testproj", REL))
        state.update(pushed=head, deployed=head)
        orchestrator.save(self.conn, "testproj", REL, state)
        self.round()
        self.assertEqual(self.run_order(), [])
        self.assertEqual(self.plan_status(), "REVIEW")


class OwnerCheckFix(OrchCase):
    def test_rejection_starts_a_fix_agent_and_the_whole_pipeline_again(self):
        self.check = self.echo("Check")
        self.project_toml()
        self.ready_plan()
        self.round()
        self.assertEqual(self.run_order(), ["Check"])
        self.assertEqual(len(self.question("owner-check")), 1)
        owner.apply(self.conn, "reject", "testproj", REL, text="The courtyard is too green", packages=["P1"])
        self.round()
        self.assertEqual(self.queens(), [])
        fixers = self.fixers()
        self.assertEqual(len(fixers), 1)
        self.assertIn("The courtyard is too green", (self.env().run_dir(fixers[0]["name"]) / "prompt.md").read_text(encoding="utf-8"))
        self.assertEqual(self.plan_status(), "RUNNING")
        self.commit(self.pwt, "courtyard.txt")
        self.end_fixer(fixers[0], "made it less green")
        self.round()
        self.assertEqual(self.run_order(), ["Check", "Check"])          # the pipeline ran again from step 1
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        self.assertEqual(self.plan_status(), "REVIEW")
        open_checks = [q for q in self.question("owner-check") if q["status"] == "open"]
        self.assertEqual(len(open_checks), 1)                            # a new owner check
        self.assertIn("made it less green", open_checks[0]["question"])
        self.assertEqual(bees.rejected_packages(self.conn, "testproj", REL), [])   # the rejected package counts done again
        self.assertEqual(self.queens(), [])

    def test_answer_through_the_questions_window_also_starts_a_fix_agent(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan()
        self.round()
        qid = self.question("owner-check")[0]["id"]
        self.conn.execute("UPDATE questions SET status='answered',answer='no',answered_by='owner',answered_at=?,owner_check_result='rejected' WHERE id=?", (db.now(), qid))
        self.round()
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.queens(), [])


class AsyncCommands(OrchCase):
    """Commands run in a thread: a pause lets them end and halts, a stop of the plan ends them, one compile check at a time."""

    def sleeper(self, seconds):
        return '"%s" -c "import time;time.sleep(%s)"' % (sys.executable, seconds)

    def wait_job(self, timeout=20):
        end = time.time() + timeout
        while orchestrator.running_jobs() and time.time() < end:
            time.sleep(0.05)
        self.assertFalse(orchestrator.running_jobs())

    def test_pause_lets_the_command_end_then_halts_and_the_resume_goes_on(self):
        orchestrator.ASYNC = True
        self.check = self.sleeper(1) + " && " + self.echo("Check")
        self.build = self.echo("Build")
        self.project_toml()
        self.ready_plan()
        self.round()
        self.assertTrue(orchestrator.running_jobs())
        hives.pause_plan(self.conn, "testproj", REL)
        self.wait_job()
        self.round()
        self.assertEqual(self.run_order(), ["Check"])                   # the command ran to its end, the next step did not start
        self.assertEqual(self.remote_head(), self.git("rev-parse", "origin/main"))
        hives.start_again(self.conn, "testproj", REL)
        self.round()
        self.wait_job()
        self.round()
        self.assertEqual(self.run_order(), ["Check", "Build"])
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))

    def test_stopping_the_plan_ends_its_command(self):
        orchestrator.ASYNC = True
        self.check = self.sleeper(60)
        self.project_toml()
        self.ready_plan()
        self.round()
        self.assertTrue(orchestrator.running_jobs())
        started = time.time()
        hives.stop_hive(self.conn, "testproj", REL)
        self.wait_job()
        self.assertLess(time.time() - started, 30)
        self.assertNotIn("job", self.state())

    def test_restart_with_a_lost_command_runs_the_step_again(self):
        self.check = self.echo("Check")
        self.project_toml()
        self.ready_plan()
        state = orchestrator._new_state(self.conn, self.env(), REL, hives.get(self.conn, "testproj", REL))
        state["placed"] = True
        state["step"] = "Check"
        state["job"] = {"step": "Check", "token": "gone", "file": str(self.base / "nothing.json"), "pid": None, "head": ""}
        orchestrator.save(self.conn, "testproj", REL, state)
        self.round()
        self.assertEqual(self.run_order(), ["Check"])
        self.assertEqual(self.plan_status(), "REVIEW")

    def test_the_check_step_follows_the_limit_of_concurrent_compile_checks(self):
        """K-2: with the limit at 2 the Check step runs beside one other compile check and waits while two run."""
        orchestrator.ASYNC = True
        self.check = self.echo("Check")
        self.project_toml()
        self.ready_plan()
        settings = self.env().settings
        self.assertEqual(integration.compile_limit(settings), 2)
        other = integration._acquire_compile_slot(settings, 0.05)   # another check of the machine runs
        try:
            self.round()
            self.wait_job()
            self.round()
            self.assertEqual(self.run_order(), ["Check"])   # not held back by one other check
        finally:
            integration._release_compile_slot(other, 0.05)
        self.assertEqual(integration._compile_holders(), [])

    def test_the_check_step_waits_while_the_limit_is_reached(self):
        orchestrator.ASYNC = True
        self.check = self.echo("Check")
        self.project_toml()
        self.ready_plan()
        settings = self.env().settings
        held = [integration._acquire_compile_slot(settings, 0.05) for _ in range(integration.compile_limit(settings))]
        try:
            self.round()
            time.sleep(1.5)
            self.assertEqual(self.run_order(), [])
        finally:
            for slot in held:
                integration._release_compile_slot(slot, 0.05)
        self.wait_job()
        self.round()
        self.assertEqual(self.run_order(), ["Check"])


class WorktreeCleanup(OrchCase):
    def test_cleanup_keeps_the_worktree_of_a_plan_the_orchestrator_holds(self):
        from haivequeen import worktrees
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan()
        env = self.env()
        self.assertFalse(orchestrator.holds_branch(env, "plan-br"))
        state = orchestrator._new_state(self.conn, env, REL, hives.get(self.conn, "testproj", REL))
        self.assertTrue(orchestrator.holds_branch(env, "plan-br"))
        self.assertEqual(state["phase"], "pipeline")
        runtime.kv_set(self.conn, "testproj", orchestrator.KEY + REL, "")
        self.assertFalse(orchestrator.holds_branch(env, "plan-br"))


class QueenEndsAtReady(OrchCase):
    """O-1: the Queen no longer integrates."""

    def test_start_text_has_no_integration_step(self):
        text = integration.start_text(self.env(), REL, "claude")
        for word in ("acquire", "compile-check", "integration release", "Build", "Deploy", "owner-check"):
            self.assertNotIn(word, text)
        self.assertIn("bzz ready", text)

    def test_ready_needs_no_compile_check_result(self):
        self.check = "exit 1"
        self.project_toml()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}], branch="plan-br", worktree=str(self.pwt))
        self.allow_ready()
        self.commit(self.pwt, "feature.txt")
        hives.create(self.conn, "testproj", REL, "claude", "white")
        self.agent("Queen1", phase="queen", plan_path=REL, cwd=str(self.pwt), status="running", pid=os.getpid())
        args = SimpleNamespace(project="testproj", branch="plan-br", tip=self.git("rev-parse", "HEAD", cwd=self.pwt), plan=REL, note="")
        with mock.patch.dict(os.environ, {"BZZ_AGENT": "Queen1"}), mock.patch("builtins.print"):
            self.assertEqual(cli.cmd_ready(args, self.conn), 0)
        self.assertEqual(self.conn.execute("SELECT status FROM queue WHERE project='testproj'").fetchone()[0], "ready")

    def test_queen_runs_no_compile_check(self):
        self.check = "exit 0"
        self.project_toml()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}], branch="plan-br", worktree=str(self.pwt))
        hives.create(self.conn, "testproj", REL, "claude", "white")
        self.agent("Queen1", phase="queen", plan_path=REL, cwd=str(self.pwt), status="running", pid=os.getpid())
        args = SimpleNamespace(project="testproj", plan=REL, worktree=None)
        with mock.patch.dict(os.environ, {"BZZ_AGENT": "Queen1"}), mock.patch("builtins.print"):
            self.assertEqual(cli.cmd_compile_check(args, self.conn), 1)

    def test_takeover_waits_for_the_ended_queen(self):
        self.check = self.deploy = self.build = ""
        self.project_toml()
        self.ready_plan()
        self.agent("Queen1", phase="queen", plan_path=REL, cwd=str(self.pwt), status="running", pid=os.getpid())
        self.round()
        self.assertIsNone(self.state())
        self.finish("Queen1")
        runtime.upsert_agent(self.conn, "testproj", "Queen1", {"pid": None})
        self.round()
        self.assertEqual(self.plan_status(), "REVIEW")
        self.assertEqual(len(self.queens()), 1)                          # no second Queen started
