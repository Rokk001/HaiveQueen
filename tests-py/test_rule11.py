"""Port of test-rule11.ps1 and test-restart.ps1 and test-fixes.ps1: rule 11, restart-safe follow-ups, slug names, blocked row without a plan."""
import unittest

from orchtest import REL, RoundCase, runtime, write_plan
from orchlib import scheduler as sch


class Rule11Test(RoundCase):
    """Only READY or IN_PROGRESS plans may get an agent."""

    def test_rule_11(self):
        """A forced brain answer tries to start an agent in four plans (DRAFT, PAUSED, READY, no header); only the READY plan starts."""
        actions = []
        for stem, status in (("DRAFT", "DRAFT"), ("PAUSED", "PAUSED"), ("READY", "READY"), ("NOSTATUS", "")):
            write_plan(self.repo / "docs/plans/dummy" / ("%s-PLAN.md" % stem), status, [{"id": "P1", "title": "first"}], "Dummy plan " + stem)
            actions.append({"action": "start_agent", "name": "R11-%s-work" % stem, "type": "claude", "phase": "work",
                            "plan": "docs/plans/dummy/%s-PLAN.md" % stem, "package": "P1", "cwd": str(self.wt), "prompt": "Do package P1."})
        self.round(self.brain_answer(*actions))
        self.assertEqual(self.ag("R11-READY-work")["status"], "running")
        log = self.log()
        for k in ("DRAFT", "PAUSED", "NOSTATUS"):
            self.assertIsNone(self.ag("R11-%s-work" % k))
            self.assertRegex(log, r"(?s)R11-%s-work.*?rule 11" % k)
        runtime.sync_plans(self.conn, "testproj", str(self.repo))
        st = {p["path"]: p["status"] for p in runtime.list_plans(self.conn, "testproj")}
        self.assertEqual(st["docs/plans/dummy/READY-PLAN.md"], "IN_PROGRESS")
        self.assertIn('status = "IN_PROGRESS"', (self.repo / "docs/plans/dummy/READY-PLAN.md").read_text(encoding="utf-8"))
        self.assertEqual((st["docs/plans/dummy/DRAFT-PLAN.md"], st["docs/plans/dummy/PAUSED-PLAN.md"]), ("DRAFT", "PAUSED"))


class RestartTest(RoundCase):
    """Follow-up tasks are found from the STATE after a restart of the loop."""

    def test_follow_ups_after_restart(self):
        """A failed 'pane never started' work agent restarts once; a verify with open items gets one work agent; running follow-ups start nothing."""
        self.plan("IN_PROGRESS", [{"id": "A1", "title": "first"}, {"id": "A2", "title": "second"}])
        self.agent("TT-A1-work", package="A1", started=30, ended=29, last_line="ok")
        self.agent("TT-A1-verify", package="A1", phase="verify", started=20, ended=19, last_line="ok")
        self.finish("TT-A1-verify", "Nein, teilweise.\nOffene Anforderungen: 3")
        self.agent("TT-A2-work", package="A2", status="failed", started=10, ended=9, last_line="pane never started (no runner process)")
        from orchlib import routine
        for restart in (1, 2):   # two "restarts": the same state gives the same result
            env = self.env()
            ctx = {"plans": [{"path": REL, "status": "IN_PROGRESS"}], "queue": [], "all_rows": [], "questions": [], "can_start_integrator": False,
                   "free": {"claude": 2, "codex": 2, "workers": 4, "int": 0}}
            starts = [a for a in routine.get_routine(env, ctx)["actions"] if a["action"] == "start_agent"]
            self.assertEqual(len([a for a in starts if a["package"] == "A1" and a["phase"] == "work"]), 1, restart)
            self.assertEqual(len([a for a in starts if a["package"] == "A2" and a["phase"] == "work"]), 1, restart)
            self.assertEqual(len(starts), 2, restart)
        self.agent("TT-A1-work-2", package="A1", status="running", started=1)
        self.agent("TT-A2-work-2", package="A2", status="running", started=1)
        ctx["free"] = {"claude": 2, "codex": 2, "workers": 4, "int": 0}
        starts = [a for a in routine.get_routine(self.env(), ctx)["actions"] if a["action"] == "start_agent"]
        self.assertEqual(starts, [])


class FixesTest(RoundCase):
    """Slug names and a blocked queue row without a plan."""

    def test_slug(self):
        """Folder and branch names are slugs."""
        self.assertEqual(sch.to_slug("R4 experience, arena, prison, torture, undead"), "r4-experience-arena-prison-torture-undead")
        self.assertEqual(sch.to_slug("  A/B -- x__y "), "a-b-x-y")
        self.assertEqual(sch.to_slug(",,, "), "pkg")
        self.assertRegex(sch.to_slug("Feature/Mood: Test #1!"), r"^[a-z0-9]+(-[a-z0-9]+)*$")

    def test_prompt_template_slugs(self):
        """The main-checkout template carries the slug folder and sub-branch names."""
        from orchlib import routine
        env = self.env()
        slug = sch.to_slug("R4 experience, arena, prison")
        text = routine.prompt(env, "workInMainCheckout", {"cwd": str(self.repo), "newWorktree": "x/proj-dummy-" + slug, "subBranch": "dummy-" + slug})
        self.assertIn("proj-dummy-r4-experience-arena-prison", text)
        self.assertNotIn("experience,", text)

    def test_bundled_prompt_uses_slugs(self):
        """A package started in the main checkout gets an own worktree with slug names."""
        self.agent("SEED-work", package="SEED", cwd=str(self.repo), started=30, ended=20)
        self.plan("IN_PROGRESS", [{"id": "R4 experience, arena", "title": "x"}])
        self.round()
        name = [n for n in self.names() if n.startswith("DUMMY-") and n.endswith("-work")][0]
        text = (self.env().run_dir(name) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("dummy-r4-experience-arena", text)
        self.assertIn("only lower-case letters, digits and hyphens", text)

    def test_blocked_row_without_plan(self):
        """A blocked queue row without a plan starts a fix agent with the row text as its task, once; an unknown plan is still skipped."""
        self.plan("DONE", [{"id": "P1", "title": "first"}])
        runtime.queue_upsert(self.conn, "testproj", "chore/no-plan-fix", "feed1234abcd", "keiner", "blocked", "two checks red: fix the two checks")
        self.round()
        fx = [a for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "FIX-feed123"]
        self.assertEqual(len(fx), 1)
        self.assertEqual(fx[0]["phase"], "work")
        prompt = (self.env().run_dir(fx[0]["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("blockiert two checks red", prompt)
        self.assertIn("fix the two checks", prompt)
        self.assertIn("chore/no-plan-fix", prompt)
        self.assertIn("fix-chore-no-plan-fix", prompt)
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj") if a["package"] == "FIX-feed123"]), 1)
        self.conn.execute("DELETE FROM agents")
        self.conn.execute("DELETE FROM queue")
        runtime.queue_upsert(self.conn, "testproj", "chore/other", "bad99999", "some/unknown/PLAN.md", "blocked", "x: y")
        self.round()
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["package"].startswith("FIX-")])


if __name__ == "__main__":
    unittest.main()
