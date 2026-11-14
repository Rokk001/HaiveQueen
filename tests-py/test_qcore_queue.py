"""Q-1 through Q-5: real Git queue states with deliberately held command results."""
from collections import Counter
import shlex
import sys
import threading
import time
from unittest import mock

from bzztest import REL
from haivequeen import buildjobs, hives, orchestrator
from test_o1_orchestrator import OTHER, OrchCase

THIRD = "docs/plans/third/THIRD-PLAN.md"


class SpeculativeQueue(OrchCase):
    check = build = deploy = "controlled command"

    def setUp(self):
        super().setUp()
        self.second = self.make_worktree("second-br", "secondwt")
        self.ready_plan(feature="first.txt")
        self.ready_plan(OTHER, "second-br", self.second, "second.txt")
        self.started = Counter()
        self.finished = set()
        self.failed = set()
        patcher = mock.patch.object(orchestrator, "_command", self.command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, conn, env, plan, state, folder, step, free_mb):
        if state["steps"].get(step) != "running":
            self.started[plan, step] += 1
            state["steps"][step] = "running"
            orchestrator.save(conn, env.name, plan, state)
        if (plan, step) in self.failed:
            return orchestrator._red(conn, env, plan, state, step, "controlled failure")
        if (plan, step) in self.finished:
            return orchestrator._done(conn, env, plan, state, step)
        return None

    def tick(self):
        orchestrator.run_round(self.conn, self.env(), 16384, alive=lambda pid: False)

    def finish_step(self, plan, step):
        self.finished.add((plan, step))
        self.tick()

    def assert_contains(self, folder, *names):
        for name in names:
            self.assertTrue((folder / name).exists(), name)

    def test_three_plans_merge_cumulatively_and_check_together(self):
        self.third = self.make_worktree("third-br", "thirdwt")
        self.ready_plan(THIRD, "third-br", self.third, "third.txt")
        self.tick()
        self.assert_contains(self.second, "first.txt", "second.txt")
        self.assert_contains(self.third, "first.txt", "second.txt", "third.txt")
        for plan in (REL, OTHER, THIRD):
            self.assertEqual(self.state(plan)["steps"]["Check"], "running")
            self.assertEqual(self.started[plan, "Check"], 1)
        self.assertNotEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))

    def test_serial_build_waits_for_earlier_integration(self):
        """Keep builds and their checks in queue order when the project requires it."""
        self.project_toml("serialIntegrationBuild = true\n")
        self.tick()
        self.finish_step(OTHER, "Check")
        self.assertEqual(self.started[OTHER, "Build"], 0)
        self.finish_step(REL, "Check")
        self.assertEqual(self.started[REL, "Build"], 1)
        self.assertEqual(self.started[OTHER, "Build"], 0)
        self.finish_step(REL, "Build")
        self.assertEqual(self.started[OTHER, "Build"], 0)
        self.finish_step(REL, "Deploy")
        self.assertEqual(self.started[OTHER, "Build"], 1)
        self.assertEqual(self.started[OTHER, "Check"], 1)

    def test_parallel_builds_ordered_push_deploy_and_no_recheck(self):
        self.tick()
        self.finish_step(OTHER, "Check")
        self.finish_step(REL, "Check")
        self.assertEqual(self.state()["steps"]["Build"], "running")
        self.assertEqual(self.state(OTHER)["steps"]["Build"], "running")
        self.finish_step(OTHER, "Build")
        self.assertEqual(self.state(OTHER)["step"], "Push")
        self.assertNotIn("Push", self.state(OTHER)["steps"])
        self.finish_step(REL, "Build")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        self.assertEqual(self.state()["steps"]["Deploy"], "running")
        self.assertEqual(self.started[OTHER, "Deploy"], 0)
        self.finish_step(REL, "Deploy")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.second))
        self.assertEqual(self.state(OTHER)["steps"]["Deploy"], "running")
        self.finish_step(OTHER, "Deploy")
        self.assertIsNone(self.state(OTHER))
        for plan in (REL, OTHER):
            for step in ("Check", "Build", "Deploy"):
                self.assertEqual(self.started[plan, step], 1)

    def fail_and_replay(self, step, running):
        self.tick()
        if running == "Build":
            self.finish_step(OTHER, "Check")
        if step in ("Build", "Push", "Deploy"):
            self.finish_step(REL, "Check")
        if step in ("Push", "Deploy"):
            if step == "Push":
                with mock.patch.object(orchestrator, "_push", return_value=None):
                    self.finish_step(REL, "Build")
            else:
                self.finish_step(REL, "Build")
        if step == "Merge":
            # Reproduce a Merge retry that fails after successors speculated.
            state = self.state()
            state["step"] = "Merge"
            orchestrator.save(self.conn, "testproj", REL, state)
        follower = self.state(OTHER)
        job = orchestrator.Job("held-worker")
        job.thread = mock.Mock()
        job.thread.is_alive.return_value = True
        follower["job"] = {"step": running, "token": job.token, "pid": None}
        orchestrator.JOBS["testproj", OTHER] = job
        self.addCleanup(orchestrator.JOBS.pop, ("testproj", OTHER), None)
        orchestrator.save(self.conn, "testproj", OTHER, follower)
        if step in ("Merge", "Push"):
            real_git = orchestrator._git

            def fail_git(folder, *args, **kwargs):
                if folder == self.pwt and args[0] == step.lower():
                    return 1, "controlled failure"
                return real_git(folder, *args, **kwargs)

            with mock.patch.object(orchestrator, "_git", side_effect=fail_git):
                self.tick()
        else:
            self.failed.add((REL, step))
            self.tick()
        self.assertTrue(job.cancelled)
        self.tick()
        self.assertEqual(len(self.fixers()), 1)
        self.assertTrue(self.state(OTHER)["replay"])
        self.assertTrue((self.second / "first.txt").exists())
        # The old worker must stop before its worktree is restored, even after restart.
        job.thread.is_alive.return_value = False
        self.finished.discard((OTHER, "Check"))
        self.tick()
        self.assertFalse((self.second / "first.txt").exists())
        self.assert_contains(self.second, "second.txt")
        self.assertEqual(self.started[OTHER, "Check"], 2)
        self.assertEqual(self.state(OTHER)["steps"]["Check"], "running")
        self.commit(self.pwt, "fixed.txt")
        self.end_fixer(self.fixers()[0], verification_command='git ls-files --error-unmatch fixed.txt')
        self.failed.discard((REL, step))
        self.finished.update((REL, command) for command in ("Check", "Build", "Deploy"))
        self.tick()
        self.assertEqual(orchestrator._ahead(self.conn, "testproj", REL), [OTHER])
        self.assertEqual(self.state()["step"], "Push")
        self.finish_step(OTHER, "Check")
        self.finish_step(OTHER, "Build")
        second_head = self.remote_head()
        self.assertFalse((self.second / "first.txt").exists())
        self.finish_step(OTHER, "Deploy")
        self.tick()
        self.assertIsNone(self.state())
        self.git("merge-base", "--is-ancestor", second_head, self.remote_head())
        self.assert_contains(self.pwt, "first.txt", "second.txt", "fixed.txt")

    def test_red_merge_replays_running_check(self):
        self.fail_and_replay("Merge", "Check")

    def test_red_check_replays_running_check(self):
        self.fail_and_replay("Check", "Check")

    def test_access_failure_invalidates_speculative_followers(self):
        self.tick()
        self.assert_contains(self.second, 'first.txt', 'second.txt')
        orchestrator._red(self.conn, self.env(), REL, self.state(),
                          'Check', 'remote check: HTTP 403 forbidden')
        self.assertEqual(self.state()['phase'], 'blocked')
        self.assertTrue(self.state(OTHER).get('replay'))
        self.tick()
        self.assertFalse((self.second / 'first.txt').exists())
        self.assertEqual(self.started[OTHER, 'Check'], 2)
        self.assertEqual(self.state()['phase'], 'blocked')

    def test_access_failure_after_publication_keeps_followers(self):
        self.tick()
        self.finish_step(REL, 'Check')
        self.finish_step(REL, 'Build')
        self.assertEqual(self.state()['step'], 'Deploy')
        orchestrator._red(self.conn, self.env(), REL, self.state(),
                          'Deploy', 'deployment: HTTP 403 forbidden')
        self.assertFalse(self.state(OTHER).get('replay'))
        self.tick()
        self.assert_contains(self.second, 'first.txt', 'second.txt')
        self.assertEqual(self.started[OTHER, 'Check'], 1)

    def test_repaired_plan_joins_behind_all_current_queue_members(self):
        self.tick()
        self.failed.add((REL, "Check"))
        self.tick()
        self.assertEqual(len(self.fixers()), 1)
        self.assertEqual(self.state()["phase"], "fix")
        self.assertEqual(self.started[OTHER, "Check"], 2)

        # A new plan joins while the first plan is being repaired.
        self.third = self.make_worktree("third-br", "thirdwt")
        self.ready_plan(THIRD, "third-br", self.third, "third.txt")
        self.tick()
        self.assertEqual(orchestrator._ahead(self.conn, "testproj", THIRD), [OTHER])
        self.assert_contains(self.third, "second.txt", "third.txt")
        self.assertFalse((self.third / "first.txt").exists())

        self.commit(self.pwt, "fixed.txt")
        self.end_fixer(self.fixers()[0], verification_command='git ls-files --error-unmatch fixed.txt')
        self.failed.clear()
        self.tick()
        self.assertEqual(orchestrator._ahead(self.conn, "testproj", REL), [OTHER, THIRD])
        self.assert_contains(self.pwt, "first.txt", "second.txt", "third.txt", "fixed.txt")
        self.assertEqual(self.state()["steps"]["Check"], "running")

        # Complete the checks and builds out of order; pushes still follow the row.
        for plan in (REL, THIRD, OTHER):
            self.finish_step(plan, "Check")
            self.finish_step(plan, "Build")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.second))
        self.finish_step(OTHER, "Deploy")
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.third))
        self.finish_step(THIRD, "Deploy")
        self.tick()
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
        self.finish_step(REL, "Deploy")
        self.assertIsNone(self.state())

    def test_red_check_cancels_active_successor_merge_before_replay(self):
        # Hold a real Git post-merge hook after the predecessor has been applied.
        script = self.base / "hold_merge.py"
        started = self.base / "merge.started"
        script.write_text(
            "from pathlib import Path\nimport sys, time\n"
            "Path(sys.argv[1]).touch()\nend = time.monotonic() + 15\n"
            "while time.monotonic() < end: time.sleep(.02)\n",
            encoding="utf-8")
        hooks = self.base / "hooks"
        hooks.mkdir()
        hook = hooks / "post-merge"
        hook.write_text("#!/bin/sh\nexec %s %s %s\n" % tuple(
            shlex.quote(str(path).replace("\\", "/")) for path in (sys.executable, script, started)),
            encoding="utf-8")
        hook.chmod(0o755)
        orchestrator.advance(self.conn, self.env(), REL, 16384, alive=lambda pid: False)
        finished = threading.Event()
        release = threading.Event()
        real_work = orchestrator._merge_work
        real_git = orchestrator._git

        def hooked_git(folder, *args, **kwargs):
            if folder == self.second and args[0] == "merge":
                args = ("-c", "core.hooksPath=" + str(hooks), *args)
            return real_git(folder, *args, **kwargs)

        def held_work(*args):
            try:
                real_work(*args)
            finally:
                finished.set()
                release.wait(60)

        def cleanup():
            release.set()
            job = orchestrator.JOBS.pop(("testproj", OTHER), None)
            if job and job.thread:
                job.cancelled = True
                if job.pid:
                    orchestrator.osplat.kill_process_tree(job.pid)
                job.thread.join(10)

        self.addCleanup(cleanup)
        with mock.patch.object(orchestrator, "ASYNC", True), \
                mock.patch.object(orchestrator, "_git", side_effect=hooked_git), \
                mock.patch.object(orchestrator, "_merge_work", side_effect=held_work):
            # This call must return while Git is still running.
            before = time.monotonic()
            orchestrator.advance(self.conn, self.env(), OTHER, 16384, alive=lambda pid: False)
            self.assertLess(time.monotonic() - before, 5, "active Merge blocked the orchestrator")
            end = time.monotonic() + 10
            while not started.exists() and time.monotonic() < end:
                time.sleep(.02)
            self.assertTrue(started.exists(), "successor Merge did not start")
            job = orchestrator.JOBS["testproj", OTHER]
            self.assertTrue(job.alive())
            merge_pid = job.pid
            self.assertIsNotNone(merge_pid)
            self.assert_contains(self.second, "first.txt", "second.txt")
            self.failed.add((REL, "Check"))
            self.tick()
            self.assertTrue(job.cancelled)
            self.assertEqual(self.state(OTHER)["job"]["pid"], merge_pid)
            self.assertTrue(finished.wait(10), "cancelled Git process did not exit")
            self.assertEqual(len(self.fixers()), 1)
            self.assertTrue(self.state(OTHER)["replay"])
            self.tick()
            self.assert_contains(self.second, "first.txt")
            self.assertEqual(self.started[OTHER, "Check"], 0)
            release.set()
            job.thread.join(10)
            self.assertFalse(job.alive())
        self.tick()
        self.assertFalse((self.second / "first.txt").exists())
        self.assert_contains(self.second, "second.txt")
        self.assertEqual(self.started[OTHER, "Check"], 1)
        self.assertEqual(self.state()["phase"], "fix")

    def test_red_build_replays_running_build(self):
        self.fail_and_replay("Build", "Build")

    def test_red_push_replays_running_build(self):
        self.fail_and_replay("Push", "Build")

    def test_red_deploy_keeps_successors_checks_unchanged(self):
        self.tick()
        self.finish_step(REL, "Check")
        self.finish_step(REL, "Build")
        self.failed.add((REL, "Deploy"))
        self.tick()
        self.assertEqual(len(self.fixers()), 1)
        self.assertFalse(self.state(OTHER).get("replay"))
        self.finish_step(OTHER, "Check")
        self.finish_step(OTHER, "Build")
        self.finish_step(OTHER, "Deploy")
        self.assertEqual(self.started[OTHER, "Check"], 1)
        self.assertEqual(self.started[OTHER, "Build"], 1)
        self.assert_contains(self.second, "first.txt", "second.txt")

    def test_remote_movement_invalidates_successor_results(self):
        self.tick()
        state = self.state()
        orchestrator._moved(self.conn, self.env(), REL, state, "Push")
        self.assertTrue(self.state(OTHER)["replay"])

    def test_red_release_keeps_successors_completed_checks_and_builds(self):
        from haivequeen import publish
        self.project_toml('wayOfWorking = "local"\ncheckEnabled = true\nbuildEnabled = true\n'
                          'deployEnabled = true\nreleaseEnabled = true\nreleaseMode = "local"\n')
        self.tick()
        for plan in (OTHER, REL):
            self.finish_step(plan, "Check")
            self.finish_step(plan, "Build")
        pushed = self.remote_head()
        self.assertEqual(pushed, self.state()["pushed"])
        with mock.patch.object(publish, "pipeline_release", side_effect=publish.ReleaseFailed("upload failed")):
            self.finish_step(REL, "Deploy")
        self.assertEqual(self.state()["phase"], "fix")
        self.assertFalse(self.state(OTHER).get("replay"))
        self.assertEqual(self.started[OTHER, "Check"], 1)
        self.assertEqual(self.started[OTHER, "Build"], 1)
        self.git("merge-base", "--is-ancestor", pushed, self.remote_head())
        with mock.patch.object(publish, "pipeline_release", return_value="published"):
            self.finish_step(OTHER, "Deploy")
        self.assertIsNone(self.state(OTHER))
        self.assertEqual(self.started[OTHER, "Check"], 1)
        self.assertEqual(self.started[OTHER, "Build"], 1)
        self.assert_contains(self.second, "first.txt", "second.txt")

    def test_middle_failure_replays_the_tail_onto_the_surviving_prefix(self):
        self.third = self.make_worktree("third-br", "thirdwt")
        self.ready_plan(THIRD, "third-br", self.third, "third.txt")
        self.tick()
        self.failed.add((OTHER, "Check"))
        self.tick()
        self.assertEqual(len(self.fixers()), 1)
        self.assert_contains(self.third, "first.txt", "third.txt")
        self.assertFalse((self.third / "second.txt").exists())
        self.assertEqual(self.started[REL, "Check"], 1)
        self.assertEqual(self.started[THIRD, "Check"], 2)

    def test_legacy_integration_holder_blocks_speculative_merge(self):
        hives.take_integration(self.conn, "testproj", "legacy")
        self.tick()
        self.assertEqual(self.started[REL, "Check"], 0)
        self.assertEqual(self.started[OTHER, "Check"], 0)

    def test_ready_order_survives_a_temporarily_missing_first_worktree(self):
        real_worktree = orchestrator._worktree
        with mock.patch.object(orchestrator, "_worktree", side_effect=lambda env, plan:
                               None if plan == REL else real_worktree(env, plan)):
            self.tick()
        self.assertEqual(self.started[OTHER, "Check"], 0)
        self.tick()
        self.assert_contains(self.second, "first.txt", "second.txt")

    def test_successor_build_waits_for_cores_and_ram(self):
        self.tick()
        env = self.env()
        state = self.state(OTHER)
        state.update(step="Build", steps={"Merge": "done", "Check": "done"})
        orchestrator.save(self.conn, env.name, OTHER, state)
        # Call the real command gate, before any command can be launched.
        real_command = type(self).real_command
        with mock.patch.object(buildjobs, "waiting_for_ram", return_value="free RAM below minimum"), \
                mock.patch.object(buildjobs, "plan_build") as plan_build:
            self.assertIsNone(real_command(self.conn, env, OTHER, state, self.second, "Build", 1))
            plan_build.assert_not_called()
        with mock.patch.object(buildjobs, "waiting_for_ram", return_value=None), \
                mock.patch.object(buildjobs, "settings_from", return_value={}), \
                mock.patch.object(buildjobs, "plan_build", return_value={"Jobs": 0}):
            self.assertIsNone(real_command(self.conn, env, OTHER, state, self.second, "Build", 16384))
        self.assertNotIn("job", self.state(OTHER))

    real_command = staticmethod(orchestrator._command)


class LiveQueue(OrchCase):
    """Real supervisors and workers prove the machine limits apply within a project."""

    def setUp(self):
        super().setUp()
        self.script = self.base / "command.py"
        self.script.write_text(
            "from pathlib import Path\nimport sys, time\n"
            "kind = sys.argv[1]\n"
            "Path(kind + '.started').touch()\n"
            "while not Path(sys.argv[2], kind + '.release').exists(): time.sleep(.02)\n",
            encoding="utf-8")
        self.check = '"%s" "%s" Check "%s"' % (sys.executable, self.script, self.base)
        self.build = '"%s" "%s" Build "%s"' % (sys.executable, self.script, self.base)
        self.project_toml()
        self.second = self.make_worktree("second-br", "secondwt")
        self.ready_plan(feature="first.txt")
        self.ready_plan(OTHER, "second-br", self.second, "second.txt")
        orchestrator.ASYNC = True

    def tearDown(self):
        self.release_workers()
        super().tearDown()

    def release_workers(self):
        for step in ("Check", "Build"):
            (self.base / (step + ".release")).touch()
        self.wait_for(lambda: not orchestrator.running_jobs())

    def wait_for(self, condition):
        end = time.monotonic() + 30
        while not condition() and time.monotonic() < end:
            time.sleep(.05)
        self.assertTrue(condition(), "pipeline worker did not reach the expected state")

    def tick(self):
        orchestrator.run_round(self.conn, self.env(), 16384, alive=lambda pid: False)

    def start_merges(self):
        end = time.monotonic() + 30
        while time.monotonic() < end:
            self.tick()
            if all(self.state(plan).get("step") != "Merge" for plan in (REL, OTHER)):
                return
            time.sleep(.05)
        self.fail("queue merges did not finish")

    def test_two_checks_and_builds_overlap_inside_one_project(self):
        self.machine["maxConcurrentCompileChecks"] = 3
        self.start_merges()
        self.wait_for(lambda: all((folder / "Check.started").exists() for folder in (self.pwt, self.second)))
        self.assert_contains_both()
        (self.base / "Check.release").touch()
        self.wait_for(lambda: not orchestrator.running_jobs())
        self.tick()
        self.wait_for(lambda: all((folder / "Build.started").exists() for folder in (self.pwt, self.second)))
        self.assertIsNotNone(self.state())
        self.assertEqual(self.state(OTHER)["step"], "Build")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.second), self.state(OTHER)["merged"])

    def assert_contains_both(self):
        self.assertTrue((self.second / "first.txt").exists())
        self.assertTrue((self.second / "second.txt").exists())

    def test_limit_one_runs_only_one_check_and_next_waits(self):
        self.machine["maxConcurrentCompileChecks"] = 1
        self.start_merges()
        self.wait_for(lambda: any((folder / "Check.started").exists() for folder in (self.pwt, self.second)))
        time.sleep(.3)
        self.assertEqual(sum((folder / "Check.started").exists() for folder in (self.pwt, self.second)), 1)
        (self.base / "Check.release").touch()
        self.wait_for(lambda: all((folder / "Check.started").exists() for folder in (self.pwt, self.second)))

    def live_failure_and_replay(self, failure, running, reuse_pid=False):
        """Q-5: kill a real successor, replay it, then integrate both plans."""
        self.script.write_text(
            "from pathlib import Path\nimport os, sys, time, uuid\n"
            "kind, root = sys.argv[1], Path(sys.argv[2])\n"
            "(root / (kind + '.instance')).write_text(uuid.uuid4().hex)\n"
            "(root / (kind + '.pid')).write_text(str(os.getpid()))\n"
            "while not (root / (kind + '.release')).exists(): time.sleep(.02)\n"
            "(root / (kind + '.completed')).touch()\n", encoding="utf-8")
        self.machine["maxConcurrentCompileChecks"] = 3
        self.started = Counter()
        self.finished = set()
        self.failed = set()
        real_command = orchestrator._command
        real_git = orchestrator._git
        real_process_alive = orchestrator.osplat.process_alive
        resets = []
        old_job = old_pid = None

        def command(conn, env, plan, state, folder, step, free_mb):
            if plan == REL:
                return SpeculativeQueue.command(self, conn, env, plan, state, folder, step, free_mb)
            return real_command(conn, env, plan, state, folder, step, free_mb)

        def git(folder, *args, **kwargs):
            if folder == self.second and args[0] == "reset":
                # Observe the actual reset boundary, even if cancellation and
                # replay complete within a single orchestrator round.
                self.assertFalse(orchestrator.osplat.process_alive(old_pid))
                self.assertFalse(old_job.alive())
                self.assertFalse((self.base / (running + ".completed")).exists())
                resets.append(args)
            return real_git(folder, *args, **kwargs)

        def drive(condition):
            def round_until():
                # Let assertions at the reset boundary reach the test instead
                # of the round's per-plan exception logger.
                for plan in (REL, OTHER):
                    orchestrator.advance(self.conn, self.env(), plan, 16384, alive=lambda pid: False)
                return condition()
            self.wait_for(round_until)

        def process_alive(pid):
            if reuse_pid and resets and pid == old_pid:
                return True  # The operating system assigned this PID to another process.
            return real_process_alive(pid)

        with mock.patch.object(orchestrator, "_command", side_effect=command), \
                mock.patch.object(orchestrator, "_git", side_effect=git), \
                mock.patch.object(orchestrator.osplat, "process_alive", side_effect=process_alive):
            self.start_merges()
            self.wait_for(lambda: (self.base / "Check.pid").exists())
            if running == "Build":
                (self.base / "Check.release").touch()
                drive(lambda: (self.base / "Build.pid").exists())
            old_job = orchestrator.JOBS["testproj", OTHER]
            old_pid = int((self.base / (running + ".pid")).read_text())
            old_instance = (self.base / (running + ".instance")).read_text()
            self.assertTrue(orchestrator.osplat.process_alive(old_pid))
            self.assertTrue(old_job.alive())
            self.assertTrue((self.second / "first.txt").exists())
            original_remote = self.remote_head()

            if failure in ("Build", "Push"):
                self.finished.add((REL, "Check"))
            if failure == "Push":
                self.finished.add((REL, "Build"))
            if failure == "Merge":
                state = self.state()
                state["step"] = "Merge"
                orchestrator.save(self.conn, "testproj", REL, state)
            self.failed.add((REL, failure))

            def fail_git(folder, *args, **kwargs):
                if folder == self.pwt and args[0] == failure.lower():
                    return 1, "controlled failure"
                return git(folder, *args, **kwargs)

            with mock.patch.object(orchestrator, "_git", side_effect=fail_git):
                drive(lambda: self.state()["phase"] == "fix")
            self.assertTrue(old_job.cancelled)
            self.assertEqual(self.state()["steps"][failure], "red")
            self.assertEqual(self.remote_head(), original_remote)
            drive(lambda: bool(resets) and self.state(OTHER).get("step") != "Merge")
            self.assertFalse(old_job.alive())
            self.assertFalse((self.second / "first.txt").exists())
            self.assertTrue((self.second / "second.txt").exists())
            self.assertFalse(self.state(OTHER).get("replay", False))
            drive(lambda: (self.base / (running + ".instance")).read_text() not in ("", old_instance))
            drive(lambda: bool(self.fixers()))
            self.assertEqual(len(self.fixers()), 1)
            self.commit(self.pwt, "fixed.txt")
            self.end_fixer(self.fixers()[0], verification_command='git ls-files --error-unmatch fixed.txt')
            self.failed.clear()
            self.finished.update((REL, step) for step in ("Check", "Build", "Deploy"))
            drive(lambda: self.state()["phase"] == "pipeline" and self.state()["step"] == "Push")
            self.assertEqual(orchestrator._ahead(self.conn, "testproj", REL), [OTHER])
            self.release_workers()
            drive(lambda: self.state() is None and self.state(OTHER) is None)
            for plan in (REL, OTHER):
                row = self.conn.execute(
                    "SELECT status FROM queue WHERE project='testproj' AND plan_path=? "
                    "ORDER BY id DESC LIMIT 1", (plan,)).fetchone()
                self.assertEqual(row["status"], "integrated")
            self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD", cwd=self.pwt))
            for feature in ("first.txt", "second.txt", "fixed.txt"):
                self.assertTrue((self.pwt / feature).exists())

    def test_red_merge_stops_live_successor_check_and_integrates(self):
        self.live_failure_and_replay("Merge", "Check")

    def test_red_check_stops_live_successor_check_and_integrates(self):
        self.live_failure_and_replay("Check", "Check")

    def test_red_build_stops_live_successor_build_and_integrates(self):
        self.live_failure_and_replay("Build", "Build")

    def test_red_build_replays_when_stopped_worker_pid_is_reused(self):
        self.live_failure_and_replay("Build", "Build", reuse_pid=True)

    def test_red_push_stops_live_successor_build_and_integrates(self):
        self.live_failure_and_replay("Push", "Build")
