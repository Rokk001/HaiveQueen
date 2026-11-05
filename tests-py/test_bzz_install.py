import tempfile
import unittest
from pathlib import Path

from haivequeen import bzz_install


class InstallTests(unittest.TestCase):
    def test_installs_commands_and_preserves_existing_command(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            existing = home / ".claude/commands/bzz.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("custom", encoding="utf-8")
            result = bzz_install.install(home)
            self.assertEqual(existing.read_text(encoding="utf-8"), "custom")
            self.assertIn("not replaced", result["Claude Code"])
            for path in (".agents/skills/bzz/SKILL.md", ".config/opencode/commands/bzz.md", ".config/kilo/commands/bzz.md"):
                content = (home / path).read_text(encoding="utf-8")
                self.assertIn("bzz questions --open", content)
                self.assertIn("--by owner", content)
            self.assertIn("skipped", result["Custom CLI"])
