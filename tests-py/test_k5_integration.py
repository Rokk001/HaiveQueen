"""K5: delegation-free integration settings, command recognition and branch cleanup."""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bzztest import REL, RoundCase, integrated_tip
from haivequeen import agent_runner, bees, cli, db, settings, hives, inbox, integration, plans, routine, runtime, scheduler, worktrees


def provider(name, *capabilities, **extra):
    """A provider entry with explicit capabilities."""
    return {"name": name, "protocol": "claude", "command": "claude", "slots": 2, "use": "preferred", "capabilitiesVersion": 1,
            "capabilities": list(capabilities), "classes": {"medium": {"model": "m"}}, **extra}


class IntegrationCase(RoundCase):
    """A Plan of the dummy plan with one package; the machine settings name the providers."""

    providers = [provider("claude", "Coding", "Queen", "Build", "Deploy")]

    def hive(self, name="claude", status="IN_PROGRESS"):
        """Write the plan and create its Plan on provider `name`."""
        self.plan(status, [{"id": "P1", "title": "Work", "class": "medium"}])
        hives.create(self.conn, "testproj", REL, name, "123456")
        return self.env()

    def use(self, *providers, **project):
        """Replace the providers and write settings into project.toml."""
        self.machine["providers"] = list(providers)
        toml = self.repo / ".haivequeen" / "project.toml"
        lines = ['%s = %s' % (key, json.dumps(value)) for key, value in project.items()]
        toml.write_text(toml.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")


class ReadyAndReleaseGateTests(IntegrationCase):
    """Bzz ready, integration acquire and release enforce the steps in code."""

    def queen(self, cwd=None):
        """Register the running Queen of the Plan, with her worktree when `cwd` is given."""
        fields = {"status": "running", "phase": "queen", "plan_path": REL, "pid": 4242, "provider": "claude"}
        runtime.upsert_agent(self.conn, "testproj", "Ada", {**fields, "cwd": cwd} if cwd else fields)
        patcher = mock.patch.dict(os.environ, {"BZZ_AGENT": "Ada"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, fn, **values):
        """Run a command with the test machine settings; returns (exit code, printed JSON)."""
        buffer = io.StringIO()
        with mock.patch("haivequeen.config.load_config", return_value=self.machine), redirect_stdout(buffer):
            code = fn(argparse.Namespace(project="testproj", plan=REL, **values), self.conn)
        return code, json.loads(buffer.getvalue().strip().splitlines()[-1]) if buffer.getvalue().strip() else {}

    def ready(self):
        """Call bzz ready with an accepting ready gate."""
        with mock.patch("haivequeen.readygate.gate", return_value=(True, "", False)):
            return self.call(cli.cmd_ready, branch="b", tip="abc", note="")

    def milestones(self, *lines):
        """Write the milestones of the Queen's run."""
        folder = self.env().run_dir("Ada")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "milestones.txt").write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    def test_ready_is_not_held_by_a_build_package(self):
        """O-1: the Build is a step of the Orchestrator after ready, not of the Queen."""
        self.use(provider("claude", "Coding", "Queen"), provider("builder", "Build"))
        self.hive()
        self.queen()
        code, result = self.ready()
        self.assertEqual(code, 0, result)
        self.assertTrue(db.queue_rows(self.conn, "testproj"))

    def worktree(self):
        """A Git folder with one commit, used as the Queen's worktree; returns its path."""
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.commit(folder.name, "one")
        return folder.name

    def commit(self, folder, message):
        """Add an empty commit to the Git folder (the folder becomes a repository on the first call)."""
        subprocess.run(["git", "-C", folder, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message], check=True, capture_output=True)

    def checked(self, exit_code=0):
        """A Queen with a worktree whose compile check is a script that records where it ran and exits with `exit_code`; returns the worktree."""
        folder = self.worktree()
        Path(folder, "check.py").write_text("import os, sys\nopen('ran.txt', 'w').write(os.getcwd())\nsys.exit(%d)\n" % exit_code, encoding="utf-8")
        self.use(self.providers[0], compileCheck='"%s" check.py' % sys.executable)
        self.hive()
        self.queen(folder)
        return folder

    def test_ready_needs_no_compile_check_result(self):
        self.checked()
        code, result = self.ready()
        self.assertEqual(code, 0, result)
        self.assertIsNone(integration.compile_result(self.conn, "testproj", "Ada"))

    def test_compile_check_is_for_the_queen_of_the_hive_only(self):
        self.use(self.providers[0], compileCheck="npm test")
        self.hive()
        code, result = self.call(cli.cmd_compile_check)
        self.assertEqual(code, 1)
        self.assertIn("not this Plan's Queen", result["error"])

    def test_compile_check_has_a_command_line(self):
        args = cli.build_parser().parse_args(["compile-check", "--project", "p", "--plan", "x.md"])
        self.assertEqual((args.fn, args.project, args.plan), (cli.cmd_compile_check, "p", "x.md"))

    def test_ready_needs_no_compile_check_when_switched_off(self):
        self.use(self.providers[0], compileCheck="")
        self.hive()
        self.queen()
        self.assertEqual(self.ready()[0], 0)

    def test_acquire_reports_the_steps_and_skips_the_ram_wait_without_build_and_deploy(self):
        self.use(self.providers[0], compileCheck="")
        self.hive()
        self.queen()
        with mock.patch("haivequeen.buildjobs.waiting_for_ram", return_value="too little RAM"), mock.patch("haivequeen.platform.free_ram_mb", return_value=100.0):
            code, result = self.call(cli.cmd_integration, action="acquire", owner_pid=None)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["integrationSteps"], {"compileCheck": False, "build": False, "deploy": False})
        self.assertIsNone(result["buildJobs"])

    def test_acquire_waits_for_ram_when_a_build_command_is_set(self):
        self.use(self.providers[0], buildCommand="make")
        self.hive()
        self.queen()
        with mock.patch("haivequeen.buildjobs.waiting_for_ram", return_value="too little RAM"), mock.patch("haivequeen.platform.free_ram_mb", return_value=100.0):
            code, result = self.call(cli.cmd_integration, action="acquire", owner_pid=None)
        self.assertEqual(code, 1)
        self.assertIn("too little RAM", result["error"])

    def test_release_after_integration_is_not_held_by_a_deploy_package(self):
        """O-4: no step package exists any more; the Deploy command is a step of the Orchestrator."""
        self.use(provider("claude", "Coding", "Queen", "Build"), provider("shipper", "Deploy"), deployCommand="make deploy")
        self.hive()
        self.queen()
        self.assertEqual(self.ready()[0], 0)
        runtime.queue_upsert(self.conn, "testproj", "b", status="integrated")
        with mock.patch("haivequeen.worktrees.cleanup"):
            self.assertEqual(self.call(cli.cmd_integration, action="release")[0], 0)

    def test_release_before_integration_is_not_refused(self):
        self.use(provider("claude", "Coding", "Queen", "Build"), provider("shipper", "Deploy"))
        self.hive()
        self.queen()
        with mock.patch("haivequeen.worktrees.cleanup"):
            self.assertEqual(self.call(cli.cmd_integration, action="release")[0], 0)

    def test_build_wait_is_refused(self):
        self.use(self.providers[0])
        self.hive()
        self.queen()
        code, result = self.call(cli.cmd_package, package="P1", state="waiting", reason="build")
        self.assertEqual(code, 1)
        self.assertIn("Orchestrator", result["error"])


class VerifyBuildTests(IntegrationCase):
    """A package marked verify = "build" is closed like a code package: the build is the Build step after bzz ready (O-4)."""

    def test_verify_build_counts_as_code(self):
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "Work", "class": "medium"}])
        self.conn.execute("UPDATE packages SET verify='build'")
        self.assertEqual(self.env().packages(REL)[0]["verify"], "code")

    def test_verify_owner_stays(self):
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "Work", "class": "medium"}])
        self.conn.execute("UPDATE packages SET verify='owner'")
        self.assertEqual(self.env().packages(REL)[0]["verify"], "owner")


class StartTextTests(IntegrationCase):
    """The Queen start text holds no integration step: she calls bzz ready and ends (O-1)."""

    def text(self, name="claude"):
        return integration.start_text(self.env(), REL, name)

    def config(self):
        return integration.configuration(self.env(), REL)

    def test_the_text_ends_at_bzz_ready(self):
        self.use(self.providers[0], compileCheck="npm test", deployCommand="make deploy")
        self.hive()
        text = self.text()
        self.assertIn("bzz ready --project testproj --plan %s" % REL, text)
        for word in ("compile-check", "integration acquire", "integration release", "release worktree", "Deploy step", "Build step"):
            self.assertNotIn(word, text)

    def test_owner_topics_win_over_triage_and_empty_list_is_silent(self):
        self.use(self.providers[0])
        self.hive()
        self.assertEqual(integration.owner_topics_text(self.env()), "")
        self.use(self.providers[0], ownerTopics=["pricing"])
        text = integration.owner_topics_text(self.env())
        self.assertIn('"pricing"', text)
        self.assertIn("even a technical question", text)

    def test_configuration_holds_remote_branch_location_and_only_active_steps(self):
        self.use(self.providers[0], remoteName="upstream", integrationBranch="", worktreeLocation="D:/trees", compileCheck="",
                 buildCommand="make", deployCommand="make deploy")
        self.hive()
        config = self.config()
        self.assertEqual(config["remoteName"], "upstream")
        self.assertEqual(config["integrationBranch"], "main")
        self.assertEqual(config["worktreeLocation"], "D:/trees")
        for key in ("compileCheck", "buildCommand", "deployCommand"):
            self.assertNotIn(key, config)


class OwnerCheckTextTests(IntegrationCase):
    """The configured text is the question; the default follows the language; the Queen's report stays."""

    def test_default_follows_the_language(self):
        env = SimpleNamespace(name="p", settings={"language": "German"})
        with mock.patch.object(hives.runtime, "list_agents", return_value=[]), mock.patch.object(hives.db, "add_question", return_value=7) as add, mock.patch.object(hives, "wait_for_owner_check"):
            hives.file_owner_check(None, env, "plan.md")
        self.assertEqual(add.call_args.args[4], "Das integrierte Ergebnis prüfen")

    def test_report_of_the_queen_stays_below_the_default_text(self):
        env = self.hive()
        runtime.upsert_agent(self.conn, "testproj", "Ada", {"status": "done", "phase": "queen", "plan_path": REL})
        folder = env.run_dir("Ada")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "final.txt").write_text("All merged", encoding="utf-8")
        qid = hives.file_owner_check(self.conn, env, REL)
        text = next(q for q in runtime.list_questions(self.conn, "testproj") if q["id"] == qid)["question"]
        self.assertTrue(text.startswith("Check the integrated result"))
        self.assertIn("All merged", text)


class MigrationTests(unittest.TestCase):
    """At the change existing Projects and providers keep today's behaviour; new ones get the defaults."""

    @staticmethod
    def old(**extra):
        """A provider entry from before the ticks, read as the program reads it."""
        return scheduler.get_providers({"providers": [{"name": "p", "protocol": "claude", "slots": 1, "classes": {"medium": {"model": "m"}}, **extra}]})[0]

    def test_existing_provider_gains_no_capability_it_did_not_select(self):
        self.assertEqual(self.old(capabilities=["images"])["capabilities"], ["Coding", "Queen"])
        self.assertEqual(self.old(capabilities=["build"])["capabilities"], ["Build", "Coding", "Queen"])

    def test_existing_provider_gets_no_package_for_a_capability_it_did_not_select(self):
        selected, other = self.old(capabilities=["build"]), self.old(name="q", capabilities=["images"])
        self.assertEqual(bees.capable_providers([selected, other], ["Build"]), [selected])
        self.assertEqual(bees.capable_providers([selected, other], ["Deploy"]), [])
        self.assertEqual(bees.route_candidates([other], "q", ["Build"], False), [])
        self.assertIsNone(scheduler.select_provider([other], "medium", ["Build"], {"q": 1}))
        self.assertIs(scheduler.select_provider([selected, other], "medium", ["Build"], {"p": 1, "q": 1}), selected)
        self.assertFalse(scheduler.capability_available([selected, other], ["Deploy"]))

    def test_plan_may_not_declare_a_capability_only_the_old_rule_would_have_given(self):
        parsed = plans.Plan(path="x", title="x", status="READY", packages=[plans.Package(id="P1", title="Ship", capability="Deploy")])
        offered = {c for c in self.old(capabilities=["build"])["capabilities"]}
        self.assertTrue(any("Deploy" in problem for problem in plans.check_plan(parsed, offered)))

    def test_existing_provider_has_no_implicit_integration_steps(self):
        """No derived value lets the Queen of an old entry do a step she has no tick for."""
        self.assertNotIn("queenSteps", self.old())
        self.assertNotIn("Build", self.old()["capabilities"])
        self.assertNotIn("Deploy", self.old(capabilities=["build"])["capabilities"])

    def test_existing_endpoint_provider_gets_no_integration_step(self):
        old = self.old(endpoint={"baseUrl": "http://localhost"})
        self.assertNotIn("Build", old["capabilities"])
        self.assertNotIn("Deploy", old["capabilities"])

    def test_ticked_provider_keeps_exactly_its_ticks(self):
        ticked = scheduler.get_providers({"providers": [provider("p", "Coding", "Queen")]})[0]
        self.assertEqual(ticked["capabilities"], ["Coding", "Queen"])

    def test_new_apiary_file_has_the_new_defaults(self):
        from haivequeen import init
        with tempfile.TemporaryDirectory() as folder:
            init.init_project(folder)
            data = tomllib.loads((Path(folder) / ".haivequeen" / "project.toml").read_text(encoding="utf-8"))
        settings = scheduler.build_settings({}, data)
        self.assertEqual((settings["compileCheck"], settings["buildCommand"], settings["deployCommand"]), ("", "", ""))   # the example has no commands
        self.assertNotIn("buildEnabled", data)
        self.assertTrue(data["deleteFinishedWorktrees"])
        self.assertFalse(data["deleteIntegratedBranches"])
        self.assertNotIn("remoteName", data)
        self.assertEqual(data["ownerTopics"], [])


class CommandRecognitionTests(unittest.TestCase):
    """The configured command is recognised in any shell."""

    WINDOWS = "C:\\Tools\\Python\\python.exe -W ignore -m unittest discover -s tests-py -p test*.py"

    def assertRecognised(self, command, configured=WINDOWS):
        self.assertEqual((agent_runner.classify_cmd(command, compile_check=configured) or {}).get("label"), "Compile test", command)

    def assertNotRecognised(self, command, configured=WINDOWS):
        self.assertNotEqual((agent_runner.classify_cmd(command, compile_check=configured) or {}).get("label"), "Compile test", command)

    def test_adjacent_quoted_pieces_are_one_word(self):
        """Codex joins a PowerShell script from adjacent pieces ('a'"'b'"'c', one word in the shell); seen in a Queen run whose green check was not recognised."""
        command = r'''"C:\Tools\pwsh.exe" -Command '$env:LOCALAPPDATA = Join-Path $env:TEMP '"'hq-local'; "'$env:APPDATA = Join-Path $env:TEMP '"'hq-roaming'; Remove-Item Env:BZZ_AGENT -ErrorAction SilentlyContinue; & 'C:\Tools\Python\python.exe' -W ignore -m unittest discover -s tests-py -p test*.py"'''
        self.assertRecognised(command)
        self.assertNotRecognised(command.replace("unittest discover", "pytest"))
        self.assertNotRecognised(r'''"C:\Tools\pwsh.exe" -Command 'Write-Output '"'C:\Tools\Python\python.exe -W ignore -m unittest discover -s tests-py -p test*.py'"''')

    def test_doubled_backslashes_in_the_program_path(self):
        """Codex writes the Windows path of the program with every backslash doubled; it is the same program."""
        double = lambda text: text.replace("\\", "\\\\")   # the doubling as the Codex command shows it
        command = r'''"C:\Tools\pwsh.exe" -Command '$env:APPDATA = Join-Path $env:TEMP '"'hq-roaming'; & 'C:\Tools\Python\python.exe' -W ignore -m unittest discover -s tests-py -p test*.py"'''
        self.assertIn("C:\\\\Tools\\\\Python", double(command))
        self.assertRecognised(double(command))
        self.assertRecognised(double(r"C:\Tools\Python\python.exe -W ignore -m unittest discover -s tests-py -p test*.py"))
        self.assertNotRecognised(double(r"C:\Tools\Python\python.exe -W ignore -m pytest tests-py"))

    def test_plain_and_chained(self):
        self.assertRecognised(self.WINDOWS)
        self.assertRecognised("cd worktree && " + self.WINDOWS)
        self.assertRecognised("cd worktree; " + self.WINDOWS + " 2>&1 | tail -5")
        self.assertRecognised("$env:X='1'; " + self.WINDOWS)

    def test_powershell_call_operator_and_quotes(self):
        self.assertRecognised("& 'C:\\Tools\\Python\\python.exe' -W ignore -m unittest discover -s tests-py -p test*.py")
        self.assertRecognised('& "C:\\Tools\\Python\\python.exe" -W ignore -m unittest discover -s tests-py -p "test*.py"')

    def test_shell_wrappers(self):
        self.assertRecognised('pwsh -NoProfile -Command "' + self.WINDOWS + '"')
        self.assertRecognised("powershell.exe -c \"& 'C:\\Tools\\Python\\python.exe' -W ignore -m unittest discover -s tests-py -p test*.py\"")
        self.assertRecognised('cmd /c "' + self.WINDOWS + '"')
        self.assertRecognised("bash -lc 'npm test'", "npm test")
        self.assertRecognised("sh -c 'cd src && npm test'", "npm test")
        self.assertRecognised("/usr/bin/env FOO=1 npm test", "npm test")
        self.assertRecognised("FOO=1 npm test", "npm test")

    def test_script_run_by_a_shell(self):
        for command in ("pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1", "bash scripts/check.ps1", "powershell -File scripts\\check.ps1", "& ./scripts/check.ps1 -Fast"):
            configured = "./scripts/check.ps1" if command.startswith("&") else "scripts/check.ps1"
            self.assertRecognised(command, configured)
        self.assertNotRecognised("pwsh -File scripts/other.ps1", "scripts/check.ps1")

    def test_paths_in_any_form(self):
        self.assertRecognised("C:/Tools/Python/python.exe -W ignore -m unittest discover -s tests-py -p test*.py")
        self.assertRecognised("/c/Tools/Python/python.exe -W ignore -m unittest discover -s tests-py -p test*.py")
        self.assertRecognised("c:\\tools\\python\\PYTHON.EXE -W ignore -m unittest discover -s tests-py -p test*.py")

    def test_extra_arguments_and_commit_in_one_call(self):
        self.assertRecognised("npm test -- --ci", "npm test")
        self.assertRecognised("npm test && git commit -m 'merge'", "npm test")

    def test_text_is_no_command(self):
        for command in ("echo npm test", "grep 'npm test' file", "Select-String -Pattern 'npm test' f", "git commit -m 'npm test'", "bash -c 'echo npm test'", "python -c 'npm test'", "npm testing", "npm"):
            self.assertNotRecognised(command, "npm test")

    def test_other_arguments_are_another_command(self):
        self.assertNotRecognised("C:\\Tools\\Python\\python.exe -W ignore -m unittest tests-py.test_one")

    def test_configured_command_of_several_parts(self):
        self.assertRecognised("cd a && make && make test", "make && make test")
        self.assertNotRecognised("make", "make && make test")

    def test_runner_reads_the_command_from_the_merged_settings(self):
        self.assertEqual(agent_runner.configured_compile_check({"compileCheck": "make"}, {}), "make")
        self.assertEqual(agent_runner.configured_compile_check({"compileCheck": "make"}, {"compileCheck": "npm test"}), "npm test")
        self.assertEqual(agent_runner.configured_compile_check({}, {}), "")

    def opencode_run(self, folder, status, exit_code):
        """Feed a compile check of an OpenCode Queen to the runner and return the milestone lines."""
        run = agent_runner.AgentRun.__new__(agent_runner.AgentRun)
        run.ms_file, run.out, run.queue_re, run.compile_check = Path(folder) / "milestones.txt", io.StringIO(), None, "npm test"
        state = {"status": status, "input": {"command": "npm test"}, "output": "out", "metadata": {"exit": exit_code} if exit_code is not None else {}}
        run._opencode_event({"type": "tool_use", "part": {"type": "tool", "tool": "bash", "state": state}})
        return run.ms_file.read_text(encoding="utf-8").splitlines() if run.ms_file.exists() else []

    def test_opencode_queen_reports_her_compile_check(self):
        """Otherwise bzz ready would wait for ever for a check it cannot see."""
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(integration.COMPILE_MILESTONE.match(self.opencode_run(folder, "completed", 0)[-1]).group(1), "Green")
            self.assertEqual(integration.COMPILE_MILESTONE.match(self.opencode_run(folder, "completed", 1)[-1]).group(1), "Red")
            self.assertEqual(integration.COMPILE_MILESTONE.match(self.opencode_run(folder, "error", None)[-1]).group(1), "Red")

    def test_opencode_command_still_running_is_no_result(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.opencode_run(folder, "running", None), [])

    def test_without_a_configured_command_nothing_is_a_compile_test(self):
        self.assertNotRecognised("powershell -File compile-check-changed.ps1", "")
        self.assertNotRecognised("powershell -File compile-check-changed.ps1", None)


class RemoteAndLocationTests(unittest.TestCase):
    """Remote, integration branch fallback and worktree location."""

    def env(self, **settings):
        return SimpleNamespace(name="p", code_repo=Path("/work/repo"), settings=settings, run_dir=lambda name: Path("/runs") / name)

    def test_worktree_beside_the_repository_by_default(self):
        self.assertEqual(Path(routine.new_worktree(self.env(worktreePrefix="HQ"), "x")), Path("/work/HQ-x"))

    def test_worktree_location_absolute_and_relative(self):
        absolute = str(Path(tempfile.gettempdir()) / "trees")
        self.assertEqual(Path(routine.new_worktree(self.env(worktreePrefix="HQ", worktreeLocation=absolute), "x")), Path(absolute) / "HQ-x")
        self.assertEqual(Path(routine.new_worktree(self.env(worktreePrefix="HQ", worktreeLocation="trees"), "x")), Path("/work/repo/trees/HQ-x"))

    def test_template_values_fall_back(self):
        values = routine.base_values(self.env(integrationBranch="", remoteName=""))
        self.assertEqual(values["integrationBranch"], "main")
        self.assertEqual(values["remoteName"], "origin")

    def test_inbox_ref_follows_the_settings_and_has_the_main_fallback(self):
        self.assertEqual(inbox.integration_ref({"remoteName": "upstream", "integrationBranch": "develop"}), "upstream/develop")
        self.assertEqual(inbox.integration_ref({}), "origin/main")

    def test_project_page_refuses_a_remote_git_would_read_as_an_option(self):
        self.assertTrue(settings.check({"remoteName": "--upload-pack=x"}, machine=False))
        self.assertTrue(settings.check({"integrationBranch": "a b"}, machine=False))
        self.assertEqual(settings.check({"remoteName": "upstream", "integrationBranch": "release/1.0"}, machine=False), [])


class SettingsPageTests(unittest.TestCase):
    """The Project page shows every setting with its default and a translated label."""

    def test_defaults_of_the_apiary_page(self):
        shown = settings.apiary_settings({})
        self.assertEqual(shown["integrationBranch"], "main")
        self.assertEqual(shown["remoteName"], "origin")
        self.assertEqual(shown["worktreeLocation"], "")
        self.assertEqual(shown["ownerTopics"], [])
        self.assertEqual(shown["ownerCheckText"], "")
        for key in ("compileCheckEnabled", "buildEnabled", "deployEnabled"):
            self.assertNotIn(key, shown)
        self.assertIs(shown["deleteFinishedWorktrees"], True)
        self.assertIs(shown["deleteIntegratedBranches"], False)

    def test_saved_values_stay(self):
        shown = settings.apiary_settings({"deployCommand": "make deploy", "remoteName": "upstream"})
        self.assertEqual(shown["deployCommand"], "make deploy")
        self.assertEqual(shown["remoteName"], "upstream")

    def test_labels_are_translated(self):
        from haivequeen import i18n
        for label in settings.SETTING_LABELS.values():
            self.assertIn(label, i18n.GERMAN, label)


class BranchCleanupTests(RoundCase):
    """The program deletes integrated plan and sub-branches, local and remote, only when that loses nothing."""

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", "-C", str(cwd or self.clone), *args], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def setUp(self):
        super().setUp()
        self.remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], capture_output=True, check=True)
        self.clone = self.base / "clone"
        subprocess.run(["git", "clone", str(self.remote), str(self.clone)], capture_output=True, check=True)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("checkout", "-b", "main")
        self.commit("base")
        self.git("push", "origin", "main")
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + 'codeRepo = %s\ndeleteIntegratedBranches = true\n' % json.dumps(str(self.clone)), encoding="utf-8")
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "Work", "class": "medium"}])
        self.git("checkout", "-b", "plan-x")
        self.commit("plan work")
        self.git("branch", "plan-x-k1", "HEAD~0")
        self.git("push", "origin", "plan-x", "plan-x-k1")
        self.tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        self.git("merge", "--ff-only", "plan-x")
        self.git("push", "origin", "main")
        hive = hives.create(self.conn, "testproj", REL, "claude", "123456")
        ready = db.add_ready(self.conn, "testproj", "plan-x", self.tip, REL, "")
        self.conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (ready["id"], "testproj", REL))
        runtime.queue_upsert(self.conn, "testproj", "plan-x", status="integrated")
        self.assertIsNotNone(hive)

    def commit(self, name):
        (self.clone / (name.replace(" ", "-") + ".txt")).write_text(name, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", name)

    def local(self):
        return set(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split())

    def on_remote(self):
        return {line.split("refs/heads/")[1] for line in self.git("ls-remote", "--heads", "origin").splitlines()}

    def run_cleanup(self, env=None):
        return worktrees.delete_integrated_branches(env or self.env())

    def test_switch_off_deletes_nothing(self):
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("deleteIntegratedBranches = true", "deleteIntegratedBranches = false"), encoding="utf-8")
        with mock.patch.object(worktrees, "prune", return_value={}):
            worktrees.cleanup(self.env())
        self.assertIn("plan-x", self.local())
        self.assertIn("plan-x", self.on_remote())

    def test_plan_and_sub_branches_go_local_and_remote(self):
        result = self.run_cleanup()
        self.assertEqual(sorted(result["deleted"]), ["local plan-x", "local plan-x-k1", "remote plan-x", "remote plan-x-k1"])
        self.assertEqual(self.local(), {"main"})
        self.assertEqual(self.on_remote(), {"main"})

    def test_cleanup_uses_the_configured_remote(self):
        self.git("remote", "rename", "origin", "upstream")
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + 'remoteName = "upstream"\n', encoding="utf-8")
        self.run_cleanup()
        self.assertEqual({line.split("refs/heads/")[1] for line in self.git("ls-remote", "--heads", "upstream").splitlines()}, {"main"})

    def test_unmerged_work_stays(self):
        self.git("checkout", "plan-x-k1")
        self.commit("late work")
        self.git("push", "origin", "plan-x-k1")
        self.git("checkout", "main")
        result = self.run_cleanup()
        self.assertIn("plan-x-k1", self.local())
        self.assertIn("plan-x-k1", self.on_remote())
        self.assertIn("plan-x-k1", [row["branch"] for row in result["kept"]])
        self.assertNotIn("plan-x", self.local())

    def test_remote_only_work_stays(self):
        other = self.base / "other"
        subprocess.run(["git", "clone", "-b", "plan-x-k1", str(self.remote), str(other)], capture_output=True, check=True)
        self.git("config", "user.email", "t@example.com", cwd=other)
        self.git("config", "user.name", "t", cwd=other)
        (other / "elsewhere.txt").write_text("x", encoding="utf-8")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "elsewhere", cwd=other)
        self.git("push", "origin", "plan-x-k1", cwd=other)
        self.run_cleanup()
        self.assertIn("plan-x-k1", self.on_remote())

    def test_integration_branch_and_similar_names_stay(self):
        self.git("branch", "plan-xy", self.tip)
        self.git("branch", "main-old", self.tip)
        self.run_cleanup()
        self.assertIn("main", self.local())
        self.assertIn("plan-xy", self.local())
        self.assertIn("main-old", self.local())

    def test_integration_branch_named_like_a_sub_branch_stays(self):
        self.git("branch", "plan-x-release", self.tip)
        self.git("push", "origin", "plan-x-release")
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + 'integrationBranch = "plan-x-release"\n', encoding="utf-8")
        self.run_cleanup()
        self.assertIn("plan-x-release", self.local())
        self.assertIn("plan-x-release", self.on_remote())

    def test_checked_out_branch_stays(self):
        path = self.base / "tree"
        self.git("worktree", "add", str(path), "plan-x")
        result = self.run_cleanup()
        self.assertIn("plan-x", self.local())
        self.assertIn("plan-x", [row["branch"] for row in result["kept"]])

    def test_branch_of_another_open_plan_stays(self):
        self.git("branch", "plan-x-other", self.tip)
        with mock.patch.object(worktrees, "_open_plan_branches", return_value={"plan-x-other"}):
            self.run_cleanup()
        self.assertIn("plan-x-other", self.local())

    def test_open_queue_row_keeps_the_branch(self):
        db.add_ready(self.conn, "testproj", "plan-x-k1", self.tip, "other.md", "")
        self.run_cleanup()
        self.assertIn("plan-x-k1", self.local())
        self.assertNotIn("plan-x", self.local())

    def test_reopened_plan_keeps_its_branch(self):
        runtime.queue_upsert(self.conn, "testproj", "plan-x", status="ready")
        self.run_cleanup()
        self.assertIn("plan-x", self.local())

    def test_running_agent_of_the_plan_keeps_the_branches(self):
        runtime.upsert_agent(self.conn, "testproj", "Ada", {"status": "running", "phase": "queen", "plan_path": REL})
        self.run_cleanup()
        self.assertIn("plan-x", self.local())

    def test_plan_tip_not_in_the_integration_branch_stays(self):
        self.git("checkout", "-B", "main", "HEAD~1")
        self.git("push", "--force", "origin", "main")
        self.run_cleanup()
        self.assertIn("plan-x", self.local())

    def test_hive_without_ready_link_keeps_the_branch_for_recovery(self):
        self.conn.execute("UPDATE hives SET ready_id=NULL")
        self.run_cleanup()
        self.assertIn("plan-x", self.local())

    def test_failed_fetch_deletes_nothing(self):
        self.git("remote", "set-url", "origin", str(self.base / "gone.git"))
        result = self.run_cleanup()
        self.assertIn("plan-x", self.local())
        self.assertTrue(result["kept"])

    def test_cleanup_runs_both_switches_independently(self):
        with mock.patch.object(worktrees, "prune", return_value={}) as prune, mock.patch.object(worktrees, "delete_integrated_branches", return_value={}) as delete:
            worktrees.cleanup(SimpleNamespace(settings={"deleteFinishedWorktrees": False, "deleteIntegratedBranches": True}, log=lambda text: None))
            prune.assert_not_called()
            delete.assert_called_once()
            delete.reset_mock()
            worktrees.cleanup(SimpleNamespace(settings={"deleteFinishedWorktrees": True, "deleteIntegratedBranches": False}, log=lambda text: None))
            prune.assert_called_once()
            delete.assert_not_called()

    def test_a_failure_of_the_worktree_step_still_tries_the_branches(self):
        logs = []
        with mock.patch.object(worktrees, "prune", side_effect=OSError("busy")), mock.patch.object(worktrees, "delete_integrated_branches", return_value={}) as delete:
            worktrees.cleanup(SimpleNamespace(settings={"deleteFinishedWorktrees": True, "deleteIntegratedBranches": True}, log=logs.append))
        delete.assert_called_once()
        self.assertTrue(any("failed" in line for line in logs))


class CleanupLogTests(unittest.TestCase):
    """The result of the branch cleanup reads in German in the log of the window."""

    def test_branch_cleanup_line_is_german(self):
        from haivequeen import i18n
        line = 'Branch cleanup: ' + json.dumps({"deleted": ["local plan-x"], "kept": [{"branch": "plan-x-k1", "where": "remote", "reason": "has commits that are not in the integrated tip"}]})
        self.assertEqual(i18n.log_text(line, language="German"), "Branch-Bereinigung: gelöscht: lokal plan-x; behalten: Remote plan-x-k1 (hat Commits, die nicht im integrierten Stand sind)")


class ProgramPushRuleTests(unittest.TestCase):
    """The remote branch is deleted by the program with a lease, so a moved branch is never removed."""

    def test_remote_deletion_uses_a_lease_on_the_checked_commit(self):
        with mock.patch.object(worktrees.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            worktrees._delete_remote("repo", "upstream", "plan-x", "abc123")
        argv = run.call_args.args[0]
        self.assertIn("--force-with-lease=refs/heads/plan-x:abc123", argv)
        self.assertEqual(argv[-2:], ["upstream", ":refs/heads/plan-x"])
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")


if __name__ == "__main__":
    unittest.main()
