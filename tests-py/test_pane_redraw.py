"""Panes redraw in place: two updates (also after a resize and in a pane narrower than the text) leave one block, nothing in the scrollback.

The escape strings of the agent header, the dashboard and the Queen header are fed into a small terminal model (vtscreen.Screen).
"""
import io
import unittest
from datetime import datetime

from bzztest import RoundCase
from haivequeen import dashboard, header, loop
from test_header import NOW, row
from vtscreen import Screen


def head_of(width, height, **kw):
    """Header lines of one agent row for a pane of this size, as header.run builds them."""
    return header.build_head(row(**kw), "ODP", "cyan", width, NOW, max_lines=height - 1)


class AgentHeaderRedrawTest(unittest.TestCase):
    """header.render: full redraw on a size change, a partial one for every other update."""

    def run_frames(self, cols, rows, width):
        """Draw full, partial, partial, full (resize) and partial frames into one screen; return it and the last header lines."""
        scr = Screen(cols, rows)
        ms = [("White", "milestone one"), ("White", "milestone two")]
        scr.feed("old text that was there before\r\nmore old text\r\n")
        for status, full in (("running", True), ("running", False), ("done", False), ("done", True), ("done", False)):
            lines = head_of(width, rows, status=status)
            scr.feed(header.render(lines, width, rows, ms if full else [], full))
        return scr, head_of(width, rows, status="done")

    def test_wide_pane_one_header_and_no_scrollback(self):
        """In a wide pane the header stands once at the top, the old text is gone, the scrollback stays empty."""
        scr, lines = self.run_frames(60, 12, 60)
        self.assertEqual(scr.scrollback, [])
        self.assertEqual([scr.text(i) for i in range(len(lines))], ["".join(t for t, _ in ln)[:60] for ln in lines])
        self.assertEqual(sum(1 for t in scr.lines() if "MAX |" in t), 1)
        self.assertEqual(sum(1 for t in scr.lines() if "milestone one" in t), 1)

    def test_narrow_pane_cuts_lines_instead_of_wrapping(self):
        """A pane of 14 columns (as the 7th split makes): header lines are cut at the edge, never wrapped into the next row, no scrollback."""
        scr, lines = self.run_frames(14, 8, 14)
        self.assertEqual(scr.scrollback, [])
        for i, ln in enumerate(lines):
            self.assertEqual(scr.text(i), "".join(t for t, _ in ln)[:14])
        self.assertEqual(sum(1 for t in scr.lines() if "MAX |" in t), 1)

    def test_full_redraw_does_not_push_the_old_screen_into_the_scrollback(self):
        """Control: a full redraw with 2J alone (the old string) leaves a copy of the header in the scrollback each time; with 3J behind it none."""
        lines = head_of(60, 12)
        old = Screen(60, 12)
        for _ in range(3):
            old.feed(header.render(lines, 60, 12, [], True).replace(header.ESC + "[3J", ""))
        self.assertTrue(old.scrollback, "model check: 2J without 3J stacks copies")
        new = Screen(60, 12)
        for _ in range(3):
            new.feed(header.render(lines, 60, 12, [], True))
        self.assertEqual(new.scrollback, [])

class DashboardRedrawTest(unittest.TestCase):
    """dashboard.frame_text: every frame overwrites the previous one."""

    def test_two_frames_leave_one_block(self):
        """A second, shorter frame replaces the first: nothing of the first stays, nothing lands in the scrollback."""
        scr = Screen(30, 8)
        first = "\n".join("LIMITS line %d" % i for i in range(7))
        second = "LIMITS new\nmore ended: 2"
        scr.feed(dashboard.frame_text(first, clear=True))
        scr.feed(dashboard.frame_text(first))
        scr.feed(dashboard.frame_text(second))
        self.assertEqual(scr.scrollback, [])
        self.assertEqual(scr.lines(), ["LIMITS new", "more ended: 2"] + [""] * 6)

    def test_overlong_line_is_cut_not_wrapped(self):
        """A line wider than the pane is cut at the edge; the next line stays on its own row."""
        scr = Screen(10, 4)
        scr.feed(dashboard.frame_text("x" * 25 + "\nsecond", clear=True))
        scr.feed(dashboard.frame_text("x" * 25 + "\nsecond"))
        self.assertEqual(scr.scrollback, [])
        self.assertEqual(scr.lines()[:2], ["x" * 10, "second"])

    def test_resize_clears_the_scrollback(self):
        """After a size change the first frame empties the screen and the scrollback."""
        scr = Screen(20, 4)
        scr.scrollback.append("junk")
        scr.feed(dashboard.frame_text("a\nb", clear=True))
        self.assertEqual(scr.scrollback, [])


class QueenHeaderRedrawTest(RoundCase):
    """loop.Pane: the header is redrawn in place while event lines scroll below it."""

    def feed_queen(self, cols, rows):
        """Start the Queen pane output like run_loop, then 40 event lines with a header redraw after each 10; return the screen."""
        out = io.StringIO()
        pane = loop.Pane(self.conn, [], out)
        ui = {"busy": False, "since": datetime(2026, 10, 28, 12, 0), "next_at": datetime(2026, 10, 28, 12, 5), "stop": False, "brain": False,
              "model": "Sonnet 5.5 med.", "projects": ["alpha", "beta"]}
        scr = Screen(cols, rows)
        out.write("%s[2J%s[3J%s[H" % (loop.ESC, loop.ESC, loop.ESC))
        pane.draw_head(ui)
        out.write("%s[%d;1H" % (loop.ESC, pane.rows + 1))
        for i in range(40):
            out.write("12:00:%02d  event line %d\n" % (i, i))
            if i % 10 == 9:
                pane.draw_head(ui)
        scr.feed(out.getvalue())
        return scr

    def test_header_once_events_scroll_below(self):
        """Several header redraws: one QUEEN line, events fill the rows below, nothing of the header in the scrollback."""
        scr = self.feed_queen(100, 30)
        self.assertEqual(sum(1 for t in scr.lines() if t.startswith("◆ Beekeeper")), 1)
        self.assertFalse([t for t in scr.scrollback if "Beekeeper" in t or "Projects:" in t])
        self.assertTrue(scr.lines()[0].startswith("◆ Beekeeper"))
        self.assertIn("12:00:39  event line 39", scr.lines())

    def test_narrow_pane_keeps_the_header_in_its_rows(self):
        """A 40-column pane: the long project line is cut, the header does not wrap into the event rows."""
        scr = self.feed_queen(40, 30)
        self.assertEqual(sum(1 for t in scr.lines() if t.startswith("◆ Beekeeper")), 1)
        self.assertFalse([t for t in scr.scrollback if "Beekeeper" in t or "Projects:" in t])


if __name__ == "__main__":
    unittest.main()
