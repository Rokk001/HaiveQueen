"""Regression coverage for comments in settings files."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from haivequeen import settings


class SettingsCommentsTest(unittest.TestCase):
    def test_removed_multiline_strings_with_quote_endings_keep_comments(self):
        for quote in ('"', "'"):
            for count in (4, 5):
                source = 'removed = ' + quote * 3 + 'value # string content' + quote * count + ' # real comment\nkeep = 1 # keep comment\n'
                with self.subTest(quote=quote, count=count), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "config.toml"
                    path.write_text(source, encoding="utf-8")
                    settings.save_file(path, {"keep": 2})
                    saved = path.read_text(encoding="utf-8")
                    self.assertEqual(tomllib.loads(saved), {"keep": 2})
                    self.assertIn("# real comment", saved)
                    self.assertEqual(saved.count("# keep comment"), 1)
                    self.assertNotIn("# string content", saved)

    def test_provider_comments_follow_named_entries_on_reorder_and_removal(self):
        source = '[[providers]] # alpha header\nname = "alpha" # alpha name\nslots = 1 # alpha slots\n[[providers]] # beta header\nname = "beta" # beta name\nslots = 2 # beta slots\n'
        for remove in (False, True):
            with self.subTest(remove=remove), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.toml"
                path.write_text(source, encoding="utf-8")
                data = tomllib.loads(source)
                data["providers"].reverse()
                if remove:
                    data["providers"].pop()
                settings.save_file(path, data)
                saved = path.read_text(encoding="utf-8")
                self.assertEqual(tomllib.loads(saved), data)
                self.assertIn('name = "beta" # beta name', saved)
                for name in ("alpha", "beta"):
                    for suffix in ("header", "name", "slots"):
                        self.assertEqual(saved.count("# " + name + " " + suffix), 1)

    def test_comments_survive_removed_entry_and_replaced_array(self):
        source = 'removed = 1 # removed comment\nvalues = [\n  1, # first comment\n  2,\n]\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(source, encoding="utf-8")
            data = {"values": [3]}
            settings.save_file(path, data)
            saved = path.read_text(encoding="utf-8")
            self.assertEqual(tomllib.loads(saved), data)
            self.assertIn("# removed comment", saved)
            self.assertIn("# first comment", saved)

    def test_multiline_values_and_literal_hash_survive_other_edits(self):
        source = '''title = """hello # not a comment
world""" # title comment
values = [
  "# text", # array comment
  "other",
]
count = 1
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(source, encoding="utf-8")
            data = tomllib.loads(source)
            data["count"] = 2
            settings.save_file(path, data)
            saved = path.read_text(encoding="utf-8")
            self.assertEqual(tomllib.loads(saved), data)
            self.assertIn("# title comment", saved)
            self.assertIn("# array comment", saved)

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
            settings.save_file(path, data)
            saved = path.read_text(encoding="utf-8")
            self.assertEqual(tomllib.loads(saved), data)
            for comment in ("# top comment", "# slot comment", "# provider comment", "# provider header comment", "# name comment", "# classes comment", "# nested comment", "# model comment"):
                with self.subTest(comment=comment):
                    self.assertIn(comment, saved)
