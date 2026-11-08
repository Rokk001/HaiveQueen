"""Tests of the live output of agents in the graphical dashboard: reading the end of a live file (gui_data.tail_file, live_lines, live_path)
and the live window (Enter / double click, one window per agent, hint without a file)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import db, gui_data, hives, runtime  # noqa: E402

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
        self.assertEqual(gui_data.tail_file(self.dir / "none.txt"), [])
        (self.dir / "empty.txt").write_text("", encoding="utf-8")
        self.assertEqual(gui_data.tail_file(self.dir / "empty.txt"), [])

    def test_last_lines_only(self):
        """Only the newest `lines` lines come back, in file order."""
        f = self.dir / "a.txt"
        f.write_text("\n".join("line %d" % i for i in range(1, 11)) + "\n", encoding="utf-8")
        self.assertEqual(gui_data.tail_file(f, 3), ["line 8", "line 9", "line 10"])
        self.assertEqual(len(gui_data.tail_file(f, 300)), 10)

    def test_undecodable_bytes_are_replaced(self):
        """Bytes that are not utf-8 become the replacement character instead of raising."""
        f = self.dir / "b.txt"
        f.write_bytes(b"ok line\n" + bytes([0xff, 0xfe]) + b" bad\n")
        rows = gui_data.tail_file(f)
        self.assertEqual(rows[0], "ok line")
        self.assertIn(chr(0xfffd), rows[1])
        self.assertTrue(rows[1].endswith(" bad"))

    def test_large_file_is_read_from_the_end(self):
        """A file much larger than the read limit still gives its newest lines, and a line cut by the limit is dropped."""
        f = self.dir / "big.txt"
        with open(str(f), "w", encoding="utf-8") as handle:
            for i in range(200000):
                handle.write("row %06d\n" % i)
        rows = gui_data.tail_file(f, 300, max_bytes=4096)
        self.assertEqual(rows[-1], "row 199999")
        self.assertLessEqual(len(rows), 300)
        self.assertTrue(all(r.startswith("row ") and len(r) == 10 for r in rows), rows[:2])
        self.assertEqual(len(gui_data.tail_file(f, 300)), 300)

    def test_live_lines_colours(self):
        """A milestone line carries its colour word before a tab; other lines are plain text."""
        f = self.dir / "milestones.txt"
        f.write_text("Green" + TAB + "12:00 done\nRed" + TAB + "12:01 failed\nplain text\nUnknown" + TAB + "x\n", encoding="utf-8")
        self.assertEqual(gui_data.live_lines(f), [("green", "12:00 done"), ("red", "12:01 failed"), ("text", "plain text"), ("text", "Unknown" + TAB + "x")])

    def test_live_path(self):
        """The live file is milestones.txt in the run folder <data>/runs/<project>/<agent>."""
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": str(self.dir)}):
            p = gui_data.live_path("ODP", "Ada")
        self.assertEqual(p, self.dir / "runs" / "ODP" / "Ada" / "milestones.txt")


# Test the live window.
class LiveWindowTest(unittest.TestCase):
    """The window opens for a Hive row (its Queen) and a Bee row, shows the file, and is opened only once per agent."""

    def setUp(self):
        """A temp data folder with one Hive, its Queen and a Bee; skip without a display."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")
        class QuietToplevel(tk.Toplevel):
            """A Toplevel that is fully transparent from the start, so the live windows of the tests do not flash on the user's screen."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        quiet = mock.patch("tkinter.Toplevel", QuietToplevel)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name, "BZZ_LOG_DIR": str(Path(self.tmp.name) / "logs")})
        self.patch.start()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        self.plan = "docs/plans/AI-LEVELS-PLAN.md"
        hives.create(self.conn, "ODP", self.plan, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": self.plan, "phase": "queen", "provider": "claude", "status": "running",
                                                              "startedAt": db.now(), "person": "Ava"})
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": self.plan, "phase": "bee", "provider": "claude", "status": "running",
                                                            "startedAt": db.now(), "person": "Sophia", "package": "P1"})
        from haivequeen import gui
        gui.REFRESH_MS = gui.LIVE_MS = 10 ** 9
        self.gui = gui
        self.root.geometry("1672x901")
        self.win = gui.Dashboard(self.root, self.conn)
        self.root.update()

    def tearDown(self):
        """Close the windows and remove the temp folder."""
        self.root.destroy()
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    def write_live(self, agent, text):
        """Write the live file of an agent of the project ODP."""
        path = gui_data.live_path("ODP", agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def shown(self, view):
        """The text currently shown in a live window."""
        return view.box.get("1.0", "end")

    def test_hive_row_opens_the_queen_and_focuses_on_second_open(self):
        """Enter on a Hive row opens the Queen's live window with her text; a second Enter reuses the same window."""
        self.write_live("ODP__queen", "Green" + TAB + "12:00 queen started\n")
        self.assertEqual(self.win.snap["hives"][0]["queen_agent"], "ODP__queen")
        self.win.on_key(mock.Mock(keysym="Return"))
        self.root.update()
        self.assertEqual(len(self.win.live), 1)
        view = self.win.live[("ODP", "ODP__queen")]
        self.assertIn("12:00 queen started", self.shown(view))
        self.win.on_key(mock.Mock(keysym="Return"))
        self.root.update()
        self.assertIs(self.win.live[("ODP", "ODP__queen")], view)
        self.assertEqual(len(self.win.live), 1)

    def test_bee_row_opens_the_bee(self):
        """After selecting a Bee row, Enter opens that Bee's window."""
        self.write_live("ODP__bee", "White" + TAB + "12:01 agent works\n")
        self.win.select(("bees", 1))   # row 0 is the Queen, her Bee hangs below
        self.win.open_live()
        self.root.update()
        self.assertIn("12:01 agent works", self.shown(self.win.live[("ODP", "ODP__bee")]))

    def test_double_click_on_a_row_opens_its_window(self):
        """A double click inside the registered area of the first Hive row opens the live window."""
        self.write_live("ODP__queen", "White" + TAB + "12:02 hello\n")
        x0, y0, x1, y1, action, arg = next(h for h in self.win.hits if h[4] == self.win.select and h[5] == ("hives", 0))
        self.win.on_double(mock.Mock(x=(x0 + x1) / 2, y=(y0 + y1) / 2))
        self.root.update()
        self.assertIn(("ODP", "ODP__queen"), self.win.live)

    def test_missing_file_shows_a_hint(self):
        """Without a live file the window shows a hint instead of failing."""
        self.win.open_live()
        self.root.update()
        text = self.shown(self.win.live[("ODP", "ODP__queen")])
        self.assertIn("No live output yet", text)

    def test_only_the_newest_lines_and_the_end_is_followed(self):
        """Of 500 lines only 300 show, and the view sits at the end."""
        self.write_live("ODP__queen", "".join("White" + TAB + "line %d\n" % i for i in range(500)))
        self.win.open_live()
        self.root.update()
        view = self.win.live[("ODP", "ODP__queen")]
        rows = [r for r in self.shown(view).split(chr(10)) if r]
        self.assertEqual((len(rows), rows[0], rows[-1]), (300, "line 200", "line 499"))
        self.assertGreaterEqual(view.box.yview()[1], 0.999)
        self.write_live("ODP__queen", "".join("White" + TAB + "line %d\n" % i for i in range(500, 520)))
        view.refresh()
        self.root.update()
        self.assertIn("line 519", self.shown(view))
        self.assertGreaterEqual(view.box.yview()[1], 0.999)

    def test_panel_headers_show_the_hint(self):
        """The Hive and Bee panel headers draw the Enter hint."""
        texts = [self.win.canvas.itemcget(i, "text") for i in self.win.canvas.find_all() if self.win.canvas.type(i) == "text"]
        self.assertGreaterEqual(texts.count("[Enter]"), 2)


if __name__ == "__main__":
    unittest.main()
