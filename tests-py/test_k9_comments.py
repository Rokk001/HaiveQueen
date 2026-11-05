"""Regression coverage for comments in settings files."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from haivequeen import gui_config


class SettingsCommentsTest(unittest.TestCase):
    def test_save_retains_comments_and_updated_values(self):
        source = '''# top comment
slots = 3 # slot comment

# provider comment
[[providers]] # provider header comment
name = "example" # name comment
[providers.classes] # classes comment
# nested comment
medium = { model = "test#model" } # model comment
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(source, encoding="utf-8")
            data = tomllib.loads(source)
            data["slots"] = 4
            data["providers"][0]["classes"]["medium"]["model"] = "changed"
            gui_config.save_file(path, data)
            saved = path.read_text(encoding="utf-8")
            self.assertEqual(tomllib.loads(saved), data)
            for comment in ("# top comment", "# slot comment", "# provider comment", "# provider header comment", "# name comment", "# classes comment", "# nested comment", "# model comment"):
                with self.subTest(comment=comment):
                    self.assertIn(comment, saved)
