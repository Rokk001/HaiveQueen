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

    def test_new_plan_and_routine_chain(self):
        """A new READY plan costs exactly one brain call; the rest of the chain needs none."""
        self.plan("READY", P12)
        self.round(self.start("DUMMY-P1-work"))
        self.assertEqual(self.calls(), 1)
        self.assertEqual(self.ag("DUMMY-P1-work")["status"], "running")
        self.assertEqual(self.plan_status(), "IN_PROGRESS")
        self.round(self.start("DUMMY-P1-work"))
        self.assertEqual(self.calls(), 1, "a handled event is not asked twice")
        self.finish("DUMMY-P1-work")
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-verify")["status"], "running")
        self.assertEqual(self.ag("DUMMY-P1-verify")["provider"], "codex", "verify takes the other provider")
        self.finish("DUMMY-P1-verify", "Alles geprueft.\nOffene Anforderungen: 0")
        self.round()
        self.assertEqual(self.ag("DUMMY-P2-work")["status"], "running")
        self.finish("DUMMY-P2-work")
        self.round()
        self.assertEqual(self.ag("DUMMY-P2-verify")["status"], "running")
        self.finish("DUMMY-P2-verify", "Offene Anforderungen: 0")
        self.round()
        self.assertEqual(self.ag("DUMMY-ALL-verify")["status"], "running")
        self.finish("DUMMY-ALL-verify", "Offene Anforderungen: 0")
        self.round()
        self.assertEqual(self.ag("DUMMY-release")["status"], "running")
        self.finish("DUMMY-release")
        self.round()
        self.assertEqual(self.plan_status(), "DONE")
        self.assertEqual(self.calls(), 1, "the whole routine chain used no further brain call")
        before = self.calls()
        for _ in range(12):
            self.round()
        self.assertEqual(self.calls(), before, "quiet hour: zero brain calls")

    def triggers_setup(self):
        """A plan with a finished work and a finished verify that reported one open item."""
        self.plan("IN_PROGRESS", P12)
        self.agent("T-P1-work", package="P1", started=20, ended=15)
        self.agent("T-P1-verify", type="codex", provider="codex", package="P1", phase="verify", started=10, ended=5)
        self.finish("T-P1-verify", "- Item A: teilweise\nOffene Anforderungen: 1")

    def test_each_trigger_one_call(self):
        """verify_open and agent_failed cost one call each; blocked rows and answered questions are handled by the routine."""
        note = self.brain_answer({"action": "note", "text": "nothing to start"})
        self.triggers_setup()
        b = self.calls()
        self.round(note)
        self.assertEqual(self.calls(), b + 1)
        self.round(note)
        self.assertEqual(self.calls(), b + 1, "verify_open handled: no second call")
        self.agent("T-P2-work", package="P2", status="failed", started=3, ended=2, last_line="test failure")
        b = self.calls()
        self.round(note)
        self.assertEqual(self.calls(), b + 1, "agent_failed: exactly one call")
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "test conflict")
        b = self.calls()
        self.round(note)
        self.assertEqual(self.calls(), b, "integration_blocked: handled by the routine (fix agent)")
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", status="integrated")
        from haivequeen import db
        qid = db.add_question(self.conn, "testproj", REL, "X", "Which one?", "A")
        db.answer_question(self.conn, qid, "Take A.", "owner")
        b = self.calls()
        self.round(note)
        self.assertEqual(self.calls(), b, "question_answered: handled by the routine (work agent)")

    def test_open_for_owner_without_triage(self):
        """With triage off the lock text goes to the brain once (open_for_owner)."""
        self.settings = None
        self.machine["triage"] = False
        self.triggers_setup()
        note = self.brain_answer({"action": "note", "text": "x"})
        self.round(note)   # verify_open
        b = self.calls()
        runtime.kv_set(self.conn, "testproj", "lock:%s|P1" % REL, "Should the dummy do X or Y?")
        self.round(note)
        self.assertEqual(self.calls(), b + 1)

    def test_context_limit_handoff(self):
        """310k context: HANDOFF flag, no brain call, agent not killed, then a -c2 agent continues with the same task."""
        self.plan("IN_PROGRESS", P12)
        self.agent("C-P1-work", package="P1", status="running", pid=__import__("os").getpid(), started=30, ctxTokens=310000)
        env = self.env()
        d = env.run_dir("C-P1-work")
        d.mkdir(parents=True)
        (d / "prompt.md").write_text("Header\n\nTASK\n\nDo package P1 of the dummy plan.\n\nPrüfe nach jedem abgeschlossenen Arbeitsschritt, ob die Datei X existiert.", encoding="utf-8")
        b = self.calls()
        self.round()
        self.assertTrue((d / "HANDOFF").exists())
        self.assertEqual(self.calls(), b)
        self.assertEqual(self.ag("C-P1-work")["status"], "running")
        self.finish("C-P1-work")
        self.round()
        c2 = self.ag("C-P1-work-c2")
        self.assertTrue(c2 and c2["status"] == "running")
        text = (env.run_dir("C-P1-work-c2") / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("Do package P1", text)
        self.assertIn("Handoff C-P1-work", text)
        self.assertIn("HANDOFF", text)

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

    def test_finished_packages_not_startable(self):
        """Packages done in the table or the queue are never started."""
        self.reset_rt()
        for i in ("P1", "P2"):
            runtime.set_package(self.conn, "testproj", REL, i, phase="done")
        b = self.calls()
        self.round()
        self.assertFalse([n for n in self.names() if n.startswith(("DUMMY-P1", "DUMMY-P2"))])
        self.assertEqual(self.calls(), b)
        self.reset_rt()
        runtime.set_package(self.conn, "testproj", REL, "P1", phase="done")
        self.round()
        self.assertIsNone(self.ag("DUMMY-P1-work"))
        self.assertEqual(self.ag("DUMMY-P2-work")["status"], "running", "dependent P2 is startable")
        self.reset_rt()
        runtime.set_package(self.conn, "testproj", REL, "P1", phase="work")
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-work")["status"], "running")
        self.reset_rt()
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "integrated", "Lauf 1: Paket ALL (Aggregat-Branch)")
        self.round()
        self.assertIsNone(self.ag("DUMMY-P1-work"))
        self.assertIsNone(self.ag("DUMMY-P2-work"))
        self.reset_rt()
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "2 checks red: Paket ALL")
        self.round()
        self.assertEqual(self.ag("DUMMY-P1-work")["status"], "running", "a blocked row does not count as done")

    def test_decided_questions_not_asked(self):
        """ask_owner for an item with a decision or a known question is skipped; a new item is written."""
        from haivequeen import db
        self.reset_rt()
        runtime.add_decision(self.conn, "testproj", "owner", "Dummy room: crystal bowl (M7): yes.", REL, "M7")
        v9 = db.add_question(self.conn, "testproj", REL, "V9 Mood decay in the bath", "Decay?", "A")
        db.answer_question(self.conn, v9, "Yes.", "owner")
        ask = lambda item: {"action": "ask_owner", "plan": REL, "item": item, "question": "q?", "recommendation": "r"}  # noqa: E731
        self.agent("Z-ZZ-verify", type="codex", provider="codex", package="P1", phase="verify", status="failed", started=3, ended=2, last_line="x")
        self.round(self.brain_answer(ask("M7"), ask("V9"), ask("M8 new item")))
        qs = runtime.list_questions(self.conn, "testproj")
        self.assertFalse([q for q in qs if q["item"].startswith("M7")])
        self.assertEqual([q["question"] for q in qs if q["item"].startswith("V9")], ["Decay?"])
        self.assertEqual(len([q for q in qs if q["item"] == "M8 new item"]), 1)

    def test_answered_question_uses_worktree_of_its_package(self):
        """A Q-package works in the existing worktree of the package the question belongs to, not in the newer workspace folder."""
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
        self.assertEqual(self.ag("DUMMY-Q-D3-work")["cwd"], str(pkg_wt))

    def test_lock_text_with_decided_items_no_call(self):
        """An open-for-owner point naming only decided items causes no brain call."""
        self.machine["triage"] = False
        self.reset_rt()
        runtime.add_decision(self.conn, "testproj", "owner", "Dummy room: crystal bowl (M7): yes.", REL, "M7")
        runtime.kv_set(self.conn, "testproj", "lock:%s|P1" % REL, "M7 crystal bowl: where does it stand?")
        b = self.calls()
        self.round(self.brain_answer({"action": "note", "text": "x"}))
        self.assertEqual(self.calls(), b)

    def test_mechanical_rules(self):
        """Freeze list, duplicate and implemented answers, fixes first, verify_open on finished packages, rule 5 (graphics go to codex)."""
        from haivequeen import db
        self.reset_rt()
        runtime.nofollow_add(self.conn, "testproj", "dummy-plan.md|p1")
        self.round()
        self.assertIsNone(self.ag("DUMMY-P1-work"), "frozen package is not started")
        runtime.nofollow_remove(self.conn, "testproj", "dummy-plan.md|p1")
        self.conn.execute("DELETE FROM agents")
        self.conn.execute("DELETE FROM kv")
        self.plan("IN_PROGRESS", [{"id": "D2", "title": "implemented item"}])
        self.agent("Z-ZZ-work", package="ZZ", started=60, ended=50)
        runtime.set_package(self.conn, "testproj", REL, "D2", phase="done")
        for item, ans in (("D1", "already answered (ANSWERS.md). Duplicate question."), ("D2", "yes"), ("D3", "yes, do it")):
            q = db.add_question(self.conn, "testproj", REL, item, "Which one?", "A")
            db.answer_question(self.conn, q, ans, "owner")
        b = self.calls()
        self.round()
        self.assertIsNone(self.ag("DUMMY-Q-D1-work"), "duplicate answer: no work agent")
        self.assertIsNone(self.ag("DUMMY-Q-D2-work"), "item already implemented: no work agent")
        self.assertEqual(self.ag("DUMMY-Q-D3-work")["status"], "running")
        self.assertEqual(self.calls(), b)
        self.reset_rt()
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "blocked", "2 checks red: x")
        b = self.calls()
        self.round()
        self.assertEqual(self.ag("DUMMY-FIX-abc123d-work")["status"], "running")
        self.assertEqual(self.calls(), b)
        self.reset_rt()
        runtime.set_package(self.conn, "testproj", REL, "P1", phase="done")
        self.agent("V-P1-work", package="P1", started=20, ended=15)
        self.agent("V-P1-verify", type="codex", provider="codex", package="P1", phase="verify", started=10, ended=5)
        self.finish("V-P1-verify", "- Item A: teilweise\nOffene Anforderungen: 1")
        b = self.calls()
        self.round(self.brain_answer({"action": "note", "text": "x"}))
        self.assertEqual(self.calls(), b)
        self.assertIsNone(self.ag("DUMMY-P1-work-2"))
        self.reset_rt()
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}, {"id": "P3", "title": "Blender models, textures assets", "depends": ["P9"]}])
        self.agent("Z-ZZ-verify", type="codex", provider="codex", package="P1", phase="verify", status="failed", started=3, ended=2, last_line="x")
        self.round(self.brain_answer({"action": "start_agent", "name": "DUMMY-P3-work", "type": "claude", "phase": "work", "plan": REL,
                                      "package": "P3", "cwd": str(self.wt), "prompt": "Do P3.", "graphics": False}))
        self.assertEqual(self.ag("DUMMY-P3-work")["type"], "codex", "a graphics package sent as Claude starts as Codex")

    def test_v1_fix_plan_without_worktree(self):
        """Fix from v1: a plan with a package table but no known worktree is asked once, then started by the routine in the main checkout."""
        self.plan("IN_PROGRESS", P12)
        note = self.brain_answer({"action": "note", "text": "ok"})
        self.round(note)
        self.assertIsNone(self.ag("DUMMY-P1-work"))
        self.assertEqual(self.calls(), 1)
        self.round()
        row = self.ag("DUMMY-P1-work")
        self.assertTrue(row and row["status"] == "running")
        self.assertEqual(row["cwd"], str(self.repo))

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

    def test_v1_fix_verify_without_count_restarts(self):
        """Fix from v1: a verify answer without any count line starts the verify again, at most 3 verifies per package."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])
        self.agent("W-P1-work", package="P1", started=40, ended=35)
        for i in (1, 2):
            self.agent("V%d-P1-verify" % i, type="codex", provider="codex", package="P1", phase="verify", started=30 - 10 * i, ended=25 - 10 * i)
            self.finish("V%d-P1-verify" % i, "no count here")
        self.round(self.brain_answer({"action": "note", "text": "x"}))
        self.assertTrue(self.ag("DUMMY-P1-verify"), "second verify without a count starts a third one")
        self.finish("DUMMY-P1-verify", "still no count")
        self.round(self.brain_answer({"action": "note", "text": "x"}))
        self.assertIsNone(self.ag("DUMMY-P1-verify-2"), "at most 3 verifies per package")


if __name__ == "__main__":
    unittest.main()
