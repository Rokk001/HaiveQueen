"""Tests that carry over checks of the old PowerShell wrapper and phase-rule tests that the Python suite did not cover yet."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import agent_runner as ar  # noqa: E402
from haivequeen import config  # noqa: E402


class WrapperParityTest(unittest.TestCase):
    """Runs haivequeen/cli.py with BZZ_* folders pointing into a temp folder (no live data, no model, no terminal window)."""

    def setUp(self):
        """Create temp config, data and log folders."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.env = dict(os.environ)
        self.env["BZZ_CONFIG_DIR"] = str(base / "cfg")
        self.env["BZZ_DATA_DIR"] = str(base / "data")
        self.env["BZZ_LOG_DIR"] = str(base / "logs")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def orch(self, *args):
        """Run haivequeen/cli.py; return (exit code, stdout text, stderr text)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args], env=self.env, capture_output=True,
                           text=True, encoding="utf-8")
        return r.returncode, r.stdout, r.stderr

    def ok(self, *args):
        """Run a command that must succeed and return its JSON."""
        code, out, err = self.orch(*args)
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def test_runtime_uses_own_database(self):
        """The test runtime has its own database below the temp folder, different from the default location."""
        old = os.environ.get("BZZ_DATA_DIR")
        os.environ.pop("BZZ_DATA_DIR", None)
        try:
            default_db = config.db_path()
        finally:
            if old is not None:
                os.environ["BZZ_DATA_DIR"] = old
        self.ok("kv", "set", "--project", "p", "--key", "k", "--value", "v")
        own = Path(self.env["BZZ_DATA_DIR"]) / "haivequeen.db"
        self.assertTrue(own.is_file())
        self.assertNotEqual(own.resolve(), default_db.resolve())

    def test_agent_text_with_quotes_and_backslash(self):
        """Quotes, a trailing backslash and umlauts in a field value survive the command line and the database."""
        text = 'say "hi" \\ ä ö ü \\'
        a = self.ok("agents", "upsert", "--project", "p", "--name", "Max", "--fields",
                    json.dumps({"status": "running", "lastLine": text}))
        self.assertEqual(a["last_line"], text)
        self.assertEqual(self.ok("agents", "get", "--project", "p", "--name", "Max")["last_line"], text)

    def test_update_fields_cwd_survives_windows_powershell(self):
        """The hook-allowed --cwd form records a folder with a space through Windows PowerShell 5.1; the JSON form fails there."""
        shell = shutil.which("powershell.exe") if os.name == "nt" else None
        if not shell:
            self.skipTest("Windows PowerShell 5.1 not available")
        from haivequeen import claude_hook, profile
        folder = self.base / "plan wt"
        folder.mkdir()
        cli = ROOT / "haivequeen" / "cli.py"
        policy = profile.claude_hook_policy("p", "plans/A.md", 1, "origin", "main", sys.executable, str(cli), "a", name="Max")
        self.ok("agents", "upsert", "--project", "p", "--name", "Max", "--fields", '{"status":"running"}')

        def run(args):
            """Run one command through Windows PowerShell 5.1 after the hook allowed it; return the agent row."""
            command = "& '%s' -I '%s' agents update-fields --project p --name Max %s" % (sys.executable, cli, args)
            event = {"hook_event_name": "PreToolUse", "tool_name": "PowerShell", "tool_input": {"command": command}}
            self.assertIsNotNone(claude_hook.decide(event, policy, None), command)
            subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], env=self.env, capture_output=True,
                           timeout=120)
            return self.ok("agents", "get", "--project", "p", "--name", "Max")
        self.assertNotEqual(run("--fields '{\"cwd\": \"%s\"}'" % folder.as_posix()).get("cwd"), folder.as_posix())
        self.assertEqual(run("--cwd '%s'" % folder)["cwd"], str(folder))

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


if __name__ == "__main__":
    unittest.main()
