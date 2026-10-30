"""Port of test-rule11.ps1 and test-restart.ps1 and test-fixes.ps1: rule 11, restart-safe follow-ups, slug names, blocked row without a plan."""
import unittest

from bzztest import REL, RoundCase, runtime, write_plan
from haivequeen import scheduler as sch


class Rule11Test(RoundCase):
    """Only READY or IN_PROGRESS plans may get an agent."""

    # Verify that only eligible plan statuses start a Queen.
    def test_rule_11(self):
        """Only the READY plan starts a Queen; DRAFT, PAUSED and missing status stay unchanged."""
        for stem, status in (("DRAFT", "DRAFT"), ("PAUSED", "PAUSED"), ("READY", "READY"), ("NOSTATUS", "")):
            write_plan(self.repo / "docs/plans/dummy" / ("%s-PLAN.md" % stem), status, [{"id": "P1", "title": "first"}], "Dummy plan " + stem)
        self.round()
        queens = [a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertEqual(queens[0]["plan_path"], "docs/plans/dummy/READY-PLAN.md")
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
        from haivequeen import routine
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
        from haivequeen import routine
        env = self.env()
        slug = sch.to_slug("R4 experience, arena, prison")
        text = routine.prompt(env, "workInMainCheckout", {"cwd": str(self.repo), "newWorktree": "x/proj-dummy-" + slug, "subBranch": "dummy-" + slug})
        self.assertIn("proj-dummy-r4-experience-arena-prison", text)
        self.assertNotIn("experience,", text)

    # Verify a Queen is instructed to create the plan worktree if absent.
    def test_queen_receives_worktree_instruction(self):
        """The Queen's task names the plan and its worktree creation rule."""
        self.agent("SEED-work", package="SEED", cwd=str(self.repo), started=30, ended=20)
        self.plan("IN_PROGRESS", [{"id": "R4 experience, arena", "title": "x"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "queen" and a["status"] == "running")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("create the plan worktree and branch", text)
        self.assertIn(REL, text.replace(chr(92), "/"))

    # Verify external blocked queue rows remain untouched.
    def test_blocked_row_without_hive_is_untouched(self):
        """A blocked row without a Hive starts no fix agent and keeps its status."""
        self.plan("DONE", [{"id": "P1", "title": "first"}])
        runtime.queue_upsert(self.conn, "testproj", "chore/no-plan-fix", "feed1234abcd", "keiner", "blocked", "two checks red: fix the two checks")
        self.round()
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["package"].startswith("FIX-")])
        row = dict(self.conn.execute("SELECT * FROM queue WHERE project=? AND branch=?", ("testproj", "chore/no-plan-fix")).fetchone())
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["note"], "two checks red: fix the two checks")

if __name__ == "__main__":
    unittest.main()
