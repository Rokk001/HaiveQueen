"""Tests that carry over checks of the old PowerShell wrapper and phase-rule tests that the Python suite did not cover yet."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import agent_runner as ar  # noqa: E402
from orchlib import config  # noqa: E402


class WrapperParityTest(unittest.TestCase):
    """Runs orch.py with ORCH_* folders pointing into a temp folder (no live data, no model, no terminal window)."""

    def setUp(self):
        """Create temp config, data and log folders."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.env = dict(os.environ)
        self.env["ORCH_CONFIG_DIR"] = str(base / "cfg")
        self.env["ORCH_DATA_DIR"] = str(base / "data")
        self.env["ORCH_LOG_DIR"] = str(base / "logs")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args):
        """Run orch.py; return (exit code, stdout text, stderr text)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "orch.py"), *args], env=self.env, capture_output=True,
                           text=True, encoding="utf-8")
        return r.returncode, r.stdout, r.stderr

    def ok(self, *args):
        """Run a command that must succeed and return its JSON."""
        code, out, err = self.orch(*args)
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def test_runtime_uses_own_database(self):
        """The test runtime has its own database below the temp folder, different from the default location."""
        old = os.environ.get("ORCH_DATA_DIR")
        os.environ.pop("ORCH_DATA_DIR", None)
        try:
            default_db = config.db_path()
        finally:
            if old is not None:
                os.environ["ORCH_DATA_DIR"] = old
        self.ok("kv", "set", "--project", "p", "--key", "k", "--value", "v")
        own = Path(self.env["ORCH_DATA_DIR"]) / "orchestrator.db"
        self.assertTrue(own.is_file())
        self.assertNotEqual(own.resolve(), default_db.resolve())

    def test_agent_text_with_quotes_and_backslash(self):
        """Quotes, a trailing backslash and umlauts in a field value survive the command line and the database."""
        text = 'say "hi" \\ ä ö ü \\'
        a = self.ok("agents", "upsert", "--project", "p", "--name", "Max", "--fields",
                    json.dumps({"status": "running", "lastLine": text}))
        self.assertEqual(a["last_line"], text)
        self.assertEqual(self.ok("agents", "get", "--project", "p", "--name", "Max")["last_line"], text)

    def test_unknown_agent(self):
        """An unknown agent reads as null; updating it fails with exit 1 and the message 'unknown agent'."""
        self.assertIsNone(self.ok("agents", "get", "--project", "p", "--name", "Nobody"))
        code, out, _ = self.orch("agents", "update-fields", "--project", "p", "--name", "Nobody", "--fields", '{"status":"x"}')
        self.assertEqual(code, 1)
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("unknown agent", res["error"])

    def test_mirror_line_formats(self):
        """The rounds line is 'YYYY-MM-DD HH:MM:SS text'; the events line is 'HH:MM|E|text' with whitespace folded and umlauts intact."""
        self.ok("event", "append", "--project", "p", "--text", "round started")
        self.ok("event", "append", "--project", "p", "--text", "Fehler  beim Start: ä ö ü", "--mirror", "events", "--error")
        paths = self.ok("events", "paths", "--project", "p")
        rounds = Path(paths["rounds"]).read_text(encoding="utf-8").splitlines()
        events = Path(paths["events"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rounds), 1)
        self.assertRegex(rounds[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d round started$")
        self.assertEqual(len(events), 1)
        self.assertRegex(events[0], r"^\d\d:\d\d\|E\|Fehler beim Start: ä ö ü$")
        self.assertIsNone(re.search(r"\|O\|", events[0]))

    def test_kv_special_text_and_delete(self):
        """A value with quotes and umlauts round-trips, and after delete the key reads as not found."""
        value = '{"brain":"idle","text":"ä \\"q\\""}'
        self.ok("kv", "set", "--project", "p", "--key", "state", "--value", value)
        self.assertEqual(self.ok("kv", "get", "--project", "p", "--key", "state")["value"], value)
        self.ok("kv", "delete", "--project", "p", "--key", "state")
        self.assertFalse(self.ok("kv", "get", "--project", "p", "--key", "state")["found"])

    def test_bad_arguments_fail(self):
        """A command with a missing required argument ends with a non-zero exit code."""
        code, _, _ = self.orch("kv", "get", "--project", "p")
        self.assertNotEqual(code, 0)


class PhaseRulesParityTest(unittest.TestCase):
    """Phase rule files for all four phases: verbatim sections, less text for verify, the expected sections per phase."""

    SRC = ("# Rules\n\n## Coding style\nspaces only\n\n## Process rules\nplan first\n\n"
           "## Hard rules (tool)\nno secrets\n\n## Hard rules (project)\nno live data\n\n## Extra\nmore\n")

    def setUp(self):
        """Create a workspace with one rule source and a project config that selects sections per phase."""
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        (self.ws / "RULES.md").write_text(self.SRC, encoding="utf-8")
        both = ["## Process rules", "## Hard rules (tool)", "## Hard rules (project)"]
        self.pcfg = {"ruleSources": {"main": "RULES.md"},
                     "phaseRules": {"work": {"main": ["## Coding style"] + both + ["## Extra"]},
                                    "verify": {"main": both}, "release": {"main": both}, "integrate": {"main": both}}}

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def write(self, phase):
        """Write the rule file of a phase; return (section count, text)."""
        out = self.ws / ("rules-%s.md" % phase)
        n = ar.write_phase_rules(self.pcfg, str(self.ws), phase, out)
        return n, out.read_text(encoding="utf-8")

    def test_every_phase_verbatim(self):
        """Every phase writes a rule file whose sections are verbatim parts of the source."""
        lines = self.SRC.splitlines()
        for phase, selection in self.pcfg["phaseRules"].items():
            n, text = self.write(phase)
            self.assertEqual(n, len(selection["main"]), phase)
            for pre in selection["main"]:
                block = ar.markdown_block(lines, pre)
                self.assertTrue(block, pre)
                self.assertIn(block, self.SRC)
                self.assertIn(block, text)

    def test_verify_smaller_than_work(self):
        """The verify rules are shorter than the work rules and leave out the coding-style section."""
        _, work = self.write("work")
        _, verify = self.write("verify")
        self.assertLess(len(verify), len(work))
        self.assertNotIn("Coding style", verify)
        self.assertIn("Coding style", work)

    def test_verify_keeps_process_and_hard_rules(self):
        """The verify rules keep the process rules and both hard-rule sections."""
        _, verify = self.write("verify")
        self.assertIn("Process rules", verify)
        self.assertIn("Hard rules (tool)", verify)
        self.assertIn("Hard rules (project)", verify)


if __name__ == "__main__":
    unittest.main()
