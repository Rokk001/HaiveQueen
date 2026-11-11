import subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DocsNothingToIntegrate(unittest.TestCase):
    def test_readme(self):
        t = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("nothing left to integrate", t)

    def test_docs(self):
        t = (ROOT / "docs" / "AGENT-RULES.md").read_text(encoding="utf-8")
        self.assertIn("nothing left to integrate", t)

    def test_help(self):
        out = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), "ready", "--help"],
                             capture_output=True, text=True).stdout
        self.assertIn("nothing left to integrate", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
