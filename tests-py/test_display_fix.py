"""Tests of the display fixes after the first live look at V2: slim Apiary head, equal Hive widths, a frame for every Hive, the running
Queen in the head, and plain female Queen names without a number."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from bzztest import REL, RoundCase
from haivequeen import db, hivepane, hives, loop, runtime
from haivequeen.terminal import pane_title
from haivequeen.terminal.headless import Headless

SLEEP = [sys.executable, "-c", "import time;time.sleep(60)"]


def plain(lines):
    """Text of frame lines without colours, one string per line."""
    return ["".join(t for t, _ in parts) for parts in lines]


class FrameCase(unittest.TestCase):
    """One Hive in a temporary database; the Apiary has a colour."""

    def setUp(self):
        """One Hive of the Apiary ODP."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / 'db')
        db.upsert_project(self.conn, 'ODP', None, 'FF9F43', 1)
        self.plan = 'plans/AI-LEVELS-PLAN.md'
        hives.create(self.conn, 'ODP', self.plan, 'claude', 'FF9F43')

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def queen(self, name, status, **extra):
        """A Queen row of the Hive."""
        row = {'plan': self.plan, 'phase': 'queen', 'status': status, 'person': name, 'type': 'claude', 'modelShort': 'Son 5.5 med.', 'started_at': db.now()}
        row.update(extra)
        runtime.upsert_agent(self.conn, 'ODP', name, row)

    def lines(self, **kw):
        """The frame lines at 60 columns."""
        return hivepane.frame_lines(self.conn, 'ODP', self.plan, 60, **kw)


class FrameFixTest(FrameCase):
    """Items 2 and 3: every Hive pane has a coloured frame; the running Queen is shown although older Queens of the Hive are in the list."""

    def test_hive_without_queen_has_a_frame_in_the_apiary_colour(self):
        """A Hive without a running Queen draws a small box: top, Hive line, Queen line with the state, bottom; all in the Apiary colour."""
        self.conn.execute("UPDATE hives SET state='answer' WHERE plan_path=?", (self.plan,))
        lines = self.lines()
        text = plain(lines)
        self.assertEqual(len(text), 4)
        self.assertTrue(text[0].startswith(hivepane.TOP) and text[3].startswith(hivepane.BOTTOM))
        self.assertIn('Hive: AI-LEVELS-PLAN', text[1])
        self.assertIn('Queen: wartet auf Antwort', text[2])
        self.assertEqual({c for parts in lines for t, c in parts if t.strip(' │┌┐└┘─')}, {'FF9F43'})
        self.assertEqual(len(self.lines(message='X nochmal')), 5)

    def test_the_running_queen_wins_over_an_older_one(self):
        """An ended Queen of the same Hive that is listed first does not hide the running Queen: 'Queen: Ada', never 'Queen: -'."""
        self.queen('Gina', 'done', ended_at=db.now())
        self.queen('Ada', 'running')
        self.conn.execute("UPDATE hives SET state='running' WHERE plan_path=?", (self.plan,))
        queen = next(t for t in plain(self.lines()) if 'Queen:' in t)
        self.assertIn('Queen: Ada', queen)
        self.assertIn('Son 5.5 med.', queen)

    def test_an_ended_queen_alone_does_not_show_a_bare_dash(self):
        """With only ended Queens and a Hive marked running, the Queen line is not a bare dash."""
        self.queen('Gina', 'done', ended_at=db.now())
        self.conn.execute("UPDATE hives SET state='running' WHERE plan_path=?", (self.plan,))
        queen = next(t for t in plain(self.lines()) if 'Queen:' in t)
        self.assertNotRegex(queen, r'Queen: -\s*│?\s*$')


class HeadPaneTest(FrameCase):
    """The head pane overwrites its line instead of clearing the screen, so no copies pile up in the scrollback."""

    def test_the_head_never_clears_the_screen(self):
        """run_head writes the line with a cursor move and an erase to the end of the line, never the screen clear."""
        import io
        from unittest import mock
        out = io.StringIO()
        with mock.patch('haivequeen.config.db_path', return_value=Path(self.tmp.name) / 'db'):
            hivepane.run_head('ODP', out=out, interval=0, max_ticks=2)
        self.assertNotIn('\x1b[2J', out.getvalue())
        self.assertIn('Apiary ODP', out.getvalue())


class PlacementCase(RoundCase):
    """A headless terminal in which Hive panes are opened; every split is recorded as (target, direction, size)."""

    def setUp(self):
        """Headless terminal with a Beekeeper pane; pane commands are sleepers."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        from unittest import mock
        self.term = Headless(self.base / "panes")
        self.chief = self.term.open_window("HaiveQueen", pane_title("all projects", "QUEEN", "Queen", "m", True), SLEEP, name="chief")
        self.patches = [mock.patch("haivequeen.loop.hive_argv", return_value=SLEEP), mock.patch("haivequeen.loop.apiary_argv", return_value=SLEEP)]
        for p in self.patches:
            p.start()
        self.calls = []
        orig = self.term.split_pane
        self.term.split_pane = lambda *a, **k: (self.calls.append((a[6], a[4], a[5])), orig(*a, **k))[1]

    def tearDown(self):
        """End every pane process."""
        for p in self.patches:
            p.stop()
        for pane in self.term.list_panes("HaiveQueen"):
            if pane.alive:
                self.term.close_pane(pane.id)
        super().tearDown()


class SplitSizeTest(PlacementCase):
    """Items 1 and 5: slim head, equal widths."""

    def test_the_first_hive_leaves_the_head_slim(self):
        """The Hive below the head takes almost all of the height: the head keeps one or two lines."""
        env = self.envs()[0]
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        below_head = [c for c in self.calls if c[1] == "vertical"]
        self.assertEqual(len(below_head), 1)
        self.assertGreaterEqual(below_head[0][2], 0.9)
        self.assertLess(below_head[0][2], 1.0)

    def test_hives_of_an_apiary_get_equal_widths(self):
        """With four living Hives the successive splits leave every Hive pane a quarter of the width."""
        env = self.envs()[0]
        plans = [REL] + ["other/%s-PLAN.md" % c for c in "BCD"]
        for i, plan in enumerate(plans):
            hives.create(self.conn, "testproj", plan, "claude", "4FA8FF" if i == 0 else "C77DFF")
        for plan in plans:
            loop.ensure_hive_pane(env, self.term, "HaiveQueen", plan)
        widths = [1.0]
        for _, direction, size in [c for c in self.calls if c[1] == "horizontal"][1:]:   # the first horizontal split opens the head beside the Beekeeper
            last = widths[-1]
            widths[-1] = last * (1 - size)
            widths.append(last * size)
        self.assertEqual(len(widths), 4)
        for w in widths:
            self.assertAlmostEqual(w, 0.25, places=6)

    def test_more_hives_than_counted_still_open(self):
        """A Hive that appears after the counted ones is split in half and never raises."""
        env = self.envs()[0]
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", "x/B-PLAN.md")
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", "x/C-PLAN.md")
        self.assertEqual(len([p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_hive_title(p.title)]), 3)


class QueenNameTest(PlacementCase):
    """Item 4: a Queen carries a plain female name that no running agent uses, never a number suffix."""

    def test_names_of_old_agents_are_reused_without_a_number(self):
        """Every list name was an agent of the project long ago: the new Queen still shows a plain name, her row alone carries the suffix."""
        for n in loop.AGENT_NAMES:
            self.agent(n, phase="work", status="done", person=n, started=3000, ended=2900)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(terminal=self.term, poll=0.01, tries=1)
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertIn(queens[0]["person"], loop.AGENT_NAMES)
        self.assertNotRegex(queens[0]["person"], r"-\d")
        events = [e["text"] for e in db.rows(self.conn, "SELECT text FROM events WHERE text LIKE '%started%'")]
        self.assertTrue(any(queens[0]["person"] + " started" in e for e in events), events)
        self.assertFalse(any(re.search(r"-\d+ started", e) for e in events), events)
        prompt = (self.env().run_dir(queens[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("--name %s " % queens[0]["name"], prompt)   # the Queen updates her own row, so the prompt names the row key

    def test_the_list_is_long_enough_for_many_running_agents(self):
        """The list of female names is longer than the number of agents that can run at once, and has no duplicates."""
        self.assertGreaterEqual(len(loop.AGENT_NAMES), 40)
        self.assertEqual(len(set(loop.AGENT_NAMES)), len(loop.AGENT_NAMES))

    def test_no_running_agent_shares_a_name(self):
        """When every list name but one is held by a running agent, the new agent gets the free one."""
        free = loop.AGENT_NAMES[-1]
        for n in loop.AGENT_NAMES[:-1]:
            self.agent(n, phase="work", status="running", person=n, started=5)
        self.assertEqual(loop.agent_identity(self.conn)["Person"], free)


if __name__ == "__main__":
    unittest.main()
