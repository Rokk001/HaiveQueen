"""Port of test-s7.ps1: routine transitions run without a brain call, each trigger causes exactly one call, a quiet hour causes zero calls,
the context limit writes HANDOFF and a -c2 agent continues, finished packages are never startable, decided questions are not asked again."""
import unittest

from bzztest import REL, RoundCase, runtime

P12 = [{"id": "P1", "title": "first"}, {"id": "P2", "title": "second", "depends": ["P1"]}]


class S7Test(RoundCase):
    """Rounds with a fake brain answer; no pane is opened."""

    def start(self, name, pkg="P1"):
        """A fake brain answer that starts a work agent."""
        return self.brain_answer({"action": "start_agent", "name": name, "type": "claude", "phase": "work", "plan": REL, "package": pkg,
                                  "cwd": str(self.wt), "prompt": "Do package %s." % pkg})

    def plan_status(self):
        """Status of the dummy plan in the database after reading the plan file."""
        runtime.sync_plans(self.conn, "testproj", str(self.repo))
        return next(p for p in runtime.list_plans(self.conn, "testproj") if p["path"] == REL)["status"]

    # Verify a READY plan becomes active with one Queen and no brain call.
    def test_new_plan_starts_one_queen(self):
        """A READY plan starts one Queen and stays stable across repeated rounds."""
        self.plan("READY", P12)
        self.round(self.start("DUMMY-P1-work"))
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.plan_status(), "IN_PROGRESS")
        for _ in range(12):
            self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj") if a["status"] == "running"]
        self.assertEqual([a["phase"] for a in active], ["queen"])
        self.assertEqual(self.calls(), 0)
    def triggers_setup(self):
        """A plan with a finished work and a finished verify that reported one open item."""
        self.plan("IN_PROGRESS", P12)
        self.agent("T-P1-work", package="P1", started=20, ended=15)
        self.agent("T-P1-verify", type="codex", provider="codex", package="P1", phase="verify", started=10, ended=5)
        self.finish("T-P1-verify", "- Item A: teilweise\nOffene Anforderungen: 1")

    # Verify legacy triggers do not call the removed brain.
    def test_legacy_triggers_do_not_call_brain(self):
        """Legacy verify, failure, blocked integration and owner answer cause no brain call."""
        from haivequeen import db
        self.triggers_setup()
        self.round()
        self.agent("T-P2-work", package="P2", status="failed", started=3, ended=2, last_line="test failure")
        self.round()
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "test conflict")
        self.round()
        qid = db.add_question(self.conn, "testproj", REL, "X", "Which one?", "A")
        db.answer_question(self.conn, qid, "Take A.", "owner")
        self.round()
        self.assertEqual(self.calls(), 0)
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running"]), 1)

    # Verify a legacy lock does not invoke the removed triage or brain.
    def test_legacy_lock_does_not_start_triage_or_brain(self):
        """An old package lock leaves the running Queen in charge."""
        self.triggers_setup()
        self.round()
        runtime.kv_set(self.conn, "testproj", "lock:%s|P1" % REL, "Should the dummy do X or Y?")
        self.round()
        self.assertEqual(self.calls(), 0)
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"])
    # Verify a running Queen hands off and resumes on the same provider.
    def test_context_limit_handoff(self):
        """At 310k the Queen receives HANDOFF, stays running and is replaced on the same provider."""
        self.plan("IN_PROGRESS", P12)
        self.round()
        first = next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"ctxTokens": 310000})
        d = self.env().run_dir(first["name"])
        before = self.calls()
        self.round()
        self.assertTrue((d / "HANDOFF").exists())
        self.assertEqual(self.calls(), before)
        self.assertEqual(self.ag(first["name"])["status"], "running")
        self.finish(first["name"], final="Handoff")
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"pid": None})
        self.round()
        second = next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")
        self.assertNotEqual(second["name"], first["name"])
        self.assertEqual(second["provider"], first["provider"])
        self.assertEqual(second["plan_path"], first["plan_path"])
        prompt = (self.env().run_dir(second["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("handoff", prompt.lower())
    def reset_rt(self):
        """Fresh state: plan P1, P2 plus an old row that gives the plan its worktree."""
        self.conn.execute("DELETE FROM agents")
        self.conn.execute("DELETE FROM queue")
        self.conn.execute("DELETE FROM kv")
        self.conn.execute("DELETE FROM packages")
        self.conn.execute("DELETE FROM decisions")
        self.conn.execute("DELETE FROM questions")
        self.conn.execute("DELETE FROM handled")
        self.plan("IN_PROGRESS", P12)
        self.agent("Z-ZZ-work", package="ZZ", started=60, ended=50)

    # Verify the Queen receives an existing completed package and its dependency.
    def test_completed_packages_remain_in_plan(self):
        """A completed P1 and its dependent P2 remain available to the Queen."""
        self.reset_rt()
        runtime.set_package(self.conn, "testproj", REL, "P1", phase="done")
        self.round()
        rows = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in rows if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertFalse([a for a in rows if a["phase"] in ("work", "verify") and a["status"] == "running"])
        self.assertEqual(next(p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "P1")["phase"], "done")
        packages = {p["id"]: p for p in self.env().packages(REL)}
        self.assertEqual(packages["P2"]["depends"], ["P1"])
    # Verify known answers are reused when the Queen asks questions.
    def test_decided_questions_not_asked(self):
        """Known items remain closed and a new item reaches the owner."""
        import argparse
        import contextlib
        import io
        from haivequeen import cli, db
        self.reset_rt()
        runtime.add_decision(self.conn, "testproj", "owner", "M7: yes.", REL, "M7")
        v9 = db.add_question(self.conn, "testproj", REL, "V9", "Decay?", "A")
        db.answer_question(self.conn, v9, "Yes.", "owner")
        for item in ("M7", "V9", "M8 new item"):
            args = argparse.Namespace(project="testproj", plan=REL, item=item, question="q?", recommendation="r")
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_ask(args, self.conn)
        open_items = [q["item"] for q in runtime.list_questions(self.conn, "testproj", status="open")]
        self.assertNotIn("M7", open_items)
        self.assertNotIn("V9", open_items)
        self.assertEqual(open_items.count("M8 new item"), 1)
    # Verify the Queen resumes in the existing plan worktree.
    def test_answered_question_uses_worktree_of_its_package(self):
        """The Queen resumes in the existing plan worktree instead of the newer workspace."""
        from haivequeen import db
        self.reset_rt()
        self.plan("IN_PROGRESS", [{"id": "D3", "title": "item with worktree"}])
        pkg_wt = self.base / "wt-d3"
        pkg_wt.mkdir()
        self.agent("Z-D3-work", package="D3", cwd=str(pkg_wt), started=60, ended=50)
        self.agent("Z-OTHER-work", package="OTHER", cwd=str(self.repo), started=40, ended=30)
        q = db.add_question(self.conn, "testproj", REL, "D3", "Which one?", "A")
        db.answer_question(self.conn, q, "yes, do it", "owner")
        self.round()
        self.assertEqual(next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")["cwd"], str(pkg_wt))

    # Verify the Queen resumes in the existing plan worktree.
    def test_fix_and_plan_start_use_worktree_not_workspace(self):
        """The Queen resumes the blocked plan in its existing worktree instead of the newer workspace."""
        self.reset_rt()
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        old_wt = self.base / "wt-old"
        old_wt.mkdir()
        self.agent("Z-ZZ-work", package="ZZ", cwd=str(old_wt), started=60, ended=50)
        self.agent("Z-YY-work", package="YY", cwd=str(self.repo), started=40, ended=30)
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "2 checks red: x")
        self.round()
        self.assertEqual(next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")["cwd"], str(old_wt))

    def test_lock_text_with_decided_items_no_call(self):
        """An open-for-owner point naming only decided items causes no brain call."""
        self.machine["triage"] = False
        self.reset_rt()
        runtime.add_decision(self.conn, "testproj", "owner", "Dummy room: crystal bowl (M7): yes.", REL, "M7")
        runtime.kv_set(self.conn, "testproj", "lock:%s|P1" % REL, "M7 crystal bowl: where does it stand?")
        b = self.calls()
        self.round(self.brain_answer({"action": "note", "text": "x"}))
        self.assertEqual(self.calls(), b)

    # Verify existing decisions and answers stay available without legacy agents.
    def test_answers_and_blocked_queue_do_not_start_legacy_agents(self):
        """The Queen receives existing answers while blocked work creates no fix or question-run agent."""
        from haivequeen import db
        self.reset_rt()
        runtime.nofollow_add(self.conn, "testproj", "dummy-plan.md|p1")
        runtime.set_package(self.conn, "testproj", REL, "P1", phase="done")
        for item, answer in (("D1", "already answered"), ("D2", "yes"), ("D3", "yes, do it")):
            qid = db.add_question(self.conn, "testproj", REL, item, "Which one?", "A")
            db.answer_question(self.conn, qid, answer, "owner")
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "2 checks red: x")
        self.round()
        agents = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in agents if a["phase"] == "queen" and a["status"] == "running"]), 1)
        self.assertFalse([a for a in agents if a["status"] == "running" and (a["package"].startswith("FIX-") or a["package"].startswith("Q-"))])
        self.assertEqual(len(runtime.list_questions(self.conn, "testproj", status="answered")), 3)
        self.assertEqual(self.calls(), 0)
    # Verify a plan without an existing worktree receives worktree instructions.
    def test_plan_without_worktree_starts_queen(self):
        """The Queen starts from the checkout and receives the worktree creation instruction."""
        self.plan("IN_PROGRESS", P12)
        self.round()
        row = next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")
        self.assertEqual(row["cwd"], str(self.repo))
        prompt = (self.env().run_dir(row["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("create the plan worktree and branch", prompt)
        self.assertEqual(self.calls(), 0)
    def test_v1_fix_last_count_line_decides(self):
        """Fix from v1: the LAST 'Offene Anforderungen' line decides, text after it does not matter."""
        from haivequeen import routine
        env = self.env()
        self.finish("A", "Offene Anforderungen: 3\nnoch eine Zeile\nOffene Anforderungen: 0\nNachwort")
        self.finish("B", "Offene Anforderungen: 0\nOffene Anforderungen: 2")
        self.assertTrue(routine.verify_zero(env, "A"))
        self.assertFalse(routine.verify_zero(env, "B"))

    def test_verify_partial_in_english_is_open(self):
        """A partly done item written as 'partial' or 'partially' (any case) keeps the package open even with 'Offene Anforderungen: 0'; 'impartial' is no match."""
        from haivequeen import routine
        env = self.env()
        forms = {"E1": "- Item A: partial", "E2": "* Item B: Partially done", "E3": "| Item C | PARTIAL |", "E4": "  - item d: partially"}
        for name, line in forms.items():
            self.finish(name, "%s\nOffene Anforderungen: 0" % line)
            self.assertFalse(routine.verify_zero(env, name), line)
        self.finish("E5", "- The review was impartial\nOffene Anforderungen: 0")
        self.assertTrue(routine.verify_zero(env, "E5"))

    def test_verify_partial_in_box_table_row_is_open(self):
        """A partly done item in a table drawn with box-drawing frame characters (also other than the plain pipe) keeps the package open."""
        from haivequeen import routine
        env = self.env()
        frames = {"F1": "│", "F2": "┃", "F3": "║", "F4": "╎"}
        for name, bar in frames.items():
            self.finish(name, "%s Item A %s teilweise %s\nOffene Anforderungen: 0" % (bar, bar, bar))
            self.assertFalse(routine.verify_zero(env, name), repr(bar))
        self.finish("F5", "│ Item A │ Partial │\nOffene Anforderungen: 0")
        self.assertFalse(routine.verify_zero(env, "F5"))
        self.finish("F6", "│ Item A │ done │\nOffene Anforderungen: 0")
        self.assertTrue(routine.verify_zero(env, "F6"))

    # Verify missing legacy verify counts do not start another verify agent.
    def test_verify_without_count_goes_to_queen(self):
        """Two old verify reports without counts are retained while one Queen starts."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.agent("W-P1-work", package="P1", started=40, ended=35)
        for i in (1, 2):
            self.agent("V%d-P1-verify" % i, type="codex", provider="codex", package="P1", phase="verify", started=30 - 10 * i, ended=25 - 10 * i)
            self.finish("V%d-P1-verify" % i, "no count here")
        self.round()
        agents = runtime.list_agents(self.conn, "testproj")
        self.assertEqual(len([a for a in agents if a["phase"] == "verify"]), 2)
        self.assertEqual(len([a for a in agents if a["phase"] == "queen" and a["status"] == "running"]), 1)

if __name__ == "__main__":
    unittest.main()
