"""Checks of the old PowerShell fix tests (quoted helper arguments) that the Python suite did not cover yet."""
import json
import re
import sys
import unittest
from pathlib import Path

from bzztest import ROOT
from haivequeen import platform as osplat

ECHO = "import json,sys; print(json.dumps(sys.argv[1:]))"


class QuotedArgumentsTest(unittest.TestCase):
    """Python counterpart of the old Quote-Arg checks: helper processes get their arguments as a list, never as one string."""

    def run_echo(self, *args):
        """Start a child that prints its arguments as JSON; return them as the child received them."""
        code, out, err, timed_out = osplat.run_with_timeout([sys.executable, "-I", "-c", ECHO, *args], 30)
        self.assertEqual((code, timed_out), (0, False), err)
        return json.loads(out.decode("utf-8"))

    def test_name_with_spaces_and_comma_is_one_argument(self):
        """A name with spaces, a comma and a quote reaches the helper as exactly one argument, next to the following option."""
        name = 'DUMMY-R4 experience, arena-work "x"'
        self.assertEqual(self.run_echo("--name", name, "--parent-pid", "7"), ["--name", name, "--parent-pid", "7"])

    def test_path_with_spaces_and_comma_is_one_argument(self):
        """A path with spaces and a comma arrives as one argument."""
        path = r"C:\a folder, with comma\x.txt"
        self.assertEqual(self.run_echo("--out", path), ["--out", path])

    def test_no_shell_string_calls(self):
        """No module starts a process through a shell string (the old unquoted call): no shell=True, no os.system."""
        bad = []
        for f in sorted((ROOT / "haivequeen").glob("*.py")) + [ROOT / "haivequeen" / "cli.py"]:
            if re.search(r"shell\s*=\s*True|os\.system\(", f.read_text(encoding="utf-8")):
                bad.append(f.name)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
