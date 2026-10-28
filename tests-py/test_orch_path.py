"""Tests of the PATH handling for agent processes: the folder of the `orch` console script is put in front of PATH (no real models, no panes)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import agent_runner as ar  # noqa: E402


class OrchPathTest(unittest.TestCase):
    """with_orch_on_path and child_env."""

    def setUp(self):
        """Create a scripts folder with an orch program and an empty folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.scripts = Path(self.tmp.name) / "scripts"
        self.scripts.mkdir()
        (self.scripts / "orch.exe").write_text("x", encoding="utf-8")
        self.empty = Path(self.tmp.name) / "empty"
        self.empty.mkdir()

    def tearDown(self):
        """Remove the folders."""
        self.tmp.cleanup()

    def test_scripts_folder_goes_first(self):
        """A folder with an orch program is put in front of PATH."""
        env = {"PATH": os.pathsep.join(["/a", "/b"])}
        ar.with_orch_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].split(os.pathsep), [str(self.scripts), "/a", "/b"])

    def test_no_duplicate(self):
        """A folder that is already on PATH is not added again."""
        env = {"PATH": os.pathsep.join(["/a", str(self.scripts)])}
        ar.with_orch_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].split(os.pathsep), ["/a", str(self.scripts)])
        ar.with_orch_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"].count(str(self.scripts)), 1)

    def test_no_entry_without_orch_program(self):
        """A folder without an orch program is not added."""
        env = {"PATH": "/a"}
        ar.with_orch_on_path(env, [self.empty, self.empty / "missing"])
        self.assertEqual(env["PATH"], "/a")

    def test_missing_path_variable(self):
        """Without any PATH the folder alone becomes PATH."""
        env = {}
        ar.with_orch_on_path(env, [self.scripts])
        self.assertEqual(env["PATH"], str(self.scripts) + os.pathsep)

    def test_child_env(self):
        """child_env starts PATH with the scripts folder and keeps PYTHONPATH."""
        with mock.patch.object(ar, "orch_script_dirs", return_value=[self.scripts]), \
                mock.patch.dict(os.environ, {"PATH": os.pathsep.join(["/a", "/b"])}):
            env = ar.child_env()
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(self.scripts))
        self.assertIn(str(ar.TOOL_ROOT), env["PYTHONPATH"])

    def test_candidates_include_python_scripts(self):
        """The folders looked at include the Scripts folder next to the running Python."""
        self.assertIn(Path(sys.executable).parent / "Scripts", ar.orch_script_dirs())

    def test_agent_gets_it(self):
        """run_agent builds the agent environment through with_orch_on_path."""
        src = (ROOT / "orchlib" / "agent_runner.py").read_text(encoding="utf-8")
        self.assertIn("env = with_orch_on_path(dict(os.environ))", src)


if __name__ == "__main__":
    unittest.main()
