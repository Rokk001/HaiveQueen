"""Tests of orchlib.header: colours, wrapping, header lines with the project colour, redraw strings and the redraw loop."""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import db, header, runtime  # noqa: E402

ESC = header.ESC
NOW = datetime(2026, 10, 28, 12, 0, 0)


def row(**kw):
    """Return an agent row as the database gives it, with overrides."""
    base = {"name": "A-1", "person": "Max", "color": "4FA8FF", "type": "claude", "modelShort": "Son 5.5 med.", "status": "running",
            "started_at": "2026-10-28T11:58:30", "ended_at": None, "task": "P1: Build the thing", "ctxTokens": 44000, "tokens_in": 44000, "tokens_out": 95}
    base.update(kw)
    return base


def texts(lines):
    """Return the plain text of each header line."""
    return ["".join(t for t, _ in parts) for parts in lines]


class FormatTest(unittest.TestCase):
    """Colours, wrapping and number formats."""

    def test_sgr(self):
        """Hex colours are true colour (with or without #), names are 16-colour codes, unknown is reset."""
        self.assertEqual(header.sgr("FF8800"), ESC + "[1;38;2;255;136;0m")
        self.assertEqual(header.sgr("#ff8800"), ESC + "[1;38;2;255;136;0m")
        self.assertEqual(header.sgr("Green"), ESC + "[32m")
        self.assertEqual(header.sgr("white"), ESC + "[97m")
        self.assertEqual(header.sgr("nonsense"), ESC + "[0m")

    def test_wrap(self):
        """Words wrap at the width; a word longer than the width is split; newlines stay."""
        self.assertEqual(header.wrap_text("aa bb cc", 5), ["aa bb", "cc"])
        self.assertEqual(header.wrap_text("abcdefghij", 4), ["abcd", "efgh", "ij"])
        self.assertEqual(header.wrap_text("a\nb", 10), ["a", "b"])

    def test_numbers(self):
        """Token counts and runtimes."""
        self.assertEqual([header.fmt_tok(x) for x in (None, 5, 999, 1000, 75000, 1200000)], ["–", "5", "999", "1k", "75k", "1.2M"])
        self.assertEqual(header.fmt_run(90), "01:30")
        self.assertEqual(header.fmt_run(3725), "1:02:05")
        self.assertEqual(header.format_token_line(None, None, None), "Tokens: no data yet")
        self.assertEqual(header.format_token_line(0, 0, 0), "Tokens: no data yet")
        self.assertEqual(header.format_token_line(44000, 44000, 95), "Context 44k | Total input 44k / Output 95")
        self.assertEqual(header.format_token_line(None, 5, None), "Context – | Total input 5 / Output –")


class BuildHeadTest(unittest.TestCase):
    """The header lines."""

    def test_wide(self):
        """Wide pane: line 1 has the project in its colour, the agent part in the agent colour and the status in its colour."""
        lines = header.build_head(row(), "demo", "FF8800", 100, NOW)
        self.assertEqual(texts(lines), ["demo | MAX | A-1 | Claude Son 5.5 med. | running 01:30", "P1: Build the thing", "Context 44k | Total input 44k / Output 95"])
        self.assertEqual([c for _, c in lines[0]], ["FF8800", "4FA8FF", "Yellow"])
        self.assertEqual(lines[1][0][1], "Gray")
        self.assertEqual(lines[2][0][1], "White")

    def test_status_colours_and_codex(self):
        """done is green, failed is red, a Codex agent says Codex; an ended agent shows its own runtime."""
        done = header.build_head(row(status="done", ended_at="2026-10-28T11:59:30", type="codex"), "demo", "white", 100, NOW)
        self.assertIn("Codex", texts(done)[0])
        self.assertTrue(texts(done)[0].endswith("done 01:00"))
        self.assertEqual(done[0][-1][1], "Green")
        failed = header.build_head(row(status="failed"), "demo", "white", 100, NOW)
        self.assertEqual(failed[0][-1][1], "Red")

    def test_narrow(self):
        """Narrow pane: the project gets its own line, the rest wraps; the task is cut with dots."""
        lines = header.build_head(row(task="x" * 80), "demo", "FF8800", 40, NOW)
        t = texts(lines)
        self.assertEqual(t[0], "demo")
        self.assertEqual(lines[0][0][1], "FF8800")
        self.assertTrue(all(len(x) <= 40 for x in t))
        self.assertIn("x" * 37 + "...", t)

    def test_defaults(self):
        """A row without person, colour or tokens still renders."""
        lines = header.build_head({"name": "Z", "status": "starting"}, "p", "white", 80, NOW)
        self.assertEqual(texts(lines)[0], "p | - | Z | Claude  | starting 00:00")
        self.assertEqual(texts(lines)[2], "Tokens: no data yet")


class RenderTest(unittest.TestCase):
    """Redraw strings and the loop."""

    def test_render_full_and_partial(self):
        """A full redraw clears the screen, sets the scroll region and replays the milestones; a partial one saves and restores the cursor."""
        lines = header.build_head(row(), "demo", "white", 100, NOW)
        ms = [("Green", "12:00 first"), ("Red", "12:01 second")]
        full = header.render(lines, 100, 30, ms, True)
        self.assertTrue(full.startswith(ESC + "[r" + ESC + "[2J"))
        self.assertIn("%s[4;30r" % ESC, full)
        self.assertIn("12:00 first", full)
        self.assertLess(full.index("12:00 first"), full.index("12:01 second"))
        part = header.render(lines, 100, 30, [], False)
        self.assertTrue(part.startswith(ESC + "7") and part.endswith(ESC + "8"))
        self.assertNotIn("[2J", part)

    def test_replay_keeps_newest_that_fit(self):
        """Only the newest milestones that fit below the header are replayed."""
        lines = header.build_head(row(), "demo", "white", 100, NOW)
        ms = [("White", "12:%02d line%d" % (i, i)) for i in range(40)]
        out = header.render(lines, 100, 10, ms, True)
        self.assertIn("line39", out)
        self.assertNotIn("line0 ", out)

    def test_read_milestones(self):
        """Lines are 'colour<TAB>text'; others are skipped; only the tail is read."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.txt"
            p.write_text("Green\t12:00 a\nbroken line\nRed\t12:01 b\n", encoding="utf-8")
            self.assertEqual(header.read_milestones(p), [("Green", "12:00 a"), ("Red", "12:01 b")])
            self.assertEqual(header.read_milestones(p, 1), [("Red", "12:01 b")])
            self.assertEqual(header.read_milestones(Path(tmp) / "none.txt"), [])

    def test_run_loop(self):
        """The loop draws from the database: first a full redraw with the replayed milestones, then partial redraws; it ends after the agent ended and the parent is gone."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ORCH_DATA_DIR"] = str(Path(tmp) / "data")
            try:
                conn = db.connect(Path(tmp) / "data" / "orchestrator.db")
                db.upsert_project(conn, "demo", None, "FF8800", 0)
                runtime.upsert_agent(conn, "demo", "A-1", {"status": "running", "person": "Max", "color": "4FA8FF", "task": "T", "startedAt": "2026-10-28T11:58:30"})
                runs = Path(tmp) / "runs"
                (runs / "A-1").mkdir(parents=True)
                (runs / "A-1" / "milestones.txt").write_text("Green\t12:00 replayed\n", encoding="utf-8")
                out = io.StringIO()
                self.assertEqual(header.run("demo", "A-1", 0, runs, out, interval=0, max_ticks=3), 3)
                text = out.getvalue()
                self.assertEqual(text.count("[2J"), 1)
                self.assertIn("replayed", text)
                self.assertIn("MAX | A-1", text)
                self.assertIn(header.sgr("FF8800"), text)
                # ended agent and a parent pid that is gone: the loop ends by itself after two looks
                runtime.upsert_agent(conn, "demo", "A-1", {"status": "done", "endedAt": "2026-10-28T11:59:30"})
                out2 = io.StringIO()
                ticks = header.run("demo", "A-1", 99999999, runs, out2, interval=0, max_ticks=50)
                self.assertEqual(ticks, 2)
                self.assertIn("done", out2.getvalue())
                # a parent that is alive keeps it running
                self.assertEqual(header.run("demo", "A-1", os.getpid(), runs, io.StringIO(), interval=0, max_ticks=4), 4)
                conn.close()
            finally:
                os.environ.pop("ORCH_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
