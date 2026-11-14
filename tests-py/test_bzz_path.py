"""Tests of the PATH handling for agent processes: the folder of the `bzz` console script is put in front of PATH (no real models)."""
import os
import shutil
import subprocess
import stat
import sys
import tempfile
import time
import unittest
import venv
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bzztest import REL, RoundCase, runtime  # noqa: E402
from haivequeen import agent_runner as ar  # noqa: E402
from haivequeen import loop  # noqa: E402
from haivequeen import platform as osplat  # noqa: E402


class BzzPathTest(unittest.TestCase):
    """with_bzz_on_path."""

    def setUp(self):
        """Create a scripts folder with a bzz program and an empty folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.scripts = Path(self.tmp.name) / "scripts"
        self.scripts.mkdir()
        (self.scripts / "bzz.exe").write_text("x", encoding="utf-8")
        self.empty = Path(self.tmp.name) / "empty"
        self.empty.mkdir()

    def tearDown(self):
        """Remove the folders."""
        self.tmp.cleanup()

    def test_scripts_folder_goes_first(self):
        """A folder with a bzz program is put in front of PATH."""
        env = {"PATH": os.pathsep.join(["/a", "/b"])}
        ar.with_bzz_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].split(os.pathsep), [str(self.scripts), "/a", "/b"])

    def test_no_duplicate(self):
        """A folder that is already on PATH is not added again."""
        env = {"PATH": os.pathsep.join(["/a", str(self.scripts)])}
        ar.with_bzz_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].split(os.pathsep), ["/a", str(self.scripts)])
        ar.with_bzz_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].count(str(self.scripts)), 1)

    def test_no_entry_without_orch_program(self):
        """A folder without a bzz program is not added."""
        env = {"PATH": "/a"}
        ar.with_bzz_on_path(env, [self.empty, self.empty / "missing"])
        self.assertEqual(env["PATH"], "/a")

    def test_missing_path_variable(self):
        """Without any PATH the folder alone becomes PATH."""
        env = {}
        ar.with_bzz_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"], str(self.scripts) + os.pathsep)

    def test_candidates_include_python_scripts(self):
        """The folders looked at include the Scripts folder next to the running Python."""
        self.assertIn(Path(sys.executable).parent / "Scripts", ar.bzz_script_dirs())

    def test_installed_agent_bzz_stays_on_its_version_after_switch(self):
        root = Path(self.tmp.name)
        old = root / 'versions' / 'old'
        new = root / 'versions' / 'new'
        for version in (old, new):
            package = version / 'haivequeen'
            package.mkdir(parents=True)
            (package / 'cli.py').write_text('import pathlib\nprint(pathlib.Path(__file__).parents[1].name)\n', encoding='utf-8')
        pointer = root / 'active-version'
        pointer.write_text('old', encoding='utf-8')
        env = dict(os.environ)
        with mock.patch.object(ar, 'TOOL_ROOT', old):
            ar.with_version_bzz(env, root / 'run')
        pointer.write_text('new', encoding='utf-8')
        command = ['cmd', '/c', 'bzz'] if os.name == 'nt' else ['bzz']
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'old')



# Fake provider CLI: looks up `bzz` on its own PATH like an agent would and calls `bzz note`, then prints a Claude stream-json result.
FAKE_CLI = r'''import json, os, shutil, subprocess
found = shutil.which("bzz")
with open(os.environ["FAKE_FOUND"], "w", encoding="utf-8") as f:
    f.write(found or "")
if found:
    subprocess.run([found, "note", "hello"], check=False)
for ev in ({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [{"type": "text", "text": "working"}]}},
           {"type": "result", "is_error": False, "result": "Done.", "usage": {"input_tokens": 1, "output_tokens": 1}}):
    print(json.dumps(ev))
'''


class AgentFindsOrchTest(RoundCase):
    """An agent started by loop.launch_headless (fake CLI) finds `bzz` although its folder is not on PATH."""

    def setUp(self):
        """Venv without pip as the running Python (its Scripts folder holds a fake bzz), fake CLI as provider, PATH without any bzz."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        venv_dir = self.base / "venv"
        venv.create(str(venv_dir), with_pip=False)
        win = sys.platform == "win32"
        self.scripts = venv_dir / ("Scripts" if win else "bin")
        self.python = str(self.scripts / ("python.exe" if win else "python"))
        self.mark = self.base / "bzz-called.txt"
        if win:
            (self.scripts / "bzz.cmd").write_text('@echo %* > "%FAKE_MARK%"\r\n', encoding="utf-8")
        else:
            bzz = self.scripts / "bzz"
            bzz.write_text('#!/bin/sh\necho "$@" > "$FAKE_MARK"\n', encoding="utf-8")
            bzz.chmod(bzz.stat().st_mode | stat.S_IEXEC)
        script = self.base / "fake_cli.py"
        script.write_text(FAKE_CLI, encoding="utf-8")
        if win:
            cmd = self.base / "fake-claude.cmd"
            cmd.write_text('@"%s" "%s" %%*\r\n' % (self.python, script), encoding="utf-8")
        else:
            cmd = self.base / "fake-claude.sh"
            cmd.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (self.python, script), encoding="utf-8")
            cmd.chmod(cmd.stat().st_mode | stat.S_IEXEC)
        self.found = self.base / "found.txt"
        self.saved_env = {k: os.environ.get(k) for k in ("PATH", "FAKE_MARK", "FAKE_FOUND")}
        clean = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                 if p and not any((Path(p) / n).is_file() for n in ("bzz", "bzz.exe", "bzz.cmd", "bzz.bat"))]
        os.environ.update({"PATH": os.pathsep.join(clean), "FAKE_MARK": str(self.mark), "FAKE_FOUND": str(self.found)})
        cfg = self.base / "cfg"
        cfg.mkdir(exist_ok=True)
        (cfg / "config.toml").write_text(
            "[[providers]]\nname = 'claude'\ncommand = '%s'\nargs = []\n[providers.classes.medium]\nmodel = 'claude-sonnet-5-5'\neffort = 'medium'\n"
            % str(cmd).replace(chr(92), "/"), encoding="utf-8")

    def tearDown(self):
        """End the runner this test started, then restore PATH and the fake-CLI variables."""
        for a in runtime.list_agents(self.conn, "testproj", "all"):
            if a.get("pid") and a["pid"] != os.getpid() and osplat.process_alive(a["pid"]):
                osplat.kill_process_tree(a["pid"])
                time.sleep(1)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_pane_agent_finds_orch(self):
        """The agent started through launch_headless finds the fake bzz in the interpreter's Scripts folder and `bzz note` runs."""
        prompt = self.base / "prompt.md"
        prompt.write_text("# Header\nTASK\nDo the demo task now\n", encoding="utf-8")
        runtime.upsert_agent(self.conn, "testproj", "A-1", {"status": "starting", "class": "medium", "person": "Max", "color": "4FA8FF"})
        spec = {"name": "A-1", "provider": "claude", "protocol": "claude", "plan": REL, "package": "P1", "phase": "work", "cwd": str(self.repo),
                "prompt_file": str(prompt)}
        self.env().run_dir("A-1").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(osplat, "python_executable", return_value=self.python):
            loop.launch_headless(self.env(), spec)
            deadline = time.time() + 60
            while time.time() < deadline and not self.mark.is_file():
                time.sleep(0.2)
        self.assertTrue(self.found.is_file(), "the fake CLI did not run")
        found = self.found.read_text(encoding="utf-8").strip()
        self.assertTrue(found and Path(found).parent == self.scripts, "bzz found at %r, expected folder %s" % (found, self.scripts))
        self.assertIn("note", self.mark.read_text(encoding="utf-8"))

    def test_running_agent_keeps_version_bound_bzz_after_pointer_switch(self):
        """A real runner and provider retain the old CLI after activation changes."""
        root = self.base / "install"
        old = root / "versions" / "old"
        new = root / "versions" / "new"
        for version in (old, new):
            shutil.copytree(ROOT / "haivequeen", version / "haivequeen", ignore=shutil.ignore_patterns("__pycache__"))
        pointer = root / "active-version"
        pointer.write_text("old", encoding="utf-8")
        gate = self.base / "gate"
        marker = self.base / "version-result"
        script = self.base / "fake_cli.py"
        script.write_text(
            'import json, os, pathlib, subprocess, time\n'
            'gate = pathlib.Path(os.environ["FAKE_MARK"])\n'
            'pathlib.Path(os.environ["FAKE_FOUND"]).write_text("running")\n'
            'deadline = time.monotonic() + 45\n'
            'while not gate.exists() and time.monotonic() < deadline: time.sleep(.05)\n'
            'cli = os.environ.get("BZZ_AGENT_CLI", "")\n'
            'result = subprocess.run("bzz --help", shell=True, capture_output=True)\n'
            'pathlib.Path(os.environ["VERSION_RESULT"]).write_text(cli + "\\n" + str(result.returncode))\n'
            'print(json.dumps({"type":"result", "is_error":False, "result":"Done."}))\n', encoding="utf-8")
        os.environ["FAKE_MARK"] = str(gate)
        os.environ["VERSION_RESULT"] = str(marker)
        self.addCleanup(os.environ.pop, "VERSION_RESULT", None)
        prompt = self.base / "prompt.md"
        prompt.write_text("TASK\nTest\n", encoding="utf-8")
        runtime.upsert_agent(self.conn, "testproj", "A-1", {"status": "starting", "class": "medium", "person": "Test", "color": "4FA8FF"})
        spec = {"name": "A-1", "provider": "claude", "protocol": "claude", "plan": REL,
                "package": "P1", "phase": "work", "cwd": str(self.repo), "prompt_file": str(prompt)}
        self.env().run_dir("A-1").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(loop, "TOOLS_DIR", old):
            loop.launch_headless(self.env(), spec)
            deadline = time.monotonic() + 45
            while not self.found.exists() and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertTrue(self.found.exists(), "provider did not start")
            pointer.write_text("new", encoding="utf-8")
            gate.touch()
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.1)
        self.assertTrue(marker.exists(), "provider did not continue after switch")
        cli, code = marker.read_text(encoding="utf-8").splitlines()
        self.assertEqual(Path(cli), old / "haivequeen" / "cli.py")
        self.assertEqual(code, "0")

    def test_queen_started_by_new_version_uses_new_version_cli(self):
        root = self.base / "install"
        new = root / "versions" / "new"
        shutil.copytree(ROOT / "haivequeen", new / "haivequeen", ignore=shutil.ignore_patterns("__pycache__"))
        (root / "active-version").write_text("new", encoding="utf-8")
        marker = self.base / "queen-version"
        script = self.base / "fake_cli.py"
        script.write_text(
            'import json, os, pathlib\n'
            'pathlib.Path(os.environ["QUEEN_RESULT"]).write_text(os.environ.get("BZZ_AGENT_CLI", ""))\n'
            'print(json.dumps({"type":"result", "is_error":False, "result":"Done."}))\n', encoding="utf-8")
        os.environ["QUEEN_RESULT"] = str(marker)
        self.addCleanup(os.environ.pop, "QUEEN_RESULT", None)
        prompt = self.base / "prompt.md"
        prompt.write_text("TASK\nTest\n", encoding="utf-8")
        runtime.upsert_agent(self.conn, "testproj", "Q-1", {"status": "starting", "class": "medium", "person": "Test", "color": "4FA8FF"})
        spec = {"name": "Q-1", "provider": "claude", "protocol": "claude", "plan": REL,
                "package": "", "phase": "queen", "cwd": str(self.repo), "prompt_file": str(prompt)}
        self.env().run_dir("Q-1").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(loop, "TOOLS_DIR", new):
            loop.launch_headless(self.env(), spec)
            deadline = time.monotonic() + 45
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.1)
        self.assertTrue(marker.exists(), "provider did not start")
        self.assertEqual(Path(marker.read_text(encoding="utf-8")), new / "haivequeen" / "cli.py")


    def test_old_queen_starts_version_bound_agent_after_activation(self):
        """A real old-version Queen calls its CLI after activation and starts an old-version Agent."""
        import json
        from haivequeen import hives, version_layout
        root = self.base / "install"
        old, new = root / "versions" / "old", root / "versions" / "new"
        for version in (old, new):
            shutil.copytree(ROOT / "haivequeen", version / "haivequeen", ignore=shutil.ignore_patterns("__pycache__"))
        for version in (old, new):
            shutil.copytree(ROOT / "roles", version / "roles")
        pointer = root / "active-version"
        pointer.write_text("old", encoding="utf-8")
        version_layout.install_launcher(root)
        core_script = self.base / "core.py"
        core_script.write_text(
            'import os, sys, time\n'
            'from pathlib import Path\n'
            'from unittest.mock import patch\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'from haivequeen import config, db, loop, proctree, version_leases\n'
            'base=Path(os.environ["FAKE_MARK"]).parent\n'
            'version=Path(sys.argv[1]).name\n'
            'conn=db.connect(config.db_path())\n'
            'def round_(*args, **kwargs):\n'
            ' if version == "old":\n'
            '  (base / "old-core-round").touch()\n'
            '  while not (base / "release-core").exists(): time.sleep(.05)\n'
            ' else:\n'
            '  rows=conn.execute("SELECT name, status FROM agents ORDER BY name").fetchall()\n'
            '  (base / "new-core-seen").write_text(repr([tuple(row) for row in rows]))\n'
            'with patch.object(loop, "run_round", side_effect=round_), patch.object(loop, "daily_export"), patch.object(proctree, "sweep"), patch.object(version_leases, "collect", return_value=[]), patch.object(loop, "active_count", return_value=0):\n'
            ' loop.run_loop(conn, {"providers": [], "projects": [], "roundMinutes": .01}, rounds=1)\n'
            'conn.close()\n', encoding="utf-8")
        (new / "haivequeen" / "cli.py").write_text(
            'import runpy, sys\n'
            'sys.argv=[%r, %r]\n'
            'runpy.run_path(%r, run_name="__main__")\n' % (str(core_script), str(new), str(core_script)), encoding="utf-8")
        def git(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, timeout=30)
        git("init", "-b", "main")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "initial")
        git("branch", "feature/test")
        self.plan("RUNNING", [{"id": "P1", "class": "medium"}], branch="feature/test", worktree=str(self.base / "plan"))
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        runtime.upsert_agent(self.conn, "testproj", "Q-1", {"status": "starting", "phase": "queen", "provider": "claude", "plan_path": REL, "cwd": str(self.base / "plan"), "person": "Queen"})
        gate, queen_result, bee_result = (self.base / n for n in ("gate", "queen-result", "bee-result"))
        script = self.base / "fake_cli.py"
        script.write_text(
            'import json, os, pathlib, subprocess, time\n'
            'base=pathlib.Path(os.environ["FAKE_MARK"]).parent\n'
            'if os.environ["BZZ_AGENT"] == "Q-1":\n'
            ' pathlib.Path(os.environ["FAKE_FOUND"]).touch()\n'
            ' while not (base / "gate").exists(): time.sleep(.05)\n'
            ' args=[os.environ["BZZ_AGENT_PYTHON"], "-I", os.environ["BZZ_AGENT_CLI"], "agent", "start", "--project", "testproj", "--plan", os.environ["TEST_PLAN"], "--package", "P1", "--class", "medium", "--name", "Bee", "--task", "work", "--steps", json.dumps(["work"]), "--no-subagents"]\n'
            ' result=subprocess.run(args, capture_output=True, text=True)\n'
            ' (base / "queen-result").write_text(json.dumps([result.returncode, result.stdout, result.stderr]))\n'
            'elif os.environ["BZZ_AGENT"] == "Existing":\n'
            ' (base / "existing-started").touch()\n'
            ' while not (base / "gate").exists(): time.sleep(.05)\n'
            ' result=subprocess.run([os.environ["BZZ_AGENT_PYTHON"], "-I", os.environ["BZZ_AGENT_CLI"], "--help"], capture_output=True)\n'
            ' (base / "existing-result").write_text(os.environ["BZZ_AGENT_CLI"] + "\\n" + str(result.returncode))\n'
            'else: (base / "bee-result").write_text(os.environ.get("BZZ_AGENT_CLI", ""))\n'
            'while not (base / "release-jobs").exists(): time.sleep(.05)\n'
            'print(json.dumps({"type":"result", "is_error":False, "result":"Done."}))\n', encoding="utf-8")
        os.environ["TEST_PLAN"] = REL
        self.addCleanup(os.environ.pop, "TEST_PLAN", None)
        # The saved provider is read independently by the old CLI process.
        cmd = self.base / ("fake-claude.cmd" if os.name == "nt" else "fake-claude.sh")
        cfg = self.base / "cfg" / "config.toml"
        cfg.write_text("slots = 4\nramReserveMb = 0\n[[providers]]\nname = 'claude'\nprotocol = 'claude'\ncommand = '%s'\nargs = []\nslots = 4\nbeeMode = 'process'\ncapabilities = ['Coding']\n[providers.classes.medium]\nmodel = 'test'\neffort = 'medium'\n" % str(cmd).replace(chr(92), "/"), encoding="utf-8")
        prompt = self.base / "prompt.md"
        prompt.write_text("TASK\nTest\n", encoding="utf-8")
        spec = {"name": "Q-1", "provider": "claude", "protocol": "claude", "plan": REL, "package": "", "phase": "queen", "cwd": str(self.repo), "prompt_file": str(prompt)}
        self.env().run_dir("Q-1").mkdir(parents=True, exist_ok=True)
        core = subprocess.Popen([sys.executable, str(core_script), str(old)], env=dict(os.environ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def cleanup_core():
            if self.base.exists():
                (self.base / "release-core").touch()
                (self.base / "release-jobs").touch()
            if core.poll() is None:
                core.terminate()
                core.wait(timeout=10)
        self.addCleanup(cleanup_core)
        deadline = time.monotonic() + 60
        while not (self.base / "old-core-round").exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue((self.base / "old-core-round").exists(), "old core did not start")
        with mock.patch.object(loop, "TOOLS_DIR", old):
            loop.launch_headless(self.env(), spec)
            runtime.upsert_agent(self.conn, "testproj", "Existing", {"status": "starting", "phase": "bee", "provider": "claude", "plan_path": REL, "person": "Existing"})
            existing = dict(spec, name="Existing", phase="bee", package="P1")
            self.env().run_dir("Existing").mkdir(parents=True, exist_ok=True)
            loop.launch_headless(self.env(), existing)
        while not self.found.exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue(self.found.exists(), "Queen did not start")
        while not (self.base / "existing-started").exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue((self.base / "existing-started").exists(), "existing Agent did not start before activation")
        pointer.write_text("new", encoding="utf-8")
        gate.touch()
        while not queen_result.exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue(queen_result.exists(), "Queen did not call agent start")
        code, stdout, stderr = json.loads(queen_result.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, stdout + stderr)
        result = json.loads(stdout)
        self.assertTrue(result.get("started"), result)
        while not bee_result.exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue(bee_result.exists(), "Agent did not start")
        self.assertEqual(Path(bee_result.read_text(encoding="utf-8")), old / "haivequeen" / "cli.py")
        while not (self.base / "existing-result").exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue((self.base / "existing-result").exists(), "existing Agent did not continue after activation")
        cli, code = (self.base / "existing-result").read_text(encoding="utf-8").splitlines()
        self.assertEqual(Path(cli), old / "haivequeen" / "cli.py")
        self.assertEqual(code, "0")
        self.assertIsNone(core.poll(), "old core ended before its round was released")
        (self.base / "release-core").touch()
        core.wait(timeout=30)
        self.assertEqual(core.returncode, 0)
        while not (self.base / "new-core-seen").exists() and time.monotonic() < deadline: time.sleep(.1)
        self.assertTrue((self.base / "new-core-seen").exists(), "new core did not run")
        seen = (self.base / "new-core-seen").read_text(encoding="utf-8")
        self.assertIn("('Q-1', 'running')", seen)
        self.assertIn("('Bee', 'running')", seen)
        self.assertIn("('Existing', 'running')", seen)
        (self.base / "release-jobs").touch()

if __name__ == "__main__":
    unittest.main()
