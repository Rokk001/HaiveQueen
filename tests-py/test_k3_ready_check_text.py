"""K-3: the ready check counts merge, compile check and push as tasks of the Orchestrator after ready, not as missing points."""
import unittest

from bzztest import REL  # noqa: F401  (sets up the isolated test environment)
from haivequeen import roles


class ReadyCheckTextTests(unittest.TestCase):
    def test_instructions_name_the_orchestrator_steps_as_not_missing(self):
        text = (roles.ROLES_DIR / "ready-check.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?is)after `?bzz ready`?[^.]*Orchestrator[^.]*merge")
        for word in ("compile check", "push"):
            self.assertIn(word, text.lower())
        self.assertRegex(text, r"(?is)do not fail[^.]*(merge|compile check|push)[^.]*before ready")
        self.assertRegex(text, r"(?i)project rules")


if __name__ == "__main__":
    unittest.main()
