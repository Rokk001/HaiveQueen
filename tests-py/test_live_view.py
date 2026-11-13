"""Agent live-output tail reading and path derivation."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import db, dashboard_data, hives, runtime  # noqa: E402

TAB = chr(9)


# Test the pure reading functions.
class TailFileTest(unittest.TestCase):
    """tail_file and live_lines work on plain files in a temp folder."""

    def setUp(self):
        """A temp folder for the files."""
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def test_missing_and_empty_file(self):
        """A missing or empty file gives no lines and no error."""
        self.assertEqual(dashboard_data.tail_file(self.dir / "none.txt"), [])
        (self.dir / "empty.txt").write_text("", encoding="utf-8")
        self.assertEqual(dashboard_data.tail_file(self.dir / "empty.txt"), [])

    def test_last_lines_only(self):
        """Only the newest `lines` lines come back, in file order."""
        f = self.dir / "a.txt"
        f.write_text("\n".join("line %d" % i for i in range(1, 11)) + "\n", encoding="utf-8")
        self.assertEqual(dashboard_data.tail_file(f, 3), ["line 8", "line 9", "line 10"])
        self.assertEqual(len(dashboard_data.tail_file(f, 300)), 10)

    def test_undecodable_bytes_are_replaced(self):
        """Bytes that are not utf-8 become the replacement character instead of raising."""
        f = self.dir / "b.txt"
        f.write_bytes(b"ok line\n" + bytes([0xff, 0xfe]) + b" bad\n")
        rows = dashboard_data.tail_file(f)
        self.assertEqual(rows[0], "ok line")
        self.assertIn(chr(0xfffd), rows[1])
        self.assertTrue(rows[1].endswith(" bad"))

    def test_large_file_is_read_from_the_end(self):
        """A file much larger than the read limit still gives its newest lines, and a line cut by the limit is dropped."""
        f = self.dir / "big.txt"
        with open(str(f), "w", encoding="utf-8") as handle:
            for i in range(200000):
                handle.write("row %06d\n" % i)
        rows = dashboard_data.tail_file(f, 300, max_bytes=4096)
        self.assertEqual(rows[-1], "row 199999")
        self.assertLessEqual(len(rows), 300)
        self.assertTrue(all(r.startswith("row ") and len(r) == 10 for r in rows), rows[:2])
        self.assertEqual(len(dashboard_data.tail_file(f, 300)), 300)

    def test_live_lines_colours(self):
        """A milestone line carries its colour word before a tab; other lines are plain text."""
        f = self.dir / "milestones.txt"
        f.write_text("Green" + TAB + "12:00 done\nRed" + TAB + "12:01 failed\nplain text\nUnknown" + TAB + "x\n", encoding="utf-8")
        self.assertEqual(dashboard_data.live_lines(f), [("green", "12:00 done"), ("red", "12:01 failed"), ("text", "plain text"), ("text", "Unknown" + TAB + "x")])

    def test_live_path(self):
        """The live file is milestones.txt in the run folder <data>/runs/<project>/<agent>."""
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(self.dir)}):
            p = dashboard_data.live_path("ODP", "Ada")
        self.assertEqual(p, self.dir / "runs" / "ODP" / "Ada" / "milestones.txt")


# Test the live window.


if __name__ == "__main__":
    unittest.main()
