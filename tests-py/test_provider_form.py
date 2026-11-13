"""Shared provider explanation translations."""
import unittest
from haivequeen import i18n


class AgentModeExplanationTest(unittest.TestCase):
    def test_explanation_says_subagent_is_selectable_for_every_provider_in_both_languages(self):
        from haivequeen import provider_labels
        explanation = provider_labels.TEXT["beeMode"][1]
        self.assertIn("available for every provider", explanation)
        self.assertIn("für jeden Anbieter verfügbar", i18n.tr(explanation, language="German"))


if __name__ == "__main__":
    unittest.main()
