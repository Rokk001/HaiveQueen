"""The context limit is the current context size and is watched by the Beekeeper through the HANDOFF file, never by the Queen from summed input tokens."""
import unittest

import bzztest  # noqa: F401  (puts this checkout first on the import path)
from haivequeen import bees


class QueenContextRuleTests(unittest.TestCase):
    """A Queen ended after one or two minutes because she compared the summed input tokens of her agent row (millions, Codex counts the whole context at every step) with the limit."""

    def text(self):
        """The Queen instructions with the default limit."""
        return bees.queen_instructions("medium", 3, 3, 300000)

    def test_queen_does_not_measure_her_context_herself(self):
        """The instructions forbid measuring the context from token numbers of bzz."""
        text = self.text()
        self.assertNotIn("At 300000 context", text)
        self.assertIn("Never measure your context yourself", text)
        self.assertIn("summed", text)

    def test_queen_ends_only_when_the_handoff_file_exists(self):
        """The Beekeeper writes the HANDOFF file when the current context reaches the limit; only then the Queen ends."""
        text = self.text()
        self.assertIn("file HANDOFF", text)
        self.assertIn("300000", text)
        self.assertIn("current context", text)


if __name__ == "__main__":
    unittest.main()
