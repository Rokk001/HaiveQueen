import subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _help(*args):
    out = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args, "--help"],
                         capture_output=True, text=True).stdout
    return " ".join(out.split())


class OwnerMessagesAndStopDocs(unittest.TestCase):
    def test_readme_and_docs(self):
        for rel in ("README.md", "docs/RUNTIME-API.md"):
            t = (ROOT / rel).read_text(encoding="utf-8")
            for word in ("owner_messages", "bzz agent stop", "[[stop_agent]]", "bzz dequeen"):
                self.assertIn(word, t, rel + " lacks " + word)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("subagent", readme)
        self.assertIn("explicit owner instruction", readme)

    def test_help(self):
        agent = _help("agent")
        for word in ("stop", "owner only", "subagent", "bzz dequeen", "explicit owner instruction"):
            self.assertIn(word, agent)
        self.assertIn("owner_messages", _help("decisions", "add"))


if __name__ == "__main__":
    unittest.main()
