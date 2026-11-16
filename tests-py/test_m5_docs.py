import subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _help(*args):
    out = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *args, "--help"],
                         capture_output=True, text=True).stdout
    return " ".join(out.split())


class OwnerMessagesAndStopDocs(unittest.TestCase):
    def test_owner_messages_are_documented(self):
        for rel in ("docs/INBOX.md", "docs/RUNTIME-API.md"):
            with self.subTest(document=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("owner_messages", text)

    def test_commands_and_runtime_document_stop_contract(self):
        for rel in ("docs/COMMANDS.md", "docs/RUNTIME-API.md"):
            with self.subTest(document=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                for word in ("bzz agent stop", "[[stop_agent]]", "bzz dequeen",
                             "subagent", "explicit owner instruction"):
                    self.assertIn(word, text)

    def test_help(self):
        agent = _help("agent")
        for word in ("stop", "owner only", "subagent", "bzz dequeen", "explicit owner instruction"):
            self.assertIn(word, agent)
        self.assertIn("owner_messages", _help("decisions", "add"))


if __name__ == "__main__":
    unittest.main()
