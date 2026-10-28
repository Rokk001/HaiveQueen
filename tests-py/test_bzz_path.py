"""Tests of the PATH handling for agent processes: the folder of the `bzz` console script is put in front of PATH (no real models, no panes)."""
import os
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
from haivequeen.terminal.headless import Headless  # noqa: E402


class BzzPathTest(unittest.TestCase):
    """with_bzz_on_path and child_env."""

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

    def test_child_env(self):
        """child_env starts PATH with the scripts folder and keeps PYTHONPATH."""
        with mock.patch.object(ar, "bzz_script_dirs", return_value=[self.scripts]), \
                mock.patch.dict(os.environ, {"PATH": os.pathsep.join(["/a", "/b"])}):
            env = ar.child_env()
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(self.scripts))
        self.assertIn(str(ar.TOOL_ROOT), env["PYTHONPATH"])

    def test_candidates_include_python_scripts(self):
        """The folders looked at include the Scripts folder next to the running Python."""
        self.assertIn(Path(sys.executable).parent / "Scripts", ar.bzz_script_dirs())



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
    """A pane started by loop.launch_agent (headless backend, fake CLI) finds `bzz` although its folder is not on PATH."""

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
        self.term = Headless(self.base / "panes")

    def tearDown(self):
        """Close leftover panes and restore PATH and the fake-CLI variables."""
        for p in self.term.list_panes("Orchestrator"):
            if p.alive:
                self.term.close_pane(p.id)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_pane_agent_finds_orch(self):
        """The agent started through launch_agent finds the fake bzz in the interpreter's Scripts folder and `bzz note` runs."""
        prompt = self.base / "prompt.md"
        prompt.write_text("# Header\nTASK\nDo the demo task now\n", encoding="utf-8")
        runtime.upsert_agent(self.conn, "testproj", "A-1", {"status": "starting", "class": "medium", "person": "Max", "color": "4FA8FF"})
        spec = {"name": "A-1", "provider": "claude", "protocol": "claude", "plan": REL, "package": "P1", "phase": "work", "cwd": str(self.repo),
                "prompt_file": str(prompt), "person": "Max", "model_long": "fake"}
        with mock.patch.object(osplat, "python_executable", return_value=self.python), mock.patch.dict(os.environ, {"BZZ_NO_HEADER": "1"}):
            loop.launch_agent(self.env(), self.term, "Orchestrator", spec, settle=0, poll=0.2, tries=150)
            deadline = time.time() + 60
            while time.time() < deadline and not self.mark.is_file():
                time.sleep(0.2)
        self.assertTrue(self.found.is_file(), "the fake CLI did not run")
        found = self.found.read_text(encoding="utf-8").strip()
        self.assertTrue(found and Path(found).parent == self.scripts, "bzz found at %r, expected folder %s" % (found, self.scripts))
        self.assertIn("note", self.mark.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
