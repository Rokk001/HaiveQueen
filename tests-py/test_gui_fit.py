"""Tests of the text fitting in the dashboard: no overlapping cells in the Queens-and-Bees table and a readable subtitle of the Active Hives panel."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import bees, db, hives, runtime  # noqa: E402

PLAN = "docs/plans/TURN-STALL-CRASH-FIX-PLAN.md"
LONG_TASK = "Repair the turn stall crash in the carry code of the temple"


class FitTest(unittest.TestCase):
    """A window on a temp database with a Hive with a long name, its Queen and Bees with long task names."""

    # Build the window; skip without a display.
    def setUp(self):
        """One Hive with long names, a Queen, a process Bee and a subagent Bee."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name, "BZZ_LOG_DIR": str(Path(self.tmp.name) / "logs")})
        self.patch.start()
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)
        no_loop.start()
        self.addCleanup(no_loop.stop)
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        hives.create(self.conn, "ODP", PLAN, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": PLAN, "phase": "queen", "provider": "claude", "status": "running",
                                                              "startedAt": db.now(), "person": "Queen-Annabelle", "model": "claude-opus-5-5", "effort": "medium"})
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": PLAN, "phase": "bee", "provider": "claude", "status": "running", "task": LONG_TASK,
                                                            "startedAt": db.now(), "person": "Bartholomea", "package": "TURN-STALL-CRASH-P1", "model": "claude-sonnet-5-5"})
        bees.record_embedded_start(self.conn, "ODP", PLAN, "TURN-STALL-CRASH-P2", "Cornelia", "heavy", "claude", "claude-opus-5-5", "medium")
        from haivequeen import gui
        gui.REFRESH_MS = 10 ** 9
        self.gui = gui

    # Close the window and remove the temp folder.
    def tearDown(self):
        """Destroy the window, close the database and remove the temp folder."""
        self.root.destroy()
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    # Open the window at a size.
    def open(self, width, height):
        """Create the dashboard at the window size and return it after the first draw."""
        self.root.geometry("%dx%d+0+0" % (width, height))
        win = self.gui.Dashboard(self.root, self.conn)
        self.root.update()
        win.draw()   # the first draw may come before the window has its size
        self.root.update()
        return win

    # The text items of a canvas band, per row.
    def rows_of(self, win, top, bottom, left, right):
        """{row y: [(left, right, text)] sorted by left} of the text items inside the band (design coordinates)."""
        rows = {}
        for item in win.canvas.find_all():
            if win.canvas.type(item) != "text":
                continue
            x0, y0, x1, y1 = win.canvas.bbox(item)
            centre = (y0 + y1) / 2
            if top * win.scale <= centre <= bottom * win.scale and left * win.scale <= x0 and x1 <= (right + 400) * win.scale:
                rows.setdefault(round(centre / 4), []).append((x0, x1, win.canvas.itemcget(item, "text")))
        return {k: sorted(v) for k, v in rows.items()}

    # U-1: no cell runs into the next one.
    def test_cells_of_the_bees_table_never_overlap(self):
        """In every row of the Queens and Bees table (and its heading) a cell ends before the next one starts; long text is cut with an ellipsis."""
        for width, height in ((1660, 950), (1100, 640)):
            win = self.open(width, height)
            rows = self.rows_of(win, 560, 700, 10, 1035)
            self.assertGreaterEqual(len(rows), 3)
            texts = []
            for cells in rows.values():
                for (l1, r1, t1), (l2, r2, t2) in zip(cells, cells[1:]):
                    self.assertLessEqual(r1, l2 + 0.5, "%r runs into %r at %dx%d" % (t1, t2, width, height))
                texts += [t for _l, _r, t in cells]
            self.assertTrue(any(t.endswith("…") for t in texts), texts)
            for t in texts:
                self.assertNotIn("TURN-STALL-CRASH-FIX", t.replace("…", "") + "x" if t.endswith("…") else "")

    # U-2: the subtitle is whole at the usual size.
    def test_subtitle_of_active_hives_is_whole_at_1660_wide(self):
        """At about 1660 x 950 the subtitle shows fully; it never touches the key hints."""
        win = self.open(1660, 950)
        if win.canvas.winfo_width() < 1600:
            self.skipTest("the screen is too small for this window size")
        rows = self.rows_of(win, 285, 301, 10, 1035)
        cells = next(iter(rows.values()))
        sub = next(c for c in cells if c[2].startswith("| "))
        self.assertTrue(sub[2].startswith("| 1 hives") and not sub[2].endswith("…"), sub[2])
        later = [c for c in cells if c[0] > sub[0]]
        self.assertTrue(all(sub[1] <= c[0] + 0.5 for c in later))

    # U-2: in a smaller window the subtitle is shortened, never overlapped.
    def test_subtitle_is_shortened_in_a_small_window(self):
        """In a narrow window the subtitle ends with an ellipsis (or fits) and does not touch the hints."""
        win = self.open(900, 560)
        rows = self.rows_of(win, 267 + 20, 267 + 34, 10, 1035)
        cells = next(iter(rows.values()))
        subs = [c for c in cells if c[2].startswith("| ")]
        for sub in subs:
            self.assertTrue(sub[2] in ("| 1 hives across 1 apiaries", "| 1 hives, 1 apiaries") or sub[2].endswith("…"), sub[2])
            self.assertTrue(all(sub[1] <= c[0] + 0.5 for c in cells if c[0] > sub[0]))


if __name__ == "__main__":
    unittest.main()
