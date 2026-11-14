"""Shared settings: TOML writing, allowed tools, validation and backup persistence."""
import gc
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import db, settings, i18n  # noqa: E402

SAMPLE = '''# Machine settings (kept at the top)
slots = 8
exportDir = "%LOCALAPPDATA%\\\\HaiveQueen\\\\exports"
buildJobs = { "envVar" = "BUILD_MP_JOBS", "reserveMB" = 1024, "perJobMB" = "auto" }
hives = 10

[[providers]]
name = "claude"
protocol = "claude"
command = "claude"
args = ["--output-format", "stream-json", "--allowedTools", "Bash(git merge *),Bash(git -C * merge *),Read", "--effort", "{effort}"]
slots = 5
use = "preferred"
limit = { "source" = "claude" }
[providers.classes]
light = { "model" = "claude-haiku-5-5", "effort" = "medium" }
medium = { "model" = "claude-sonnet-5-5", "effort" = "medium" }

[[providers]]
name = "codex"
protocol = "codex"
command = "codex"
args = ["exec", "--json"]
slots = 1
use = "capability-only"
capabilities = ["images"]

[[projects]]
name = "ODP"
workspace = "C:\\\\Users\\\\Example\\\\Projects\\\\Sample"
'''


class DataLayerTest(unittest.TestCase):
    """Reading, splitting, writing and checking settings."""

    def setUp(self):
        """A temp folder with a sample config.toml."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.toml"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    # The allowed tools are split at commas outside parentheses.
    def test_split_tools_keeps_patterns_with_spaces_and_stars(self):
        """Entries with spaces and stars stay whole; empty entries and blanks go."""
        self.assertEqual(settings.split_tools("Bash(git merge *), Bash(git -C * merge *),Read,,"),
                         ["Bash(git merge *)", "Bash(git -C * merge *)", "Read"])
        self.assertEqual(settings.split_tools("Bash(a,b),Read"), ["Bash(a,b)", "Read"])
        self.assertEqual(settings.split_tools(""), [])

    # The tools come out of the arguments and go back to the same place.
    def test_args_round_trip_keeps_the_position(self):
        """Taking the allowed tools out and putting them back gives the original list; no tools removes the flag."""
        args = settings.load_raw(self.path)["providers"][0]["args"]
        rest, tools, pos = settings.split_args(args)
        self.assertNotIn("--allowedTools", rest)
        self.assertEqual(len(tools), 3)
        self.assertEqual(settings.join_args(rest, tools, pos), args)
        self.assertEqual(settings.join_args(rest, [], pos), rest)
        self.assertEqual(settings.split_args(["a", "b"]), (["a", "b"], [], None))

    # What is written reads back identically, including backslashes, quotes and unicode.
    def test_dumps_round_trips_the_sample_and_odd_strings(self):
        """The sample and a dict with quotes, backslashes, unicode, nesting and empty values read back unchanged."""
        data = settings.load_raw(self.path)
        self.assertEqual(tomllib.loads(settings.dumps(data)), data)
        odd = {"a": 'say "hi"\\now', "b": "größe ünï", "c": [1, 2.5, True, "x"], "d": {}, "e": {"x y": {"k": 1}}, "f": [{"n": 1}, {"n": 2}], "g": 0.00001}
        self.assertEqual(tomllib.loads(settings.dumps(odd)), odd)

    # Problems are reported before anything is written.
    def test_check_reports_bad_machine_settings(self):
        """A good config passes; an unknown protocol, duplicate names and a zero round time are reported."""
        self.assertEqual(settings.check(settings.load_raw(self.path)), [])
        bad = settings.load_raw(self.path)
        bad["providers"][0]["protocol"] = "nope"
        self.assertTrue(settings.check(bad))
        bad = settings.load_raw(self.path)
        bad["providers"][1]["name"] = "claude"
        self.assertTrue(any("unique" in p for p in settings.check(bad)))
        bad = settings.load_raw(self.path)
        bad["roundMinutes"] = 0
        self.assertTrue(any("roundMinutes" in p for p in settings.check(bad)))
        self.assertTrue(settings.check({"x": float("nan")}, machine=False))

    # Saving keeps the top comment and the old file.
    def test_save_keeps_comment_and_backup(self):
        """The first comment lines stay, the old file is stored as .bak, and the new file reads back as the data."""
        data = settings.load_raw(self.path)
        data["slots"] = 3
        settings.save_file(self.path, data)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Machine settings (kept at the top)\n"))
        self.assertEqual(settings.load_raw(self.path)["slots"], 3)
        self.assertEqual(settings.load_raw(self.path.with_name("config.toml.bak"))["slots"], 8)
        self.assertEqual(tomllib.loads(text), data)

    # Typed values keep the type of the old value.
    def test_parse_typed(self):
        """A number stays a number, true/false a bool, a new value is guessed, wrong text raises."""
        self.assertEqual(settings.parse_typed("5", 3), 5)
        self.assertEqual(settings.parse_typed("2.5", 1.0), 2.5)
        self.assertIs(settings.parse_typed("true", False), True)
        self.assertEqual(settings.parse_typed("7"), 7)
        self.assertEqual(settings.parse_typed("abc"), "abc")
        with self.assertRaises(ValueError):
            settings.parse_typed("many", 3)


# All widgets below a widget.


def sch_capabilities(entry):
    """The ticks of a saved provider entry as the program reads them."""
    from haivequeen import scheduler
    return scheduler.declared_capabilities(entry)


if __name__ == "__main__":
    unittest.main()
