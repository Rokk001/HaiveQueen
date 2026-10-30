"""Tests of the Hive sub-terminals: the Beekeeper dashboard only counts Hives, every living Hive has one pane of its own that shows the
Hive frame (Hive, Queen, Bees), runs its Queens one after the other and takes the commands dequeen / requeen with a confirmation."""
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import dashboard, db, hivepane, hives, loop, runtime
from haivequeen.terminal import pane_title
from haivequeen.terminal.headless import Headless

SYS = {'cores': 4, 'free_mb': 4000, 'total_mb': 8000, 'host': 'test', 'compiler_count': None}
SLEEP = [sys.executable, "-c", "import time;time.sleep(60)"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(lines):
    """Text of frame lines without colours, one string per line."""
    return ["".join(t for t, _ in parts) for parts in lines]


class DashboardCountTest(unittest.TestCase):
    """Requirement 1: the dashboard shows only the number of Hives and no Hive commands."""

    def setUp(self):
        """A database with two Hives, a Queen and a Bee."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / 'db')
        db.upsert_project(self.conn, 'alpha', None, 'cyan', 1)
        hives.create(self.conn, 'alpha', 'A-PLAN.md', 'claude', 'red')
        hives.create(self.conn, 'alpha', 'B-PLAN.md', 'codex', 'green')
        runtime.upsert_agent(self.conn, 'alpha', 'q1', {'plan': 'A-PLAN.md', 'phase': 'queen', 'status': 'running', 'person': 'Ada'})

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def text(self):
        """The rendered dashboard without colours."""
        return dashboard.render(self.conn, width=100, height=100, now=datetime.now(), cfg={}, sysinfo=SYS, color=False)

    def test_only_the_number_of_hives_is_shown(self):
        """One line per Apiary ('alpha: 2 Hives'), no Hive frame (plan, Queen name, box) and no Hive hint."""
        text = self.text()
        self.assertIn('alpha: 2 Hives', text)
        db.upsert_project(self.conn, 'beta', None, 'red', 0)
        hives.create(self.conn, 'beta', 'C-PLAN.md', 'claude', 'blue')
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path='B-PLAN.md'")
        text = self.text()
        self.assertIn('alpha: 1 Hives', text)
        self.assertIn('beta: 1 Hives', text)
        for word in ('A-PLAN', 'B-PLAN', 'Ada', '\u250c', '[H]', '[X]', '[R]', '[C]'):
            self.assertNotIn(word, text)

    def test_dashboard_says_apiary(self):
        """The project filter is called Apiary on the dashboard."""
        text = self.text()
        self.assertIn('Apiary: all', text)
        self.assertIn('[F] Apiary', text)
        self.assertNotIn('project: all', text)

    def test_hive_keys_are_gone(self):
        """H, C, X and R change nothing; the selection state and its functions no longer exist."""
        with mock.patch('haivequeen.hives.dequeen') as end, mock.patch('haivequeen.hives.requeen') as again:
            for key in ('h', 'c', 'x', 'x', 'r', 'r'):
                dashboard.handle_key(self.conn, key, None, [], now=10)
            end.assert_not_called()
            again.assert_not_called()
        self.assertFalse(hasattr(dashboard, 'select_hive'))
        self.assertFalse(hasattr(dashboard, '_hive_control'))

    def test_pause_confirmation_stays(self):
        """P still needs its second press (the confirm pattern is kept)."""
        dashboard.handle_key(self.conn, 'p', None, [], now=10.0)
        self.assertEqual(dashboard.pending_confirm(10.5), 'p')


class FrameTest(unittest.TestCase):
    """Requirement 3: the frame at the top of the Hive pane."""

    def setUp(self):
        """One Hive with a Queen and a Bee that reported a step."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / 'db')
        db.upsert_project(self.conn, 'ODP', None, 'FF9F43', 1)   # the colour of the Apiary
        hives.create(self.conn, 'ODP', 'plans/AI-LEVELS-PLAN.md', 'claude', 'FF9F43')
        self.plan = 'plans/AI-LEVELS-PLAN.md'

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def queen(self, **extra):
        """A running Queen of the Hive."""
        row = {'plan': self.plan, 'phase': 'queen', 'status': 'running', 'person': 'Ada', 'type': 'claude', 'modelShort': 'Son 5.5 med.',
               'started_at': db.now()}
        row.update(extra)
        runtime.upsert_agent(self.conn, 'ODP', 'Ada', row)

    def lines(self, **kw):
        """The frame lines."""
        return hivepane.frame_lines(self.conn, 'ODP', self.plan, 80, **kw)

    def test_labels_of_hive_queen_and_bee(self):
        """Hive line with project and plan stem, Queen line with name, model and runtime, one Bee line with task and step."""
        self.queen()
        runtime.upsert_agent(self.conn, 'ODP', 'bee1', {'plan': self.plan, 'phase': 'bee', 'status': 'running', 'person': 'Bea', 'task': 'Build'})
        db.add_event(self.conn, 'ODP', 'note', 'Bea H2: step 2/5 Build')
        text = plain(self.lines(now=datetime.now()))
        joined = "\n".join(text)
        self.assertIn('Hive: AI-LEVELS-PLAN', joined)
        self.assertNotIn('ODP', joined)   # the Apiary is named by its head pane
        queen = next(t for t in text if 'Queen:' in t)
        self.assertIn('Queen: Ada', queen)
        self.assertIn('Son 5.5 med.', queen)
        self.assertRegex(queen, r'\d\d:\d\d')
        self.assertIn('Bee: Bea Build 2/5 [##---]', joined)

    def test_every_line_has_the_apiary_colour(self):
        """The frame is drawn in the colour of the Apiary (project colour), not in a colour of its own."""
        self.queen()
        colours = {c for parts in self.lines() for t, c in parts if t.strip(' \u2502\u250c\u2510\u2514\u2518\u2500')} - {'yellow', 'darkgray'}
        self.assertEqual(colours, {'FF9F43'})
        self.conn.execute("UPDATE hives SET colour='123456'")
        self.assertEqual({c for parts in self.lines() for t, c in parts if t.strip(' │┌┐└┘─')} - {'yellow', 'darkgray'}, {'FF9F43'})

    def test_without_queen_the_line_shows_the_state_in_german(self):
        """Every Hive state without a running Queen has its German words on the Queen line."""
        cases = [('answer', 'wartet auf Antwort'), ('queenless', 'weisellos'), ('integration_wait', 'wartet auf Integration'),
                 ('codex_wait', 'wartet auf Codex'), ('handoff', '\u00dcbergabe'), ('waiting', 'wartet auf Start'),
                 ('starting', 'wartet auf Start'), ('resting', 'ruht bis 2026-11-01 12:00')]
        for state, expected in cases:
            with self.subTest(state=state):
                self.conn.execute('UPDATE hives SET state=?,reset_at=? WHERE plan_path=?', (state, '2026-11-01T12:00:00', self.plan))
                queen = next(t for t in plain(self.lines()) if 'Queen:' in t)
                self.assertIn('Queen: ' + expected, queen)

    def test_bees_follow_queen_notes(self):
        """A Bee that only shows up in the notes of its Queen is listed with its step and leaves the frame at its last step."""
        self.queen()
        self.conn.execute("INSERT INTO packages(project,plan_path,id) VALUES ('ODP',?,'P1')", (self.plan,))
        db.add_event(self.conn, 'ODP', 'note', 'Cleo P1: step 1/3 Draft', agent='Ada')
        self.assertIn('Bee: Cleo Draft 1/3', "\n".join(plain(self.lines())))
        db.add_event(self.conn, 'ODP', 'note', 'Cleo P1: step 3/3 Done', agent='Ada')
        self.assertNotIn('Cleo', "\n".join(plain(self.lines())))

    def test_hive_without_queen_is_compact(self):
        """A Hive without a running Queen draws only its name and its state: no box, no key hint, no Bee lines."""
        self.conn.execute("UPDATE hives SET state='answer' WHERE plan_path=?", (self.plan,))
        lines = plain(self.lines())
        self.assertEqual(lines[0].strip(), 'Hive: AI-LEVELS-PLAN')
        self.assertEqual(lines[1].strip(), 'Queen: wartet auf Antwort')
        self.assertEqual(len(lines), 2)
        self.queen()
        self.assertGreater(len(self.lines()), 4)   # a running Queen: the big frame again
        self.conn.execute("UPDATE agents SET status='done'")
        self.assertEqual(len(self.lines()), 2)
        self.assertEqual(len(self.lines(message='X nochmal')), 3)   # a pending confirmation still shows

    def test_progress_bar(self):
        """The Bee bar is built from n/m with five cells."""
        self.assertEqual(hivepane.bar('1/5'), '[#----]')
        self.assertEqual(hivepane.bar('5/5'), '[#####]')
        self.assertEqual(hivepane.bar('2/3'), '[###--]')
        self.assertEqual(hivepane.bar(''), '')

    def test_head_of_an_apiary(self):
        """The slim head pane of an Apiary says 'Apiary <Name>' in the Apiary colour."""
        self.assertEqual(hivepane.head_lines(self.conn, 'ODP', 40), [[('Apiary ODP', 'FF9F43')]])

    def test_message_line_is_shown(self):
        """A pending confirmation text appears in the frame."""
        self.assertIn('nochmal', "\n".join(plain(self.lines(message='Dequeen: X nochmal dr\u00fccken'))))


class KeysTest(unittest.TestCase):
    """Requirement 4: confirmation logic of the keys in the pane."""

    def test_second_press_executes(self):
        """X: the first press asks, the second within the time returns the action."""
        st = hivepane.new_state()
        self.assertIsNone(hivepane.handle_key(st, 'x', 10.0, ['claude']))
        self.assertIn('nochmal', st['message'])
        self.assertEqual(hivepane.handle_key(st, 'x', 11.0, ['claude']), ('dequeen', None))
        self.assertEqual(st['message'], '')

    def test_requeen(self):
        """R works like X."""
        st = hivepane.new_state()
        hivepane.handle_key(st, 'r', 10.0, [])
        self.assertEqual(hivepane.handle_key(st, 'r', 11.0, []), ('requeen', None))

    def test_other_key_or_late_press_cancels(self):
        """Any other key cancels, a press after the confirm time is only a new first press."""
        st = hivepane.new_state()
        hivepane.handle_key(st, 'x', 10.0, [])
        self.assertIsNone(hivepane.handle_key(st, 'q', 10.5, []))
        self.assertIsNone(hivepane.handle_key(st, 'x', 11.0, []))   # first press again, the cancel cleared the first one
        self.assertIsNone(hivepane.handle_key(st, 'x', 11.0 + hivepane.CONFIRM_SECONDS + 1, []))
        self.assertEqual(hivepane.handle_key(st, 'x', 12.0 + hivepane.CONFIRM_SECONDS, []), ('dequeen', None))

    def test_x_then_r_does_not_mix(self):
        """A confirmation belongs to its key: X then R is no execution."""
        st = hivepane.new_state()
        hivepane.handle_key(st, 'x', 10.0, [])
        self.assertIsNone(hivepane.handle_key(st, 'r', 10.5, []))

    def test_requeen_with_provider(self):
        """C lists the providers, a number picks one, C again confirms and returns the provider."""
        st = hivepane.new_state()
        providers = ['claude', 'codex']
        self.assertIsNone(hivepane.handle_key(st, 'c', 10.0, providers))
        self.assertIn('1 claude', st['message'])
        self.assertIn('2 codex', st['message'])
        self.assertIsNone(hivepane.handle_key(st, '2', 10.5, providers))
        self.assertIn('codex', st['message'])
        self.assertIn('nochmal', st['message'])
        self.assertEqual(hivepane.handle_key(st, 'c', 11.0, providers), ('requeen', 'codex'))

    def test_wrong_number_cancels(self):
        """A number without a provider cancels the choice."""
        st = hivepane.new_state()
        hivepane.handle_key(st, 'c', 10.0, ['claude'])
        self.assertIsNone(hivepane.handle_key(st, '5', 10.5, ['claude']))
        self.assertIsNone(hivepane.handle_key(st, 'c', 11.0, ['claude']))   # starts the list again, no execution


class ExecuteTest(RoundCase):
    """The confirmed action calls the Hive logic of `bzz dequeen` / `bzz requeen`."""

    def setUp(self):
        """One Hive of the dummy project."""
        super().setUp()
        self.plan("READY", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")

    def test_dequeen_calls_hives(self):
        """dequeen leaves the Hive queenless."""
        msg = hivepane.execute(self.conn, "testproj", REL, ("dequeen", None), self.machine)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "queenless")
        self.assertEqual(msg, "Dequeen: fertig")

    def test_requeen_checks_the_provider(self):
        """A provider that does not offer the Queen class is refused with the text of hives.requeen, the Hive stays as it was."""
        msg = hivepane.execute(self.conn, "testproj", REL, ("requeen", "nonesuch"), self.machine)
        self.assertIn("provider does not offer the Queen class", msg)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "waiting")

    def test_requeen_sets_requeen_and_schedules(self):
        """requeen puts the Hive to 'requeen' and runs the schedule round for this plan."""
        with mock.patch("haivequeen.hives.schedule_round") as rnd:
            msg = hivepane.execute(self.conn, "testproj", REL, ("requeen", None), self.machine)
        self.assertEqual(msg, "Requeen: fertig")
        rnd.assert_called_once()
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "requeen")


class PaneOpenTest(RoundCase):
    """Requirement 2: one permanent pane per Hive, opened by the loop side."""

    def setUp(self):
        """A headless terminal; the Hive pane command is a sleeper."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        self.term = Headless(self.base / "panes")
        self.chief = self.term.open_window("HaiveQueen", pane_title("all projects", "QUEEN", "Queen", "m", True), SLEEP, name="chief")
        self.argv = mock.patch("haivequeen.loop.hive_argv", return_value=SLEEP)
        self.argv.start()
        self.head_argv = mock.patch("haivequeen.loop.apiary_argv", return_value=SLEEP)   # the real head pane ends at once when the Apiary has no Hive row
        self.head_argv.start()

    def tearDown(self):
        """End every pane process."""
        self.argv.stop()
        self.head_argv.stop()
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive:
                self.term.close_pane(p.id)
        super().tearDown()

    def hive_panes(self):
        """Live panes with a Hive title."""
        return [p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_hive_title(p.title)]

    def test_title_names_project_and_plan(self):
        """The title is the generic pane title with who=Hive and the plan stem as job."""
        self.assertEqual(loop.hive_pane_title("ODP", "plans/AI-LEVELS-PLAN.md", True), pane_title("ODP", "Hive", "AI-LEVELS-PLAN", "-", True))
        self.assertTrue(loop.is_hive_title(loop.hive_pane_title("ODP", "x/Y.md", False)))
        self.assertFalse(loop.is_hive_title(pane_title("ODP", "Agent ADA", "Y", "m", False)))

    def test_ensure_opens_once(self):
        """The pane is opened once and found again by its title; a second Hive gets a second pane."""
        env = self.envs()[0]
        first = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertEqual(loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL), first)
        self.assertEqual(len(self.hive_panes()), 1)
        second = loop.ensure_hive_pane(env, self.term, "HaiveQueen", "other/B-PLAN.md")
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.hive_panes()), 2)

    def test_dead_pane_is_opened_again(self):
        """When the pane was closed while the Hive lives, the next call opens it again."""
        env = self.envs()[0]
        first = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.term.close_pane(first)
        second = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.hive_panes()), 1)

    def test_the_pane_command_is_the_hive_pane_module(self):
        """hive_argv starts haivequeen.hivepane for the project and the plan."""
        self.argv.stop()
        try:
            cmd = loop.hive_argv(self.env(), REL)
        finally:
            self.argv.start()
        self.assertIn("haivequeen.hivepane", " ".join(cmd))
        self.assertIn(REL, cmd)
        self.assertIn("testproj", cmd)
        self.assertNotIn("", cmd)

    def test_queens_of_one_hive_share_the_pane(self):
        """Two Queens one after the other: launch.json is written for each, only one Hive pane exists, the Queen row points to it."""
        env = self.envs()[0]
        for name in ("Ada", "Bea"):
            spec = {"name": name, "provider": "claude", "protocol": "claude", "plan": REL, "package": "", "phase": "queen", "cwd": str(self.wt),
                    "prompt_file": str(env.run_dir(name) / "prompt.md"), "person": name, "model_long": "Claude Sonnet 5.5 medium"}
            env.run_dir(name).mkdir(parents=True, exist_ok=True)
            env.update_agent(name, {"plan_path": REL, "phase": "queen", "status": "starting"})
            pane = loop.start_queen(env, self.term, "HaiveQueen", spec)
            self.assertTrue(loop.is_hive_title(next(p.title for p in self.hive_panes() if p.id == pane)))
            data = json.loads((env.run_dir(name) / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(data["argv"][-1:], loop.forward_env_args()[-1:] or data["argv"][-1:])
            self.assertIn("agent_runner", " ".join(data["argv"]))
            self.assertEqual(self.ag(name)["pane_id"], pane)
        self.assertEqual(len(self.hive_panes()), 1)

    def test_placement_head_first_then_hives_to_the_right(self):
        """Per Apiary: the head pane first (first block beside the Beekeeper pane), its Hives below the head and then each to the right of the
        previous one; the head of a new Apiary splits below the last pane of the last block."""
        env = self.envs()[0]
        calls = []
        orig = self.term.split_pane
        self.term.split_pane = lambda *a, **k: (calls.append((a[6], a[4])), orig(*a, **k))[1]
        h1 = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        head1 = next(p.id for p in self.term.list_panes("HaiveQueen") if loop.is_apiary_title(p.title))
        h2 = loop.ensure_hive_pane(env, self.term, "HaiveQueen", "other/B-PLAN.md")
        self.assertEqual(calls, [(self.chief, "horizontal"), (head1, "vertical"), (h1, "horizontal")])
        env2 = self.env("testproj")
        env2.name = "other"
        h3 = loop.ensure_hive_pane(env2, self.term, "HaiveQueen", "x/C-PLAN.md")
        head2 = next(p.id for p in self.term.list_panes("HaiveQueen") if loop.is_apiary_title(p.title) and p.id != head1)
        self.assertEqual(calls[3], (h2, "vertical"))   # the new Apiary block goes below the last pane of the last block
        self.assertEqual(calls[4], (head2, "vertical"))
        self.assertEqual(len(self.hive_panes()), 3)
        self.assertEqual(len([p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_apiary_title(p.title)]), 2)
        self.assertNotEqual(h3, h1)

    def test_apiary_title(self):
        """The head pane title is the generic title with who=Apiary."""
        self.assertEqual(loop.apiary_pane_title("ODP", True), pane_title("ODP", "Apiary", "ODP", "-", True))
        self.assertTrue(loop.is_apiary_title(loop.apiary_pane_title("ODP", False)))
        self.assertFalse(loop.is_hive_title(loop.apiary_pane_title("ODP", False)))

    def test_head_pane_goes_with_its_last_hive(self):
        """close_session_panes closes the head of an Apiary without a living Hive and keeps the head of one with a living Hive."""
        env = self.envs()[0]
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        loop.close_session_panes(self.conn, self.term, "HaiveQueen")
        self.assertEqual(len([p for p in self.term.list_panes("HaiveQueen") if p.alive and loop.is_apiary_title(p.title)]), 1)
        self.conn.execute("UPDATE hives SET state='done'")
        loop.close_session_panes(self.conn, self.term, "HaiveQueen")
        self.assertEqual([p for p in self.term.list_panes("HaiveQueen") if p.alive and (loop.is_apiary_title(p.title) or loop.is_hive_title(p.title))], [])

    def test_session_stop_keeps_living_hive_panes(self):
        """close_session_panes closes the Hive pane of a done Hive but not the pane of a living Hive."""
        env = self.envs()[0]
        live = loop.ensure_hive_pane(env, self.term, "HaiveQueen", REL)
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        gone = loop.ensure_hive_pane(env, self.term, "HaiveQueen", "other/B-PLAN.md")
        hives.create(self.conn, "testproj", "other/B-PLAN.md", "claude", "C77DFF")
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path='other/B-PLAN.md'")
        closed = loop.close_session_panes(self.conn, self.term, "HaiveQueen")
        self.assertIn(gone, closed)
        self.assertNotIn(live, closed)
        alive = {p.id: p.alive for p in self.term.list_panes("HaiveQueen")}
        self.assertTrue(alive[live])
        self.assertFalse(alive[gone])

    def test_round_opens_a_pane_for_a_hive_without_queen(self):
        """A Hive that rests (no Queen) still gets its pane from the schedule round; done Hives get none."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        self.conn.execute("UPDATE hives SET state='queenless' WHERE plan_path=?", (REL,))
        self.round(terminal=self.term)
        self.assertEqual(len(self.hive_panes()), 1)
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path=?", (REL,))
        self.round(terminal=self.term)
        self.assertEqual(len(self.hive_panes()), 1)   # still the old one: no new pane for a done Hive

    def test_a_queen_start_does_not_wait_for_a_runner_pid(self):
        """_do_start of a Queen opens/finds the Hive pane, writes launch.json and returns although no runner sets a pid."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round(terminal=self.term, poll=0.01, tries=1)
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertEqual(queens[0]["status"], "starting")
        self.assertTrue((self.env().run_dir(queens[0]["name"]) / "launch.json").is_file())
        self.assertEqual(len(self.hive_panes()), 1)
        self.assertEqual(queens[0]["pane_id"], self.hive_panes()[0].id)


class LayoutTest(RoundCase):
    """Requirement 7: the wanted layout (big Hives with a running Queen, small ones without) is computed from the database and applied by resizing."""

    def setUp(self):
        """Two Apiaries: testproj with two Hives, other with one."""
        super().setUp()
        db.upsert_project(self.conn, "other", str(self.base / "repo-other"), "red", 0)
        self.conn.execute("UPDATE projects SET colour='4FA8FF' WHERE name='testproj'")
        hives.create(self.conn, "testproj", "a/A-PLAN.md", "claude", "red")
        hives.create(self.conn, "testproj", "a/B-PLAN.md", "claude", "red")
        hives.create(self.conn, "other", "c/C-PLAN.md", "claude", "red")

    def test_blocks_and_big_small(self):
        """Hives with a running or starting Queen are big, the others small; an Apiary with no working Hive is a small block."""
        from haivequeen import hivelayout
        self.agent("Ada", phase="queen", status="running", plan_path="a/A-PLAN.md", started=1)
        blocks = hivelayout.compute(self.conn)
        self.assertEqual([b["project"] for b in blocks], ["other", "testproj"])
        other, test = blocks
        self.assertEqual(test["colour"], "4FA8FF")
        self.assertEqual([(h["plan"], h["big"]) for h in test["hives"]], [("a/A-PLAN.md", True), ("a/B-PLAN.md", False)])
        self.assertGreater(test["weight"], other["weight"])
        self.assertGreater(test["hives"][0]["width"], test["hives"][1]["width"])
        self.assertEqual(other["hives"][0]["big"], False)

    def test_done_hives_and_empty_apiaries_vanish(self):
        """A done Hive is not part of the layout, an Apiary without a living Hive has no block."""
        from haivequeen import hivelayout
        self.conn.execute("UPDATE hives SET state='done' WHERE project='other'")
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path='a/B-PLAN.md'")
        blocks = hivelayout.compute(self.conn)
        self.assertEqual([b["project"] for b in blocks], ["testproj"])
        self.assertEqual([h["plan"] for h in blocks[0]["hives"]], ["a/A-PLAN.md"])

    def test_shares(self):
        """Height shares of the blocks and width shares of the Hives in a block add up to 1; the freed space goes to the working Hive."""
        from haivequeen import hivelayout
        self.agent("Ada", phase="queen", status="running", plan_path="a/A-PLAN.md", started=1)
        blocks = hivelayout.compute(self.conn)
        heights = hivelayout.height_shares(blocks)
        self.assertAlmostEqual(sum(heights.values()), 1.0)
        self.assertGreater(heights["testproj"], 0.5)
        widths = hivelayout.width_shares(blocks[1])
        self.assertAlmostEqual(sum(widths.values()), 1.0)
        self.assertGreater(widths["a/A-PLAN.md"], widths["a/B-PLAN.md"])

    def test_apply_resizes_through_the_backend_and_never_raises(self):
        """apply sends the shares to the backend's resize_pane; a backend that cannot resize is skipped without an error."""
        from haivequeen import hivelayout
        calls = []

        class Fake(Headless):
            """Headless backend that records resizes."""

            def resize_pane(self, pane_id, axis, share):
                """Record the request."""
                calls.append((pane_id, axis, round(share, 2)))

        term = Fake(self.base / "panes")
        ids = {}
        for proj, plan in (("testproj", "a/A-PLAN.md"), ("testproj", "a/B-PLAN.md"), ("other", "c/C-PLAN.md")):
            ids[plan] = term.split_pane("HaiveQueen", loop.hive_pane_title(proj, plan, True), "white", SLEEP, "horizontal", 0.5, None, "h%d" % len(ids))
        try:
            self.agent("Ada", phase="queen", status="running", plan_path="a/A-PLAN.md", started=1)
            hivelayout.apply(term, "HaiveQueen", self.conn, True)
            self.assertIn((ids["a/A-PLAN.md"], "x", 0.75), calls)
            self.assertIn((ids["a/B-PLAN.md"], "x", 0.25), calls)
            self.assertTrue(any(c[1] == "y" for c in calls))
            hivelayout.apply(Headless(self.base / "panes"), "HaiveQueen", self.conn, True)   # a backend that cannot resize: no exception
        finally:
            for p in term.list_panes("HaiveQueen"):
                if p.alive:
                    term.close_pane(p.id)

    def test_base_backend_cannot_resize(self):
        """The default resize_pane of a backend raises TerminalError (wt: no way to resize without raising the window)."""
        from haivequeen.terminal.base import TerminalError
        with self.assertRaises(TerminalError):
            Headless(self.base / "panes").resize_pane("W.0", "x", 0.5)


class LaunchTest(RoundCase):
    """The Hive pane process starts the Queen runners, one at a time."""

    def setUp(self):
        """A Hive, a dummy runner that sets its pid and ends."""
        super().setUp()
        self.plan("READY", [{"id": "P1", "title": "First"}])
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        self.env_ = self.envs()[0]
        self.dummy = self.base / "dummy.py"
        self.dummy.write_text("import sys, time\nfrom pathlib import Path\nPath(sys.argv[1]).write_text('ran')\ntime.sleep(float(sys.argv[2]))\n", encoding="utf-8")

    def row(self, name, wait="0"):
        """A starting Queen row with a launch.json for the dummy runner."""
        folder = self.env_.run_dir(name)
        folder.mkdir(parents=True, exist_ok=True)
        self.agent(name, phase="queen", status="starting", started=0)
        (folder / "launch.json").write_text(json.dumps({"argv": [sys.executable, str(self.dummy), str(folder / "ran.txt"), wait]}), encoding="utf-8")
        return folder

    def test_starts_a_starting_queen_once(self):
        """A starting Queen with launch.json is started; a second call does not start it again."""
        folder = self.row("Ada")
        proc = hivepane.launch_due(self.conn, "testproj", REL, None)
        self.assertIsNotNone(proc)
        proc.wait(timeout=20)
        self.assertEqual((folder / "ran.txt").read_text(), "ran")
        (folder / "ran.txt").unlink()
        self.assertIsNone(hivepane.launch_due(self.conn, "testproj", REL, None))
        self.assertFalse((folder / "ran.txt").exists())

    def test_one_queen_at_a_time(self):
        """While a runner of this pane runs, no second one is started."""
        self.row("Ada", "30")
        proc = hivepane.launch_due(self.conn, "testproj", REL, None)
        try:
            self.row("Bea")
            self.assertIsNone(hivepane.launch_due(self.conn, "testproj", REL, proc))
        finally:
            proc.kill()
            proc.wait()

    def test_no_header_process_in_the_runner(self):
        """The runner gets BZZ_NO_HEADER so it does not draw a second header over the Hive frame."""
        folder = self.row("Ada")
        with mock.patch("haivequeen.hivepane.subprocess.Popen") as popen:
            hivepane.launch_due(self.conn, "testproj", REL, None)
        self.assertEqual(popen.call_args.kwargs["env"].get("BZZ_NO_HEADER"), "1")

    def test_other_plans_and_missing_launch_file_are_ignored(self):
        """A Queen of another plan, or one without launch.json yet, is not started."""
        folder = self.row("Ada")
        self.agent("Cleo", phase="queen", status="starting", plan_path="other.md", started=0)
        self.env_.run_dir("Cleo").mkdir(parents=True, exist_ok=True)
        (folder / "launch.json").rename(folder / "x.json")
        self.assertIsNone(hivepane.launch_due(self.conn, "testproj", REL, None))

    def test_finished(self):
        """The pane process ends when the Hive is done or gone."""
        self.assertFalse(hivepane.finished(self.conn, "testproj", REL))
        self.conn.execute("UPDATE hives SET state='done' WHERE plan_path=?", (REL,))
        self.assertTrue(hivepane.finished(self.conn, "testproj", REL))
        self.assertTrue(hivepane.finished(self.conn, "testproj", "nope.md"))


class StaleQueenTest(RoundCase):
    """A Queen that waits for its Hive pane to start it is not failed early; one that never starts still is."""

    def test_grace_and_failure(self):
        """A starting Queen without pid survives two minutes, then the stale-row repair fails it (the pane never started it)."""
        from haivequeen import routine
        self.plan("READY", [])
        self.agent("Ada", phase="queen", status="starting", started=1)
        routine.repair_stale_agents(self.env())
        self.assertEqual(self.ag("Ada")["status"], "starting")
        self.agent("Ada", phase="queen", status="starting", started=5)
        routine.repair_stale_agents(self.env())
        self.assertEqual(self.ag("Ada")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
