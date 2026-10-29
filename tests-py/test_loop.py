"""Tests of the multi-project loop (D1, D4), the pause and drain gate and re-attach (D8), the triage start (D5), the brain, the headless terminal start and the loop itself."""
import json
import os
import sys
import time
import unittest

from bzztest import REL, RoundCase, minutes_ago, provider, runtime
from haivequeen import brain, db, export, loop, routine, runstate, triage
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

    def test_priority_first(self):
        """The shared slot goes to the project with the higher priority; the other waits."""
        res = self.round()
        self.assertEqual(self.ag("DUMMY-P1-work", "alpha")["status"], "running")
        self.assertIsNone(self.ag("DUMMY-P1-work", "beta"))
        self.assertEqual(res["projects"]["alpha"]["started"], 1)
        self.assertEqual(res["projects"]["beta"]["started"], 0)

    def test_tie_by_oldest_waiting(self):
        """At equal priority the project with the older waiting package goes first."""
        self.conn.execute("UPDATE projects SET priority=0")
        self.add_project("alpha", 0)
        runtime.kv_set(self.conn, "beta", "wait:%s|P1" % REL, minutes_ago(90))
        runtime.kv_set(self.conn, "alpha", "wait:%s|P1" % REL, minutes_ago(10))
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-work", "beta")["status"], "running")
        self.assertIsNone(self.ag("DUMMY-P1-work", "alpha"))
        self.assertEqual(runtime.kv_get(self.conn, "beta", "wait:%s|P1" % REL), None, "the marker of a started package is cleared")

    def test_same_names_in_two_projects(self):
        """Two slots: both projects start their package with the same agent name, rows stay separate."""
        self.machine["providers"] = [provider("claude", 2)]
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-work", "alpha")["status"], "running")
        self.assertEqual(self.ag("DUMMY-P1-work", "beta")["status"], "running")
        self.assertEqual(len(runtime.list_agents(self.conn, None, "active")), 4)
        # the slots count across projects: a third package of either project finds no free slot
        self.assertEqual(sch.provider_free(self.env("alpha").providers, [a for a in runtime.list_agents(self.conn, None, "active") if a["status"] == "running"])["claude"], 0)

    def test_project_data_in_events_and_logs(self):
        """Every project writes its own log folder (D4: the project is visible everywhere)."""
        self.round()
        self.assertTrue((runtime.mirror_dir("alpha") / "ROUNDS.log").is_file())
        self.assertNotEqual(runtime.mirror_dir("alpha"), runtime.mirror_dir("beta"))
        self.assertIn("start DUMMY-P1-work", self.log("alpha"))

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

    def test_pause_blocks_starts(self):
        """While paused the round starts nothing and says why; resume lifts it."""
        runstate.pause(self.conn)
        res = self.round()
        self.assertEqual(res["skipped"], "PAUSED")
        self.assertIsNone(self.ag("DUMMY-P1-work"))
        self.assertIn("round skipped (PAUSED)", self.log())
        runstate.resume(self.conn)
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-work")["status"], "running")

    def test_pause_keeps_running_agents(self):
        """A running agent is not touched by a pause; a drain reports drained once it has ended."""
        self.round()
        runstate.drain(self.conn)
        self.assertEqual(runstate.get_state(self.conn)["state"], "DRAINING")
        self.assertEqual(self.round()["skipped"], "DRAINING")
        self.assertEqual(self.ag("DUMMY-P1-work")["status"], "running")
        self.finish("DUMMY-P1-work")
        self.assertEqual(runstate.settle(self.conn)["state"], "DRAINED")
        self.assertEqual(len([e for e in runtime.list_events(self.conn, "*") if e["kind"] == "drained"]), 1)

    def test_stop_flag_and_low_ram(self):
        """The STOP flag and low free RAM skip the round, too."""
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text("x")
        self.assertEqual(self.round()["skipped"], "STOP")
        loop.stop_flag().unlink()
        res = self.round(free_ram=lambda: 0.5)
        self.assertIn("free RAM", res["skipped"])
        self.assertIsNone(self.ag("DUMMY-P1-work"))


class LoopTest(RoundCase):
    """The loop: re-attach at the start, one lock, STOP, round errors."""

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

        kw = {"free_ram": lambda: 16.0, "settle": 0, "brain_runner": lambda *a: (1, "", "", False)}
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
        hl = loop.head_lines(80, brain.totals(self.conn), {"busy": True, "model": "Son 5.5 med.", "projects": ["a", "b"]})
        self.assertIn("QUEEN", hl[0][0])
        self.assertIn("round running", hl[0][0])
        self.assertIn("Projects: a, b", hl[1][0])
        hl = loop.head_lines(80, brain.totals(self.conn), {"run_state": "PAUSED", "model": "x"})
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
        """24 unique agent names, none is Queen, and the pane of the main agent is titled QUEEN."""
        self.assertEqual(len(loop.AGENT_NAMES), 24)
        self.assertEqual(len(set(loop.AGENT_NAMES)), 24)
        self.assertNotIn("Queen", loop.AGENT_NAMES)
        self.assertEqual(loop.AGENT_NAMES[:3], ["Ada", "Bea", "Cleo"])
        self.assertEqual(loop.AGENT_NAMES[-1], "Zoe")
        self.assertIn("QUEEN", loop.queen_pane_title("m", True))


class TriageStartTest(RoundCase):
    """D5: a lock starts a triage agent; its decisions unlock the package, a game question keeps it locked until the answer."""

    def setUp(self):
        """A package with three finished work/verify pairs, each verify with open items."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file\nOffene Anforderungen: 1")

    def test_lock_starts_triage_agent(self):
        """The lock is recorded and a triage agent (class medium, phase triage) starts with the open points in its prompt."""
        self.round()
        pkg = [p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]
        self.assertTrue(pkg["locked"])
        tri = [a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"]
        self.assertEqual(len(tri), 1)
        self.assertEqual((tri[0]["package"], tri[0]["class"], tri[0]["status"]), ("K1", "medium", "running"))
        prompt = (self.env().run_dir(tri[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Layout: split the file", prompt)
        self.assertIn("triage decide", prompt)
        self.assertIn("cli.py", prompt)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"]), 1, "no second triage agent while one runs")

    def test_decision_unlocks_and_work_resumes(self):
        """A technical decision unlocks the package, the failures are forgiven and the package gets a work agent again."""
        self.round()
        tri = [a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"][0]
        triage.decide(self.conn, "testproj", REL, "K1", "Layout", "split the file", "plan rule 3", "docs/plans")
        self.finish(tri["name"])
        self.round()
        self.assertFalse([p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]["locked"])
        self.assertEqual(runtime.kv_get(self.conn, "testproj", "lockrel:%s|K1" % REL), "3")
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "lock:%s|K1" % REL))
        self.assertTrue([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "work" and a["status"] == "running" and a["package"] == "K1"])

    def test_game_question_keeps_lock_until_answer(self):
        """A game point goes to the owner; the package stays locked until the question is answered, then the routine unlocks it."""
        self.round()
        tri = [a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"][0]
        res = triage.escalate(self.conn, "testproj", REL, "K1", "Layout", "Split or not?", "split")
        self.finish(tri["name"])
        self.round()
        self.assertTrue([p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]["locked"])
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage" and a["status"] == "running"], "points with an open question need no triage")
        db.answer_question(self.conn, res["question_id"], "split", "owner")
        self.round()
        self.assertFalse([p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]["locked"])


class NoNextStepTest(RoundCase):
    """A package whose verifies gave no count line is locked once the verify repeats are used up, instead of lying still."""

    def setUp(self):
        """K1 has three finished work/verify pairs; no verify answer carries an 'Offene Anforderungen' line."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file")

    def test_package_without_next_step_is_locked(self):
        """The failures stay 0 (no count line), yet the round locks the package and starts no fourth verify."""
        self.round()
        pkg = [p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]
        self.assertTrue(pkg["locked"])
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
    """A decision or answer for item LOCK-<package> (owner, coordinator or auto) lifts the lock and forgives the failures; a closing decision ends the package."""

    def setUp(self):
        """K1 has three finished work/verify pairs with open items; K2 depends on K1."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}, {"id": "K2", "title": "next task", "depends": ["K1"]}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file\nOffene Anforderungen: 1")

    def pkg(self, pid="K1"):
        """The package row of the dummy plan."""
        return [p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == pid][0]

    def locked_round(self):
        """Run the round that locks K1 and finish its triage agent."""
        self.round()
        self.assertTrue(self.pkg()["locked"])
        for a in runtime.list_agents(self.conn, "testproj"):
            if a["phase"] == "triage":
                self.finish(a["name"])

    def assert_released(self):
        """After the next round the lock is gone, three failures are forgiven and K1 has a work agent again."""
        self.round()
        self.assertFalse(self.pkg()["locked"])
        self.assertEqual(runtime.kv_get(self.conn, "testproj", "lockrel:%s|K1" % REL), "3")
        self.assertIsNone(runtime.kv_get(self.conn, "testproj", "lock:%s|K1" % REL))
        self.assertTrue([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "work" and a["status"] == "running" and a["package"] == "K1"])

    def decision(self, source):
        """Release by a decision of this source."""
        self.locked_round()
        runtime.add_decision(self.conn, "testproj", source, "keep the layout", REL, "LOCK-K1")
        self.assert_released()

    def answer(self, by):
        """Release by a question with item LOCK-K1 answered by this source."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "LOCK-K1", "How to go on?", "retry")
        db.answer_question(self.conn, qid, "retry with the old layout", by)
        self.assert_released()

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

    def test_answer_item_with_title(self):
        """A question with item 'K1 Config values' whose text says the package is gesperrt lifts the lock once answered (not before)."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "K1 Config values: range", "Paket K1 ist gesperrt, weil die Pruefung einen offenen Punkt meldet.", "retry")
        self.assertIsNone(routine.lock_resolution(self.env(), REL, "K1"))
        db.answer_question(self.conn, qid, "siehe dort", "coordinator")
        self.assert_released()

    def test_answer_item_with_title_no_lock_question(self):
        """An answered question about K1 that is no lock question does not lift the lock."""
        self.locked_round()
        qid = db.add_question(self.conn, "testproj", REL, "K1 Colour", "Which colour should K1 have?", "blue")
        db.answer_question(self.conn, qid, "blue", "owner")
        self.round()
        self.assertTrue(self.pkg()["locked"])

    def test_open_question_keeps_lock(self):
        """An unanswered question for LOCK-K1 does not lift the lock."""
        self.locked_round()
        db.add_question(self.conn, "testproj", REL, "LOCK-K1", "How to go on?", "retry")
        self.round()
        self.assertTrue(self.pkg()["locked"])

    def test_closing_decision_marks_package_done(self):
        """'K1 done, no further runs' ends K1: no work or verify run for it, the dependent package K2 starts."""
        self.locked_round()
        before = [a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "K1"]
        runtime.add_decision(self.conn, "testproj", "coordinator", "K1 erledigt, keine weiteren K1-Work- oder Verify-Laeufe", REL, "LOCK-K1")
        self.round()
        self.assertEqual([a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "K1"], before)
        self.assertTrue([a for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "K2" and a["phase"] == "work"])
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

    def test_brain_call_and_usage(self):
        """Tokens, context, model and the answer come from the stream; the call is stored; Claude limits are remembered."""
        res = self.round(brain_runner=self.runner)
        self.assertIn("# EVENTS (JSON)", self.prompt)
        self.assertIn("agent_failed", self.prompt)
        self.assertEqual(self.argv[argv_index(self.argv, "--tools") + 1], "", "no tools without a new plan (rule 12)")
        row = runtime.usage_list(self.conn, kind="brain")[-1]
        self.assertEqual((row["tokens_in"], row["tokens_out"], row["context"], row["model"]), (150, 7, 60, "claude-sonnet-5-5"))   # stored as the model key, not the label
        self.assertEqual((row["trigger"], row["error"], row["actions"]), ("agent_failed", 0, "no action: hi"))
        self.assertEqual(sch.claude_usage(self.conn)["SevenDay"]["Pct"], 50)
        self.assertEqual(brain.totals(self.conn)["Input"], 150)
        self.assertTrue(res["projects"]["testproj"]["actions"] >= 1)
        self.assertTrue(runtime.handled_get(self.conn, "testproj", "failed:F-P1-work"))

    def test_brain_failure_counts_tries(self):
        """A failed call is stored as an error, the event is asked again once, then it is dropped."""
        bad = lambda argv, prompt, cwd, timeout: (1, "", "exit boom", False)  # noqa: E731
        self.round(brain_runner=bad)
        self.assertEqual(self.calls(), 1)
        self.assertEqual(runtime.usage_list(self.conn, kind="brain")[-1]["error"], 1)
        self.assertFalse(runtime.handled_get(self.conn, "testproj", "failed:F-P1-work"))
        self.round(brain_runner=bad)
        self.assertTrue(runtime.handled_get(self.conn, "testproj", "failed:F-P1-work"))
        self.assertIn("brain failed", self.log())

    def test_answer_parsing(self):
        """JSON cut out of text; broken or action-less answers are errors."""
        self.assertEqual(brain.parse_answer('x {"actions": []} y')[0], {"actions": []})
        self.assertIn("no JSON", brain.parse_answer("nothing")[1])
        self.assertIn("invalid", brain.parse_answer("{oops}")[1])
        self.assertIn("actions", brain.parse_answer('{"a":1}')[1])


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

    def test_start_through_terminal(self):
        """The agent row gets pane id and pid from the runner, the pane title carries project and agent name, the colour is the project colour."""
        self.round(terminal=self.term, poll=0.2, tries=100)
        row = self.ag("DUMMY-P1-work")
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["pid"] and osplat.process_alive(row["pid"]))
        panes = self.term.list_panes("HaiveQueen")
        self.assertEqual(len(panes), 1)
        self.assertIn(row["pane_id"], [p.id for p in panes])
        self.assertRegex(panes[0].title, r"^HaiveQueen TEST \| testproj \| Agent [A-Z]+ \| job: DUMMY-P1-work \| model: Claude Sonnet 5\.5 medium$")

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
