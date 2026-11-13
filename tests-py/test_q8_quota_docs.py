import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CapabilityThresholdDocs(unittest.TestCase):
    def test_readme_and_agent_rules_describe_thresholds(self):
        for rel in ("README.md", "docs/AGENT-RULES.md"):
            with self.subTest(document=rel):
                text = " ".join((ROOT / rel).read_text(encoding="utf-8").lower().split())
                for phrase in ("threshold per capability", "default is 100 %",
                               "5-hour or weekly", "equal to or above",
                               "running queens and agents", "work waits"):
                    self.assertTrue(phrase in text, f"{rel} lacks {phrase!r}")


if __name__ == "__main__":
    unittest.main()
