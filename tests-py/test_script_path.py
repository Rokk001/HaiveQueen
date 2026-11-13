"""Started as a script (python haivequeen/cli.py), the program must not let haivequeen/platform.py hide the standard module platform."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ScriptPathTest(unittest.TestCase):
    def test_cli_script_keeps_standard_platform(self):
        code = ("import runpy, sys; sys.path.insert(0, %r); "
                "runpy.run_path(%r, run_name='cli_under_test'); "
                "import platform, haivequeen.runtime as r; "
                "print(platform.__file__); print(r.host_name())") % (str(ROOT / "haivequeen"), str(ROOT / "haivequeen" / "cli.py"))
        done = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, cwd=str(ROOT.parent))
        self.assertEqual(done.returncode, 0, done.stderr)
        shown = done.stdout.splitlines()[0]
        self.assertNotIn("haivequeen", Path(shown).parent.name.lower(), shown)
        self.assertTrue(done.stdout.splitlines()[1])


if __name__ == "__main__":
    unittest.main()
