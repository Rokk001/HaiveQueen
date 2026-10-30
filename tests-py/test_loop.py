"""Tests of the multi-project loop (D1, D4), the pause and drain gate and re-attach (D8), the triage start (D5), the brain, the headless terminal start and the loop itself."""
import json
import os
import re
import sys
import time
import unittest

from bzztest import REL, RoundCase, minutes_ago, provider, runtime
from haivequeen import db, export, loop, routine, runstate, triage
from haivequeen import platform as osplat
from haivequeen import scheduler as sch
from haivequeen.terminal.headless import Headless

ONE = [{"id": "P1", "title": "first"}]


class MultiProjectTest(RoundCase):
    """Two dummy projects share the slots; priority from project.toml first, ties by the oldest waiting package."""

    shared_logs = False

    def setUp(self):
        """One claude slot for everybody; two projects with a plan and a worktree row each."""
        super().setUp()
        self.machine["providers"] = [provider("claude", 1)]
        self.add_project("alpha", 5)
        self.add_project("beta", 0)
        for name in ("alpha", "beta"):
            self.plan("IN_PROGRESS", ONE, project=name)
            self.agent("DUMMY-SEED-work", project=name, package="SEED", started=30, ended=20)

    # Verify the Queen-based loop behavior.
    def test_priority_first(self):
        """The shared slot goes to the project with the higher priority; the other waits."""
        res = self.round()
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "alpha") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")
        self.assertIsNone(next((a for a in runtime.list_agents(self.conn, "beta") if a["phase"] == "queen" and a["status"] == "running"), None))
        self.assertEqual(res["projects"]["alpha"]["started"], 1)
        self.assertEqual(res["projects"]["beta"]["started"], 0)

    # Verify the Queen-based loop behavior.
    def test_tie_by_oldest_waiting(self):
        """At equal priority the project with the older waiting package goes first."""
        self.conn.execute("UPDATE projects SET priority=0")
        self.add_project("alpha", 0)
        runtime.kv_set(self.conn, "beta", "wait:%s|P1" % REL, minutes_ago(90))
        runtime.kv_set(self.conn, "alpha", "wait:%s|P1" % REL, minutes_ago(10))
        self.round()
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "beta") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")
        self.assertIsNone(next((a for a in runtime.list_agents(self.conn, "alpha") if a["phase"] == "queen" and a["status"] == "running"), None))
        self.assertEqual(runtime.kv_get(self.conn, "beta", "wait:%s|P1" % REL), None, "the marker of a started package is cleared")

    # Verify the Queen-based loop behavior.
    def test_same_names_in_two_projects(self):
        """Two slots: both projects start their package with the same agent name, rows stay separate."""
        self.machine["providers"] = [provider("claude", 2)]
        self.round()
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "alpha") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "beta") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")
        self.assertEqual(len(runtime.list_agents(self.conn, None, "active")), 4)
        # the slots count across projects: a third package of either project finds no free slot
        self.assertEqual(sch.provider_free(self.env("alpha").providers, [a for a in runtime.list_agents(self.conn, None, "active") if a["status"] == "running"])["claude"], 0)

    # Verify the Queen-based loop behavior.
    def test_project_data_in_events_and_logs(self):
        """Every project writes its own log folder (D4: the project is visible everywhere)."""
        self.round()
        self.assertTrue((runtime.mirror_dir("alpha") / "ROUNDS.log").is_file())
        self.assertNotEqual(runtime.mirror_dir("alpha"), runtime.mirror_dir("beta"))
        self.assertTrue(any(a["phase"] == "queen" for a in runtime.list_agents(self.conn, "alpha")))

    def test_priority_from_project_toml(self):
        """The order uses the priority in project.toml, not the stale database column."""
        (self.base / "repo-beta" / ".haivequeen" / "project.toml").write_text('name = "beta"\npriority = 9\nplanGlobs = ["docs/plans/**/*.md"]\n', encoding="utf-8")
        self.assertEqual([e.name for e in sch.order_projects(self.envs())], ["beta", "alpha", "testproj"])


class RunStateTest(RoundCase):
    """Pause and drain gate (D8)."""

    def setUp(self):
        """A plan with a worktree row."""
        super().setUp()
        self.plan("IN_PROGRESS", ONE)
        self.seed()

    # Verify the Queen-based loop behavior.
    def test_pause_blocks_starts(self):
        """While paused the round starts nothing and says why; resume lifts it."""
        runstate.pause(self.conn)
        res = self.round()
        self.assertEqual(res["skipped"], "PAUSED")
        self.assertIsNone(next((a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"), None))
        self.assertIn("round skipped (PAUSED)", self.log())
        runstate.resume(self.conn)
        self.round()
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")

    # Verify the Queen-based loop behavior.
    def test_pause_keeps_running_agents(self):
        """A running agent is not touched by a pause; a drain reports drained once it has ended."""
        self.round()
        runstate.drain(self.conn)
        self.assertEqual(runstate.get_state(self.conn)["state"], "DRAINING")
        self.assertEqual(self.round()["skipped"], "DRAINING")
        self.assertEqual(next((a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"), None)["status"], "running")
        queen = next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")
        self.finish(queen["name"])
        runtime.upsert_agent(self.conn, "testproj", queen["name"], {"pid": None})
        self.assertEqual(runstate.settle(self.conn)["state"], "DRAINED")
        self.assertEqual(len([e for e in runtime.list_events(self.conn, "*") if e["kind"] == "drained"]), 1)

    # Verify the Queen-based loop behavior.
    def test_stop_flag_and_low_ram(self):
        """The STOP flag and low free RAM skip the round, too."""
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text("x")
        self.assertEqual(self.round()["skipped"], "STOP")
        loop.stop_flag().unlink()
        res = self.round(free_ram=lambda: 0.5)
        self.assertIn("free RAM", res["skipped"])
        self.assertIsNone(next((a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"), None))


class LoopTest(RoundCase):
    """The loop: re-attach at the start, one lock, STOP, round errors."""

    # Verify reattachment and complete any Queen started during the round.
    def test_reattach_and_end(self):
        """At the start a running agent with a living pid stays, one with a dead pid fails; the loop ends after the rounds and removes its lock."""
        self.plan("IN_PROGRESS", ONE)
        self.agent("LIVE-work", package="P1", status="running", pid=os.getpid(), started=5)
        self.agent("GONE-work", package="P2", status="running", pid=2000000000, started=5)
        seen = {}

        def sleep(_s):
            """While the loop waits for the running agent: note the statuses, then let the agent end."""
            seen.update(live=self.ag("LIVE-work")["status"], gone=self.ag("GONE-work")["status"])
            self.finish("LIVE-work")
            for agent in runtime.list_agents(self.conn, "testproj", "active"):
                if agent.get("phase") == "queen" and agent.get("status") in ("running", "starting"):
                    self.finish(agent["name"])
                    runtime.upsert_agent(self.conn, "testproj", agent["name"], {"pid": None})

        kw = {"free_ram": lambda: 16.0, "settle": 0}
        self.assertEqual(loop.run_loop(self.conn, self.machine, rounds=1, sleep=sleep, round_kwargs=kw), 0)
        self.assertEqual(seen, {"live": "running", "gone": "failed"})
        self.assertFalse(loop.lock_file().exists())
        self.assertIn("loop started", self.log())
        self.assertEqual(loop.read_state(self.conn)["status"], "idle")

    def test_second_loop_refused(self):
        """With a living lock a second loop ends at once."""
        loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            loop.lock_file().write_text(str(child.pid))
            self.assertEqual(loop.loop_running(), child.pid)
            self.assertEqual(loop.run_loop(self.conn, self.machine, rounds=1), 0)
            self.assertEqual(loop.lock_file().read_text(), str(child.pid), "the other loop's lock is untouched")
        finally:
            child.kill()
            child.wait()

    def test_stop_ends_loop(self):
        """With the STOP flag set no round runs; the loop waits for running agents and ends."""
        self.plan("IN_PROGRESS", ONE)
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text("x")
        self.assertEqual(loop.run_loop(self.conn, self.machine, sleep=lambda s: None), 0)
        self.assertIn("STOP found", self.log())
        self.assertIn("Loop ended", (runtime.mirror_dir("testproj") / "ORCH-EVENTS.log").read_text(encoding="utf-8"))

    def test_stop_ends_loop_when_a_running_runner_dies(self):
        """A runner that dies while the stopped loop waits is marked failed, so the loop ends instead of waiting for the orphaned row."""
        import subprocess
        self.plan("IN_PROGRESS", ONE)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        runtime.upsert_agent(self.conn, "testproj", "DUMMY-T-triage", {"type": "claude", "provider": "claude", "plan": REL, "package": "P1", "phase": "triage",
                                                                     "status": "running", "startedAt": db.now(), "pid": proc.pid})
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text("x")
        calls = []

        def sleeper(seconds):
            """Kill the runner on the first wait; fail the test when the loop is still waiting after many waits."""
            calls.append(seconds)
            if len(calls) == 1:
                proc.kill()
                proc.wait()
            self.assertLess(len(calls), 50, "the stopped loop keeps waiting for an orphaned row")
        try:
            self.assertEqual(loop.run_loop(self.conn, self.machine, sleep=sleeper), 0)
        finally:
            proc.kill()
            proc.wait()
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "DUMMY-T-triage")["status"], "failed")

    def test_daily_export_in_loop(self):
        """When the daily export is due the loop writes the export file; when not due it writes none."""
        folder = export.export_dir()
        orig = export.export_due
        try:
            export.export_due = lambda now, folder=None: False
            loop.run_loop(self.conn, self.machine, rounds=1, sleep=lambda s: None, round_minutes=0.0001)
            self.assertFalse(folder.exists() and list(folder.glob("haivequeen-*.db")))
            export.export_due = lambda now, folder=None: True
            loop.run_loop(self.conn, self.machine, rounds=1, sleep=lambda s: None, round_minutes=0.0001)
        finally:
            export.export_due = orig
        self.assertEqual(len(list(folder.glob("haivequeen-*.db"))), 1)
        self.assertIn("database exported to", self.log())

    def test_round_error_does_not_stop(self):
        """An exception in a round is logged and the loop goes on."""
        orig = loop.run_round
        loop.run_round = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertEqual(loop.run_loop(self.conn, self.machine, rounds=2, sleep=lambda s: None, round_minutes=0.0001), 0)
        finally:
            loop.run_round = orig
        self.assertEqual(self.log().count("ROUND ERROR boom"), 2)

    def test_header_and_wrap(self):
        """Header lines and word wrap."""
        self.assertEqual(loop.wrap_text("aaa bbb ccc", 7), ["aaa bbb", "ccc"])
        self.assertEqual(loop.wrap_text("abcdefghij", 4), ["abcd", "efgh", "ij"])
        hl = loop.head_lines(80, {"busy": True, "projects": ["a", "b"]})
        self.assertIn("Beekeeper", hl[0][0])
        self.assertIn("round running", hl[0][0])
        self.assertIn("Projects: a, b", hl[1][0])
        hl = loop.head_lines(80, {"run_state": "PAUSED"})
        self.assertIn("PAUSED", hl[0][0])

    def test_window_is_one_for_all(self):
        """One window for the Queen, the dashboard and every agent: a project cannot set its own window name."""
        self.assertEqual(sch.build_settings({}, {})["window"], "HaiveQueen")
        self.assertEqual(sch.build_settings({}, {"window": "ODP"})["window"], "HaiveQueen")
        self.assertEqual(sch.build_settings({"window": "Main"}, {"window": "ODP"})["window"], "Main")

    def test_pane_lock_held_until_exit(self):
        """A closing pane takes pane.lock for its own process and keeps it."""
        from unittest import mock
        with mock.patch.object(loop, "pane_lock_file", return_value=self.base / "pane.lock"):
            self.assertTrue(loop.hold_pane_lock_until_exit())
            self.assertEqual((self.base / "pane.lock").read_text(encoding="ascii").strip(), str(os.getpid()))

    def test_agent_names_and_queen_title(self):
        """At least 24 unique agent names, none is Queen, and the pane of the main agent is titled BEEKEEPER."""
        self.assertGreaterEqual(len(loop.AGENT_NAMES), 24)
        self.assertEqual(len(set(loop.AGENT_NAMES)), len(loop.AGENT_NAMES))
        self.assertNotIn("Queen", loop.AGENT_NAMES)
        self.assertEqual(loop.AGENT_NAMES[:3], ["Ada", "Bea", "Cleo"])
        self.assertEqual(loop.AGENT_NAMES[23], "Zoe")   # the first 24 names keep their order, later ones were added
        self.assertIn("| all projects | BEEKEEPER | job: Beekeeper |", loop.queen_pane_title("m", True))


class TriageStartTest(RoundCase):
    """Old verify results, decisions and questions are handed to the Queen without triage."""

    def setUp(self):
        """A package with three finished work/verify pairs, each verify with open items."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file\nOffene Anforderungen: %d" % (3 - i))   # the count falls, so the progress guard does not stop the package

    # Verify legacy failures do not start triage or lock the package.
    def test_old_failures_start_queen_not_triage(self):
        """Three old verify failures start a Queen without locking or triage."""
        self.round()
        pkg = next(p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1")
        self.assertFalse(pkg["locked"])
        agents = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in agents if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertFalse([a for a in agents if a["phase"] == "triage"])

    # Verify an existing technical decision remains available to the Queen.
    def test_decision_is_preserved_without_triage(self):
        """A recorded decision stays available and does not start a triage agent."""
        runtime.add_decision(self.conn, "testproj", "auto", "split the file", REL, "Layout")
        self.round()
        self.assertTrue([d for d in runtime.list_decisions(self.conn, "testproj") if d["item"] == "Layout"])
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"])

    # Verify an open owner question reaches the Queen and remains open.
    def test_game_question_is_handed_to_queen(self):
        """An open owner question appears in the Queen prompt without a triage agent."""
        qid = db.add_question(self.conn, "testproj", REL, "Layout", "Split or not?", "split")
        self.round()
        agents = runtime.list_agents(self.conn, "testproj")
        queen = next(a for a in agents if a["phase"] == "queen" and a["status"] == "running")
        prompt = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Split or not?", prompt)
        self.assertIn(qid, [q["id"] for q in runtime.list_questions(self.conn, "testproj", status="open")])
        self.assertFalse([a for a in agents if a["phase"] == "triage"])

class NoNextStepTest(RoundCase):
    """Legacy verifies without counts do not create automatic package locks."""

    def setUp(self):
        """K1 has three finished work/verify pairs; no verify answer carries an 'Offene Anforderungen' line."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file")

    # Verify missing legacy verify counts do not trigger an automatic lock.
    def test_missing_verify_count_does_not_lock(self):
        """Old verify results without counts do not lock the package or start another verify."""
        self.round()
        pkg = next(p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1")
        self.assertFalse(pkg["locked"])
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "verify" and a["package"] == "K1"]), 3)

class LiveRunnerSurvivesTest(RoundCase):
    """The round check never marks a running agent whose runner process lives, however long it has been running."""

    def test_running_for_six_minutes_survives_until_the_process_is_gone(self):
        """A runner alive for 6 minutes stays running; only after its process is gone the row is marked failed."""
        import subprocess
        from datetime import datetime, timedelta
        self.plan("IN_PROGRESS", ONE)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            started = datetime.now() - timedelta(minutes=6)
            runtime.upsert_agent(self.conn, "testproj", "DUMMY-T-triage", {"type": "claude", "provider": "claude", "plan": REL, "package": "P1", "phase": "triage",
                                                                         "status": "running", "startedAt": started.isoformat(), "pid": proc.pid})
            env = self.env()
            routine.repair_stale_agents(env, now=datetime.now() + timedelta(minutes=6))
            self.assertEqual(runtime.get_agent(self.conn, "testproj", "DUMMY-T-triage")["status"], "running")
        finally:
            proc.kill()
            proc.wait()
        routine.repair_stale_agents(env)
        row = runtime.get_agent(self.conn, "testproj", "DUMMY-T-triage")
        self.assertEqual(row["status"], "failed")
        self.assertIn("runner process vanished", row["last_line"])


class LockResolvedTest(RoundCase):
    """Decisions and answers are retained without recreating old package locks."""

    def setUp(self):
        """K1 has three finished work/verify pairs with open items; K2 depends on K1."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}, {"id": "K2", "title": "next task", "depends": ["K1"]}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file\nOffene Anforderungen: %d" % (3 - i))   # the count falls, so the progress guard does not stop the package

    def pkg(self, pid="K1"):
        """The package row of the dummy plan."""
        return [p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == pid][0]

    # Verify legacy failures do not lock the plan or start triage.
    def locked_round(self):
        """Start the Queen and verify the removed lock is not recreated."""
        self.round()
        self.assertFalse(self.pkg()["locked"])
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"])

    # Verify the Queen continues without a package lock.
    def assert_released(self):
        """Another round retains one Queen and no old package lock."""
        self.round()
        self.assertFalse(self.pkg()["locked"])
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "lock:%s|K1" % REL))
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)

    # Verify a decision from the given source remains recorded.
    def decision(self, source):
        """Retain a decision from the specified source."""
        self.locked_round()
        runtime.add_decision(self.conn, "testproj", source, "keep the layout", REL, "LOCK-K1")
        self.assert_released()
        self.assertTrue(runtime.list_decisions(self.conn, "testproj", item="LOCK-K1"))

    # Verify an answered question from the given source remains recorded.
    def answer(self, by):
        """Retain an answer from the specified source."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "LOCK-K1", "How to go on?", "retry")
        db.answer_question(self.conn, qid, "retry with the old layout", by)
        self.assert_released()
        self.assertIn(qid, [q["id"] for q in runtime.list_questions(self.conn, "testproj", status="answered")])
    def test_decision_owner(self):
        """Decision by the owner."""
        self.decision("owner")

    def test_decision_coordinator(self):
        """Decision by the coordinator."""
        self.decision("coordinator")

    def test_decision_auto(self):
        """Automatic decision."""
        self.decision("auto")

    def test_answer_owner(self):
        """Answer by the owner."""
        self.answer("owner")

    def test_answer_coordinator(self):
        """Answer by the coordinator."""
        self.answer("coordinator")

    def test_answer_auto(self):
        """Automatic answer."""
        self.answer("auto")

    # Verify the legacy lock question is answered without recreating the lock.
    def test_answer_item_with_title(self):
        """An answer to a legacy lock question is retained without recreating the lock."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "K1 Config values: range", "Paket K1 ist gesperrt, weil die Pruefung einen offenen Punkt meldet.", "retry")
        self.assertIsNone(routine.lock_resolution(self.env(), REL, "K1"))
        db.answer_question(self.conn, qid, "siehe dort", "coordinator")
        self.assert_released()

    # Verify an ordinary answered question remains answered without creating a lock.
    def test_answer_item_with_title_no_lock_question(self):
        """An answered item stays answered and does not create an old package lock."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "K1 Colour", "Which colour should K1 have?", "blue")
        db.answer_question(self.conn, qid, "blue", "owner")
        self.assert_released()
        self.assertIn(qid, [q["id"] for q in runtime.list_questions(self.conn, "testproj", status="answered")])

    # Verify an open owner question stays open without recreating a legacy lock.
    def test_open_question_stays_open_without_lock(self):
        """An open question stays open while no old lock is created."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "LOCK-K1", "How to go on?", "retry")
        self.round()
        self.assertFalse(self.pkg()["locked"])
        self.assertIn(qid, [q["id"] for q in runtime.list_questions(self.conn, "testproj", status="open")])

    # Verify decisions do not start old package agents.
    def test_closing_decision_does_not_start_package_agent(self):
        """A decision is retained without starting a separate package agent."""
        self.locked_round()
        before = [a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "K1"]
        runtime.add_decision(self.conn, "testproj", "coordinator", "K1 erledigt, keine weiteren K1-Work- oder Verify-Laeufe", REL, "LOCK-K1")
        self.round()
        self.assertEqual([a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "K1"], before)
        self.assertTrue(runtime.list_decisions(self.conn, "testproj", item="LOCK-K1"))
        self.assertFalse(self.pkg()["locked"])
    def test_done_answer_on_item_with_title(self):
        """An answered question with item 'K1 Config values' that says erledigt closes K1 (done_decisions), a lock question without that word does not."""
        env = self.env()
        qid = db.add_question(self.conn, "testproj", REL, "K1 Config values: range", "Paket K1 ist gesperrt, weil die Pruefung einen offenen Punkt meldet.", "x")
        db.answer_question(self.conn, qid, "siehe dort", "coordinator")
        self.assertEqual(routine.done_decisions(env, REL, ["K1", "K2"]), {})
        qid = db.add_question(self.conn, "testproj", REL, "K1 Config values: range", "Paket K1 ist gesperrt.", "x")
        db.answer_question(self.conn, qid, "erledigt, keine weiteren Laeufe", "coordinator")
        self.assertEqual(list(routine.done_decisions(env, REL, ["K1", "K2"])), ["K1"])

    def test_done_wording_needs_done(self):
        """A decision that only names the package does not close it, and 'not done' does not either."""
        env = self.env()
        runtime.add_decision(self.conn, "testproj", "owner", "K1 is not done yet", REL, None)
        runtime.add_decision(self.conn, "testproj", "owner", "K1 keeps its layout", REL, "LOCK-K1")
        self.assertEqual(routine.done_decisions(env, REL, ['K1', 'K2']), {})

class BrainTest(RoundCase):
    """The brain call with a stub program (no model)."""

    STREAM = "\n".join(json.dumps(x) for x in (
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.3, "resetsAt": int(time.time()) + 3600},
                                                                         "seven_day": {"utilization": 0.5, "resetsAt": int(time.time()) + 86400}}}},
        {"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30}}},
        {"type": "result", "result": 'Here: {"actions":[{"action":"note","text":"hi"}]}', "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0,
                                                                                                 "cache_read_input_tokens": 50, "output_tokens": 7},
         "modelUsage": {"claude-sonnet-5-5": {}}})) + "\n"

    def setUp(self):
        """A plan with a failed agent so the brain is asked."""
        super().setUp()
        self.plan("IN_PROGRESS", ONE)
        self.seed()
        self.agent("F-P1-work", package="P1", status="failed", started=3, ended=2, last_line="boom")
        self.argv = None

    def runner(self, argv, prompt, cwd, timeout):
        """Stub for the brain program."""
        self.argv, self.prompt = argv, prompt
        return 0, self.STREAM, "", False

    # Verify a failed legacy worker does not trigger a separate brain.
    def test_failed_worker_starts_queen_without_brain(self):
        """A failed legacy worker starts a Queen without invoking the brain runner."""
        self.round(brain_runner=self.runner)
        self.assertIsNone(self.argv)
        self.assertEqual(runtime.usage_list(self.conn, kind="brain"), [])
        queens = [a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(queens), 1)

    # Verify repeated rounds do not invoke a separate brain or duplicate a Queen.
    def test_repeated_rounds_do_not_call_brain(self):
        """A running Queen is retained across rounds without a brain call."""
        self.round(brain_runner=self.runner)
        self.round(brain_runner=self.runner)
        self.assertIsNone(self.argv)
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)
def argv_index(argv, name):
    """Index of an argument in a command line."""
    return argv.index(name)


class HeadlessStartTest(RoundCase):
    """A start through the headless terminal backend with a dummy runner program."""

    def setUp(self):
        """No NOSTART: the pane is really opened (headless), the runner is a tiny script that sets the pid of its agent row."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        self.plan("IN_PROGRESS", ONE)
        self.seed()
        self.dummy = self.base / "dummy_runner.py"
        self.dummy.write_text(
            "import os, sys, time\nsys.path.insert(0, %r)\nfrom haivequeen import config, db, runtime\n"
            "conn = db.connect(config.db_path())\nruntime.upsert_agent(conn, sys.argv[1], sys.argv[2], {'pid': os.getpid(), 'status': 'running'})\ntime.sleep(20)\n"
            % str(loop.TOOLS_DIR), encoding="utf-8")
        self.orig = loop.runner_argv
        loop.runner_argv = lambda env, spec: [sys.executable, str(self.dummy), env.name, spec["name"]]
        self.term = Headless(self.base / "panes")

    def tearDown(self):
        """End the dummy processes this test started."""
        loop.runner_argv = self.orig
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive:
                self.term.close_pane(p.id)
        super().tearDown()

    # Verify the Queen-based loop behavior.
    def test_start_through_terminal(self):
        """The Queen runs inside the sub-terminal of her Hive: the real hivepane process starts the runner from launch.json, the row gets the pane id of the
        Hive pane and the pid from the runner, and the pane title carries project and plan."""
        self.round(terminal=self.term, poll=0.2, tries=100)
        deadline = time.time() + 40
        row = None
        while row is None and time.time() < deadline:
            row = next((a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"), None)
            time.sleep(0.3)
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["pid"] and osplat.process_alive(row["pid"]))
        panes = self.term.list_panes("HaiveQueen")
        self.assertEqual(sorted(p.title for p in panes), ["HaiveQueen TEST | testproj | Apiary | job: testproj | model: -",
                                                          "HaiveQueen TEST | testproj | Hive | job: DUMMY-PLAN | model: -"])   # head pane of the Apiary and the Hive pane
        hive = next(p for p in panes if " | Hive | " in p.title)
        self.assertEqual(row["pane_id"], hive.id)

    def test_choose_slot(self):
        """The widest open pane is split side by side while both halves stay 40 columns wide, else the tallest top/bottom."""
        me = os.getpid()
        rows = [{"name": "A", "pane_id": "w.1", "status": "running", "pid": me, "paneCols": 140, "paneRows": 50},
                {"name": "B", "pane_id": "w.2", "status": "running", "pid": me, "paneCols": 60, "paneRows": 50},
                {"name": "C", "pane_id": "w.3", "status": "done", "pid": me, "paneCols": 200, "paneRows": 50}]
        s = loop.choose_slot(rows, "NEW")
        self.assertEqual((s["target"], s["direction"], s["cols"]), ("w.1", "horizontal", 70))
        rows[0]["paneCols"] = 70
        s = loop.choose_slot(rows, "NEW")
        self.assertEqual((s["target"], s["direction"], s["rows"]), ("w.1", "vertical", 25))
        self.assertIsNone(loop.choose_slot([], "NEW")["target"])
        self.assertIsNone(loop.choose_slot([dict(rows[0], pid=2000000000)], "NEW")["target"], "a dead runner is no target")


if __name__ == "__main__":
    unittest.main()
