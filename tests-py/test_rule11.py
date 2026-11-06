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
        self.assertEqual(st["docs/plans/dummy/READY-PLAN.md"], "RUNNING")
        self.assertIn('status = "RUNNING"', (self.repo / "docs/plans/dummy/READY-PLAN.md").read_text(encoding="utf-8"))
        self.assertEqual((st["docs/plans/dummy/DRAFT-PLAN.md"], st["docs/plans/dummy/PAUSED-PLAN.md"]), ("DRAFT", "PAUSED"))



class FixesTest(RoundCase):
    """Slug names and a blocked queue row without a plan."""

    def test_slug(self):
        """Folder and branch names are slugs."""
        self.assertEqual(sch.to_slug("R4 experience, arena, prison, torture, undead"), "r4-experience-arena-prison-torture-undead")
        self.assertEqual(sch.to_slug("  A/B -- x__y "), "a-b-x-y")
        self.assertEqual(sch.to_slug(",,, "), "pkg")
        self.assertRegex(sch.to_slug("Feature/Mood: Test #1!"), r"^[a-z0-9]+(-[a-z0-9]+)*$")


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
