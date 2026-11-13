"""K-2: an Agent checks only its own change with focused tests; no text it receives asks for the full compile check or the whole test suite."""
import re
import unittest
from pathlib import Path

from bzztest import REL  # noqa: F401  (sets up the isolated test environment)
from haivequeen import roles

ROOT = Path(__file__).resolve().parent.parent


class AgentCheckTextTests(unittest.TestCase):
    def test_agent_text_asks_for_focused_tests_and_not_for_the_full_check(self):
        text = (roles.ROLES_DIR / "bee.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?i)focused tests")
        self.assertNotRegex(text, r"(?i)run the full compile check")
        self.assertNotIn("bzz compile-check", text)
        self.assertRegex(text, r"(?i)no full compile check")

    def test_project_rules_demand_no_full_suite_and_describe_no_queen_integration(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)whole test suite[^.]*must be green")
        self.assertNotIn("bzz integration", text)
        self.assertNotRegex(text, r"(?i)integrating Queen")
        self.assertNotIn("git push origin HEAD:main", text)
        self.assertRegex(text, r"(?i)focused tests")


if __name__ == "__main__":
    unittest.main()
