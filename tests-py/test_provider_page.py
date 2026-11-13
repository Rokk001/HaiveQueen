"""Shipped provider model metadata."""
import json
import unittest
from bzztest import ROOT
from haivequeen import cli_presets


class CliDataFile(unittest.TestCase):
    def test_shipped_data_file_has_a_source_for_every_cli(self):
        """Every CLI model and effort value stands with its source (official documentation)."""
        data = json.loads((ROOT / "haivequeen" / "cli-models.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data), {"claude", "codex", "opencode", "kilo"})
        for key, entry in data.items():
            self.assertTrue(entry["source"].startswith("https://"), key)
            self.assertIsInstance(entry["models"], list)
            self.assertIsInstance(entry["efforts"], list)
        self.assertEqual(cli_presets.effort_list("claude"), ["low", "medium", "high", "xhigh", "max"])
        self.assertIn("gpt-6.1-sol", cli_presets.model_list("codex"))
        self.assertEqual(cli_presets.model_list("nonesuch"), [])


if __name__ == "__main__":
    unittest.main()
