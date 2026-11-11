"""F4 (F-9): `bzz compile-check` for Queens and Agents, one check at a time per machine, with a progress watch."""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bzztest import REL
from haivequeen import bees, claude_hook, cli, integration, roles, runtime, scheduler
from test_k5_integration import IntegrationCase, provider

SLEEPER = "import time\nprint('test_before_the_stall', flush=True)\ntime.sleep(60)\n"
STEADY = "import time\nfor i in range(12):\n    print('test_%d' % i, flush=True)\n    time.sleep(0.5)\n"
BUSY = "import time\nend = time.time() + 7\nwhile time.time() < end:\n    sum(range(10000))\n"
FOREVER = "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.3)\n"
QUICK = "import time\ntime.sleep(0.5)\n"


class CompileCheckCase(IntegrationCase):
    """A Queen Ada and a process Agent Bo of the Plan, each with a worktree; the check is a Python script."""

    def setUp(self):
        super().setUp()
        data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(data.cleanup)
        patcher = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": data.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(integration, "POLL_SECONDS", 0.2, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def commit(self, folder, message):
        subprocess.run(["git", "-C", folder, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message], check=True, capture_output=True)

    def worktree(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.commit(folder.name, "one")
        return folder.name

    def setup_plan(self, script, command=None, **project):
        """The Plan, the Queen Ada and the Agent Bo, each with a worktree (both returned); the compile check runs `script`."""
        queen_tree, bee_tree = self.worktree(), self.worktree()
        for tree in (queen_tree, bee_tree):
            Path(tree, "check.py").write_text(script, encoding="utf-8")
        self.use(provider("claude", "Coding", "Queen", "Build", "Deploy"), compileCheck='"%s" check.py' % sys.executable if command is None else command, **project)
        self.hive()
        bees.ensure_attempts(self.conn)
        runtime.upsert_agent(self.conn, "testproj", "Ada", {"status": "running", "phase": "queen", "plan_path": REL, "pid": 4242, "provider": "claude", "cwd": queen_tree})
        runtime.upsert_agent(self.conn, "testproj", "Bo", {"status": "running", "phase": "bee", "plan_path": REL, "package": "P1", "pid": 4243, "provider": "claude", "cwd": bee_tree})
        return queen_tree, bee_tree

    def as_agent(self, name):
        patcher = mock.patch.dict(os.environ, {"BZZ_AGENT": name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, **values):
        buffer = io.StringIO()
        with mock.patch("haivequeen.config.load_config", return_value=self.machine), redirect_stdout(buffer):
            code = cli.cmd_compile_check(argparse.Namespace(project="testproj", plan=REL, **values), self.conn)
        text = buffer.getvalue().strip()
        return code, json.loads(text.splitlines()[-1]) if text else {}

    def ready(self):
        with mock.patch("haivequeen.readygate.gate", return_value=(True, "", False)):
            buffer = io.StringIO()
            with mock.patch("haivequeen.config.load_config", return_value=self.machine), redirect_stdout(buffer):
                code = cli.cmd_ready(argparse.Namespace(project="testproj", plan=REL, branch="b", tip="abc", note=""), self.conn)
            return code, json.loads(buffer.getvalue().strip().splitlines()[-1])


class WhoMayCheckTests(CompileCheckCase):
    def test_a_process_agent_checks_its_own_worktree_and_it_does_not_count_for_ready(self):
        _queen_tree, bee_tree = self.setup_plan(QUICK)
        self.as_agent("Bo")
        code, result = self.call()
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertEqual(result["commit"], integration.head_commit(bee_tree))
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Bo"))
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Ada"))
        self.assertTrue(runtime.kv_get(self.conn, "testproj", integration.COMPILE_AGENT_KEY + "Bo"))
        self.as_agent("Ada")
        code, accepted = self.ready()
        self.assertEqual(code, 0, accepted)   # O-1: the Queen needs no compile check result, the Orchestrator runs the check

    def test_a_subagent_agent_names_its_worktree_and_the_result_does_not_count_for_ready(self):
        _queen_tree, bee_tree = self.setup_plan(QUICK)
        sub_tree = str(Path(self.worktree()).absolute())
        Path(sub_tree, "check.py").write_text(QUICK, encoding="utf-8")
        self.conn.execute("INSERT INTO bee_worktrees VALUES (?,?,?,?)", ("testproj", REL, "P2", sub_tree))
        self.as_agent("Ada")
        code, result = self.call(worktree=sub_tree)
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertEqual(result["commit"], integration.head_commit(sub_tree))
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Ada"))
        self.assertEqual(self.ready()[0], 0)   # ready needs no check result of the Queen (O-1)
        code, result = self.call(worktree=bee_tree)   # the worktree of the running process Agent Bo is one of the Plan's Agents too
        self.assertEqual(code, 0, result)

    def test_a_path_of_no_agent_of_the_plan_is_refused_as_foreign(self):
        queen_tree, _ = self.setup_plan(QUICK)
        foreign = self.worktree()
        self.as_agent("Ada")
        for path in (foreign, queen_tree):
            code, result = self.call(worktree=path)
            self.assertEqual(code, 1, path)
            self.assertIn("foreign", result["error"])
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Ada"))

    def test_a_worktree_of_an_agent_of_another_plan_is_foreign(self):
        self.setup_plan(QUICK)
        other = self.worktree()
        self.conn.execute("INSERT INTO bee_worktrees VALUES (?,?,?,?)", ("testproj", "plans/OTHER.md", "P9", str(Path(other).absolute())))
        self.as_agent("Ada")
        code, result = self.call(worktree=other)
        self.assertEqual(code, 1)
        self.assertIn("foreign", result["error"])

    def test_without_a_command_the_answer_is_skipped_also_for_agents(self):
        self.setup_plan(QUICK, command="")
        self.as_agent("Bo")
        code, result = self.call()
        self.assertEqual(code, 0)
        self.assertIn("skipped", result)

    def test_the_queen_runs_no_compile_check_of_her_own(self):
        self.setup_plan(QUICK)
        self.as_agent("Ada")
        code, result = self.call()
        self.assertEqual(code, 1)
        self.assertIn("Queen runs no compile check", result["error"])
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Ada"))
        self.assertEqual(self.ready()[0], 0)

    def test_the_command_line_takes_a_worktree(self):
        args = cli.build_parser().parse_args(["compile-check", "--project", "p", "--plan", "x.md", "--worktree", "w"])
        self.assertEqual(args.worktree, "w")
        self.assertIsNone(cli.build_parser().parse_args(["compile-check", "--project", "p", "--plan", "x.md"]).worktree)
        self.assertIn("--worktree", claude_hook.SCHEMA[("compile-check", None)][1])


class ProgressWatchTests(CompileCheckCase):
    LIMITS = {"compileStallMinutes": 0.05, "compileMaxMinutes": 5}   # 3 seconds without progress

    def test_simultaneous_checks_wait_for_a_new_empty_lock(self):
        created = threading.Event()
        write_pid = threading.Event()
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()
        real_fdopen = os.fdopen

        def delayed_fdopen(*args, **kwargs):
            if threading.current_thread().name == "first-check":
                # Model platforms where a newly created, still-open file can be
                # read and unlinked before its creator writes the PID.
                os.close(args[0])
                created.set()
                if not write_pid.wait(5):
                    raise TimeoutError("first check was not released")
                args = (os.open(str(integration._compile_lock_path()), os.O_WRONLY | os.O_CREAT), *args[1:])
            return real_fdopen(*args, **kwargs)

        def check(acquired, release=None):
            lock = integration._acquire_compile_lock(0.01)
            acquired.set()
            if release:
                release.wait(5)
            with integration._compile_lock_guard(lock, 0.01):
                lock.unlink()

        first = threading.Thread(target=check, args=(first_acquired, release_first), name="first-check")
        second = threading.Thread(target=check, args=(second_acquired,), name="second-check")
        with mock.patch.object(integration.os, "fdopen", side_effect=delayed_fdopen):
            try:
                first.start()
                self.assertTrue(created.wait(5))
                second.start()
                self.assertFalse(second_acquired.wait(0.2), "second check took an empty new lock")
                write_pid.set()
                self.assertTrue(first_acquired.wait(5))
                self.assertFalse(second_acquired.wait(0.2), "checks ran together")
            finally:
                write_pid.set()
                release_first.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertTrue(second_acquired.is_set())

    def test_abandoned_empty_lock_is_recovered(self):
        path = integration._compile_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        acquired = threading.Event()

        def take_lock():
            lock = integration._acquire_compile_lock(0.01)
            acquired.set()
            lock.unlink()

        thread = threading.Thread(target=take_lock, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(2), "abandoned empty lock was never recovered")
        thread.join(2)

    def test_abandoned_partial_pid_is_recovered_even_if_prefix_is_live(self):
        path = integration._compile_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pid:%s" % os.getpid(), encoding="utf-8")
        acquired = threading.Event()

        def take_lock():
            lock = integration._acquire_compile_lock(0.01)
            acquired.set()
            lock.unlink()

        thread = threading.Thread(target=take_lock, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(2), "abandoned partial PID was never recovered")
        thread.join(2)

    def test_partial_pid_is_not_mistaken_for_dead_holder(self):
        partial = threading.Event()
        finish_write = threading.Event()
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()
        real_fdopen = os.fdopen

        class SplitWriter:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                self.handle.__enter__()
                return self
            def __exit__(self, *args):
                return self.handle.__exit__(*args)
            def write(self, value):
                split = 5 if value.startswith("pid:") else 1
                self.handle.write(value[:split])
                self.handle.flush()
                partial.set()
                if not finish_write.wait(5):
                    raise TimeoutError("PID write was not released")
                return self.handle.write(value[split:])

        def split_fdopen(*args, **kwargs):
            handle = real_fdopen(*args, **kwargs)
            return SplitWriter(handle) if threading.current_thread().name == "partial-writer" else handle

        def take_lock(acquired, release=None):
            lock = integration._acquire_compile_lock(0.01)
            acquired.set()
            if release:
                release.wait(5)
            with integration._compile_lock_guard(lock, 0.01):
                lock.unlink()

        first = threading.Thread(target=take_lock, args=(first_acquired, release_first), name="partial-writer", daemon=True)
        second = threading.Thread(target=take_lock, args=(second_acquired,), daemon=True)
        with mock.patch.object(integration.os, "fdopen", side_effect=split_fdopen), \
             mock.patch.object(integration.osplat, "process_alive", side_effect=lambda pid: pid == os.getpid()):
            try:
                first.start()
                self.assertTrue(partial.wait(5))
                second.start()
                self.assertFalse(second_acquired.wait(0.2), "partial PID was treated as a dead owner")
                finish_write.set()
                self.assertTrue(first_acquired.wait(5))
                self.assertFalse(second_acquired.wait(0.2), "checks ran together")
            finally:
                finish_write.set()
                release_first.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertTrue(second_acquired.is_set())

    def run_check(self, script, **limits):
        self.machine.update({**self.LIMITS, **limits})
        self.setup_plan(script)
        self.as_agent("Bo")
        return self.call()

    def test_steady_output_runs_to_the_end_beyond_the_stall_time(self):
        code, result = self.run_check(STEADY)
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertGreater(result["seconds"], 5)

    def test_cpu_use_without_output_runs_to_the_end(self):
        code, result = self.run_check(BUSY)
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertGreater(result["seconds"], 5)

    def test_no_output_and_no_cpu_ends_red_stalled_with_the_last_lines(self):
        start = time.time()
        code, result = self.run_check(SLEEPER)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertTrue(result.get("stalled"))
        self.assertIn("stalled", result["output"])
        self.assertIn("test_before_the_stall", result["output"])
        self.assertLess(time.time() - start, 30)

    def test_the_longest_run_time_ends_a_check_red(self):
        code, result = self.run_check(FOREVER, compileMaxMinutes=0.05)
        self.assertEqual((code, result["ok"]), (1, False))
        self.assertFalse(result.get("stalled"))
        self.assertIn("compileMaxMinutes", result["output"])
        self.assertIn("tick", result["output"])

    def test_a_second_check_waits_and_the_waiting_counts_for_neither_limit(self):
        self.machine.update({"compileStallMinutes": 0.05, "compileMaxMinutes": 0.05})
        self.setup_plan(QUICK)
        self.as_agent("Bo")
        lock = integration._compile_lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")   # another check is running
        timer = threading.Timer(5, lock.unlink)   # longer than both limits
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.time()
        code, result = self.call()
        self.assertEqual((code, result["ok"]), (0, True), result)
        self.assertGreaterEqual(time.time() - start, 5)
        self.assertLess(result["seconds"], 4)   # the wait is not part of the run time
        self.assertFalse(lock.exists())

    def test_a_lock_of_a_dead_process_is_taken_over(self):
        self.machine.update(self.LIMITS)
        self.setup_plan(QUICK)
        self.as_agent("Bo")
        lock = integration._compile_lock_path()
        lock.parent.mkdir(parents=True, exist_ok=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        lock.write_text(str(dead.pid), encoding="utf-8")
        self.assertEqual(self.call()[0], 0)


class SettingTests(unittest.TestCase):
    def test_defaults_are_10_and_120_minutes(self):
        settings = scheduler.build_settings({}, {})
        self.assertEqual((settings["compileStallMinutes"], settings["compileMaxMinutes"]), (10, 120))

    def test_config_toml_values_are_read(self):
        settings = scheduler.build_settings({"compileStallMinutes": 3, "compileMaxMinutes": 45}, {})
        self.assertEqual((settings["compileStallMinutes"], settings["compileMaxMinutes"]), (3, 45))

    def test_the_old_fixed_limit_is_gone(self):
        self.assertFalse(hasattr(integration, "COMPILE_TIMEOUT"))


class RoleTextTests(unittest.TestCase):
    def test_agent_and_queen_texts_name_the_command_and_waiting_without_polling(self):
        text = (roles.ROLES_DIR / "bee.md").read_text(encoding="utf-8")
        self.assertIn("bzz compile-check", text)
        self.assertIn("without polling", text)
        self.assertNotIn("bzz compile-check", (roles.ROLES_DIR / "queen.md").read_text(encoding="utf-8"))   # O-1: the Queen runs no compile check
        self.assertIn("--worktree", (roles.ROLES_DIR / "bee.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
