"""Tests that times with a UTC offset in a v1 agent file are stored as local time without offset (the loop compares them with naive times)."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen import legacy  # noqa: E402


class LocalNaiveTest(unittest.TestCase):
    """The helper turns offset times into local naive times and leaves everything else alone."""

    def test_offset_time_becomes_local_naive(self):
        """A time with an offset (also Z) is converted to local time without offset and without fractions."""
        want = datetime(2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        self.assertEqual(legacy.local_naive("2026-01-02T10:00:00.1234567+01:00"), want)
        self.assertEqual(legacy.local_naive("2026-01-02T09:00:00Z"), want)

    def test_naive_and_other_text_stay(self):
        """A time without offset and text that is no time are returned unchanged."""
        self.assertEqual(legacy.local_naive("2026-01-02T10:00:00"), "2026-01-02T10:00:00")
        self.assertEqual(legacy.local_naive("2026-01-02 10:00"), "2026-01-02 10:00")
        self.assertEqual(legacy.local_naive("not a time"), "not a time")


class ImportAgentsTimeTest(unittest.TestCase):
    """The agent import stores no offset and a second import adds nothing."""

    def test_import_is_naive_and_repeatable(self):
        """Imported started_at and ended_at have no offset; importing the same file again skips the row."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "rt").mkdir()
            (base / "rt" / "AGENTS.json").write_text(json.dumps([
                {"name": "A-P1-work", "startedAt": "2026-01-02T10:00:00.5+01:00", "endedAt": "2026-01-02T10:30:00+01:00",
                 "status": "done"}]), encoding="utf-8")
            env = dict(os.environ, BZZ_DATA_DIR=str(base / "data"), BZZ_CONFIG_DIR=str(base / "cfg"))
            argv = [sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), "import-legacy", "--project", "demo", "--runtime", str(base / "rt")]
            first = json.loads(subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8").stdout)
            second = json.loads(subprocess.run(argv, env=env, capture_output=True, text=True, encoding="utf-8").stdout)
            self.assertEqual(first["sources"]["AGENTS.json"]["imported"], 1)
            self.assertEqual(second["sources"]["AGENTS.json"]["skipped"], 1)
            conn = sqlite3.connect(str(base / "data" / "haivequeen.db"))
            try:
                row = conn.execute("SELECT started_at, ended_at FROM agents").fetchone()
            finally:
                conn.close()
            for value in row:
                self.assertEqual(datetime.fromisoformat(value).tzinfo, None, value)


if __name__ == "__main__":
    unittest.main()
