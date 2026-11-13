"""The start command of a Queen lists the commands her role file names and does not limit her to a shorter list."""
import re
import unittest
from pathlib import Path

from bzztest import REL  # noqa: F401  (sets up the isolated test environment)
from haivequeen import agent_runner

ROLE = Path(__file__).resolve().parent.parent / "roles" / "queen.md"


class ReportPromptTest(unittest.TestCase):
    def test_queen_prompt_does_not_say_only_and_names_the_role_commands(self):
        text = agent_runner.report_prompt("task.md", "P", "7", "queen")
        self.assertNotIn("Report only", text)
        self.assertIn("never edit a queue file or state table by hand", text)
        self.assertNotIn("integration acquire", text)
        role = ROLE.read_text(encoding="utf-8")
        for command in ("package", "agent start", "agent result", "agent wait", "decisions add", "decisions list"):
            self.assertIn("bzz " + command, role)   # the role file still names them
            self.assertIn(command.split()[0], text)
        self.assertRegex(text, r"agent start\|wait\|result")
        self.assertIn("decisions list|add", text)

    def test_other_phases_keep_their_short_lists(self):
        check = agent_runner.report_prompt("task.md", "P", "8", "verify")
        self.assertNotIn("Report only", check)
        self.assertNotIn("decisions list|add", check)
        self.assertNotIn("integration acquire", check)
        bee = agent_runner.report_prompt("task.md", "P", "9", "bee")
        self.assertIn("Final report", bee)
        self.assertNotIn("ready", bee)


if __name__ == "__main__":
    unittest.main()
