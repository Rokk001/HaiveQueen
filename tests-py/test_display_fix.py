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


class QueenOrderTest(PlacementCase):
    """The Queen order names the integration place first: the release worktree may only be recreated by the Queen who holds the place."""

    def test_acquire_comes_before_the_release_worktree_the_compile_check_and_ready(self):
        """Order in the Queen prompt: acquire, then the release worktree, then the compile check, then bzz ready, then her own integration and release."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(terminal=self.term, poll=0.01, tries=1)
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        marks = ["bzz integration acquire", "release worktree", "compile check", "bzz ready", "bzz integration release"]
        at = [text.find(m) for m in marks]
        self.assertNotIn(-1, at, dict(zip(marks, at)))
        self.assertEqual(at, sorted(at), dict(zip(marks, at)))
        self.assertNotIn("Register your own branch with bzz ready, then", text)


class OneBuildJobTest(unittest.TestCase):
    """The owner's rule is one build job (--parallel 1): the value that bzz integration acquire hands out is always 1."""

    def test_the_planned_build_always_gets_one_job(self):
        """Whatever the cores, the free RAM and the measured use per job say, plan_build returns one job."""
        from haivequeen import buildjobs

        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "db")

            class Env:
                """Just enough of a project environment for plan_build."""
                name = "ODP"
                settings = {"buildJobs": {"envVar": "BUILD_MP_JOBS", "perJobMB": 300}}

            Env.conn = conn
            for cores, free in ((12, 32000), (6, 4096), (2, 100000)):
                plan = buildjobs.plan_build(Env, free_mb=free, cores=cores)
                self.assertEqual(plan["Jobs"], 1, (cores, free))
                self.assertEqual(plan["EnvVar"], "BUILD_MP_JOBS")
            conn.close()


class QueenEndRuleTest(unittest.TestCase):
    """A Queen ends only when the plan is integrated, only owner questions remain, or the Beekeeper asked for a handoff."""

    def test_the_queen_order_names_the_three_ways_to_end(self):
        """The Queen instructions say that she works through all open packages in one run and list the only three reasons to end."""
        from haivequeen import bees
        text = bees.queen_instructions("medium", 3, 3, 300000)
        self.assertIn("You end only when", text)
        for reason in ("integrated", "WAITING_FOR_OWNER", "HANDOFF"):
            self.assertIn(reason, text[text.index("You end only when"):])
        self.assertIn("never end after an intermediate step", text)
        self.assertIn("wins over any rule", text[text.index("never end after an intermediate step"):])


class EndedWithoutWorkTest(unittest.TestCase):
    """K2 looks for new commits in the repository of the Queen, in every worktree and branch, not only in her start folder."""

    def git(self, cwd, *args, date=None):
        """Run git in `cwd` with a fixed identity and an optional commit date."""
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        import subprocess
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    def test_a_commit_in_another_worktree_counts_as_work(self):
        """The Queen started in the plan worktree; her commit in a second worktree of the same repository means she did work."""
        with tempfile.TemporaryDirectory() as tmp:
            main, other = Path(tmp) / "main", Path(tmp) / "other"
            main.mkdir()
            self.git(main, "init", "-q")
            (main / "a.txt").write_text("a", encoding="utf-8")
            self.git(main, "add", "a.txt")
            self.git(main, "commit", "-q", "-m", "old", date="2020-01-01T00:00:00")
            self.git(main, "worktree", "add", "-q", "-b", "work3", str(other))
            (other / "b.txt").write_text("b", encoding="utf-8")
            self.git(other, "add", "b.txt")
            self.git(other, "commit", "-q", "-m", "new work")
            run = Path(tmp) / "run"
            run.mkdir()

            class Env:
                """Just enough of an environment: the run folder of an agent."""
                def run_dir(self, name):
                    return run

            agent = {"name": "Ada", "cwd": str(main), "started_at": "2021-01-01T00:00:00"}
            self.assertFalse(hives.ended_without_work(Env(), agent))
            from datetime import datetime, timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
            self.assertTrue(hives.ended_without_work(Env(), dict(agent, started_at=tomorrow)))   # nothing is newer than tomorrow


if __name__ == "__main__":
    unittest.main()


class WrapAndLayoutTest(FrameCase):
    """Narrow Hive panes wrap the head lines instead of cutting them; the session start puts the first Apiary head right of the Beekeeper before the dashboard goes below it."""

    def test_head_lines_wrap_in_a_narrow_pane(self):
        """At 20 columns the whole Hive line and the whole Queen line are still readable, on more than one row."""
        self.conn.execute("UPDATE hives SET state='answer' WHERE plan_path=?", (self.plan,))
        rows = [t[2:-2].strip() for t in plain(hivepane.frame_lines(self.conn, 'ODP', self.plan, 20))[1:-1]]
        self.assertTrue(all(len(t) <= 16 for t in rows))
        joined = "".join(t.replace(" ", "") for t in rows)
        self.assertIn("Hive:AI-LEVELS-PLAN", joined)
        self.assertGreater(len(rows), 2)

    def test_session_start_opens_the_dashboard_and_the_head_right_of_it(self):
        """Without panes the window gets the dashboard as its first pane and the first Apiary head split off it to the right; there is no Beekeeper pane."""
        calls = []

        class Term:
            """Records the pane calls of the session start."""

            def list_panes(self, window):
                """No pane is open."""
                return []

            def open_window(self, window, title, command, colour=None, name=None):
                """Open the window with the dashboard pane."""
                calls.append(("open", name))
                return "W.0"

            def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
                """Record a split."""
                calls.append(("split", name, direction, size, target))
                return "W.%d" % len(calls)
        panes = loop.open_session_panes(Term(), "W", SLEEP, head=("H", SLEEP, "ODP__apiary"))
        self.assertEqual(calls[0], ("open", "dashboard"))
        self.assertEqual(calls[1][:3], ("split", "ODP__apiary", "horizontal"))
        self.assertEqual(calls[1][4], "W.0")
        self.assertEqual(len(calls), 2)
        self.assertEqual(panes, ["W.0", "W.2"])

    def test_window_start_runs_the_loop_without_a_pane(self):
        """`bzz start` opens the dashboard (and the head), starts the loop as a detached process with its output in a file, and opens no Beekeeper pane."""
        import argparse
        import contextlib
        import io
        from unittest import mock
        opened, spawned = [], []

        class Term:
            """Records the pane calls and shows no pane."""
            trace = None

            def list_panes(self, window):
                """No pane is open."""
                return []

            def open_window(self, window, title, command, colour=None, name=None):
                """Record the window."""
                opened.append((title, name))
                return "W.0"

            def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
                """Record a split."""
                opened.append((title, name))
                return "W.1"
        args = argparse.Namespace(foreground=False, terminal="headless", rounds=0, round_minutes=0)
        self.addCleanup(lambda: loop.start_pending_file().unlink(missing_ok=True))   # the real loop removes the pending marker; the mocked one cannot
        with mock.patch.object(loop, "get_terminal", return_value=Term()), mock.patch.object(loop, "loop_running", return_value=0), \
                mock.patch.object(loop, "spawn_detached", side_effect=lambda argv, log: spawned.append((argv, log))), contextlib.redirect_stdout(io.StringIO()):
            loop.cmd_start(args, self.conn)
        self.assertEqual(len(spawned), 1)
        self.assertIn("--foreground", spawned[0][0])
        self.assertTrue(all("BEEKEEPER" not in title for title, _ in opened))
        self.assertEqual(opened[0][1], "dashboard")

    def test_pane_of_a_hive_without_a_running_queen_replays_no_old_text(self):
        """The text of an ended Queen (hours old) is not drawn again into the pane when the pane is redrawn."""
        import io
        from unittest import mock

        class NoKeys:
            """Key reader without keys."""

            def __enter__(self):
                """Nothing to set up."""
                return self

            def __exit__(self, *exc):
                """Nothing to restore."""

            def get(self, timeout):
                """No key pressed."""
                return None
        self.queen('Old', 'done')
        conn = self.conn

        class Keep:
            """The test connection, which the pane may not close."""

            def __getattr__(self, name):
                """Everything but close goes to the real connection."""
                return getattr(conn, name)

            def close(self):
                """Keep the connection open for the test."""
        with mock.patch.object(hivepane.header, 'read_milestones', return_value=['old text']) as read,                 mock.patch.object(hivepane.db, 'connect', return_value=Keep()):
            hivepane.run('ODP', self.plan, out=io.StringIO(), interval=0, max_ticks=1, keys=NoKeys())
        read.assert_not_called()

    def test_start_closes_idle_hive_panes_and_keeps_the_one_with_a_running_agent(self):
        """Hive panes without a running agent and the head of an Apiary without a busy Hive pane are closed at a start; a Hive pane with a running Queen and its head stay."""
        runtime.upsert_agent(self.conn, 'ODP', 'Dina', {'plan': self.plan, 'phase': 'queen', 'status': 'running', 'pane_id': 'W.5'})
        titles = {'W.2': loop.apiary_pane_title('ODP', False), 'W.5': loop.hive_pane_title('ODP', self.plan, False),
                  'W.6': loop.hive_pane_title('ODP', 'plans/OTHER.md', False), 'W.7': loop.apiary_pane_title('XYZ', False),
                  'W.8': loop.hive_pane_title('XYZ', 'plans/X.md', False)}
        closed = []

        class Term:
            """Lists the panes and records which are closed."""

            def list_panes(self, window):
                """All panes are alive."""
                from haivequeen.terminal import PaneInfo
                return [PaneInfo(i, t, 1, True) for i, t in titles.items()]

            def close_pane(self, pane_id):
                """Record the closed pane."""
                closed.append(pane_id)
        self.assertEqual(sorted(loop.close_idle_hive_panes(self.conn, Term(), 'W')), ['W.6', 'W.7', 'W.8'])

    def test_no_word_project_in_the_texts_the_owner_reads(self):
        """The Beekeeper header, the log lines of the pane and the fault and error texts say Apiary, never project."""
        import inspect
        from haivequeen import faults, runtime, triage
        import datetime
        header = " ".join(t for t, _ in loop.head_lines(80, {"projects": ["a", "b"], "since": datetime.datetime.now(), "next_at": None, "busy": False}))
        self.assertNotIn("Project", header)
        for fn in (faults._check_agents, faults._check_idle):
            self.assertNotRegex(inspect.getsource(fn), r"\"[^\"]*[Pp]roject %s")
        self.assertNotRegex(inspect.getsource(runtime), r"f\"project \{project\} has no workspace")
        self.assertNotRegex(inspect.getsource(triage), r"not active in \{project\}")
        self.assertIn('"[Apiary %s] %s"', inspect.getsource(loop.Pane))


class HiveShareByQueenTest(PlacementCase):
    """F9: a Hive without a running Queen gets a narrow share from the start; when a Queen starts in it, its pane is built again with a larger share as long as no agent runs in it."""

    OTHER = "other/B-PLAN.md"

    def two_hives(self):
        """Two living Hives of the project and its environment."""
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        hives.create(self.conn, "testproj", self.OTHER, "claude", "4FA8FF")
        return self.envs()[0]

    def queen_of(self, plan, status="starting"):
        """The Queen row of a Hive."""
        return self.agent("Ada", phase="queen", status=status, plan_path=plan)

    def start(self, env, plan):
        """Start the Queen Ada of the Hive `plan` through start_queen and return its pane id."""
        from unittest import mock
        with mock.patch.object(loop, "runner_argv", return_value=SLEEP):
            return loop.start_queen(env, self.term, "HaiveQueen", {"name": "Ada", "plan": plan})

    def test_a_hive_without_queen_gets_the_narrow_share(self):
        """Hive B (no Queen) beside Hive A that has a Queen takes a quarter of the width; beside a Hive without Queen it takes half."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertEqual(self.calls[-1][2], 0.5)
        self.term_reset()
        self.queen_of(self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertEqual(self.calls[-1][2], 0.25)

    def term_reset(self):
        """Close the Hive and Apiary panes of the project so the next placement starts clean."""
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive and (loop.is_hive_title(p.title) or loop.is_apiary_title(p.title)):
                self.term.close_pane(p.id)
        self.calls.clear()

    def test_a_starting_queen_gets_her_pane_built_again_larger(self):
        """The pane of Hive A was built without a Queen; when Ada starts there and no agent runs in it, the pane is closed and built again with 3/4 of the space."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.queen_of(REL)
        new = self.start(env, REL)
        self.assertNotEqual(new, old)
        self.assertNotIn(old, [p.id for p in self.term.list_panes("HaiveQueen") if p.alive])
        self.assertEqual(self.calls[-1][2], 0.75)
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "Ada")["pane_id"], new)

    def test_a_pane_with_a_running_agent_keeps_its_size(self):
        """While an agent runs in the pane it is not closed, whatever the Queen start wants."""
        env = self.two_hives()
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        old = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.agent("Bea", phase="bee", status="running", plan_path=REL, pane_id=old)
        self.queen_of(REL)
        before = len(self.calls)
        self.assertEqual(self.start(env, REL), old)
        self.assertEqual(len(self.calls), before)

    def test_a_pane_built_with_its_queen_is_not_built_again(self):
        """A second Queen start in a pane that was already built large leaves it alone."""
        env = self.two_hives()
        self.queen_of(REL)
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", self.OTHER)
        first = self.start(env, REL)
        self.assertEqual(self.start(env, REL), first)
