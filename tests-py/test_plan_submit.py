"""A plan sent as SENT (formerly SUBMITTED) gets a Queen who cuts it, bzz plan check verifies the cut, and capabilities are not tied to one provider."""
import contextlib
import io
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import bees, cli, hives, plans


def plan_of(packages, status="READY", branch="b", worktree="/w"):
    """Build a Plan from package tuples (id, title, depends_on, capability)."""
    return plans.Plan(path="x.md", status=status, packages=[plans.Package(id=i, title=t, depends_on=d, capability=c) for i, t, d, c in packages], branch=branch, worktree=worktree)


class CheckPlanTests(unittest.TestCase):
    """plans.check_plan: what keeps a cut plan from running."""

    def test_a_good_plan_has_no_problem(self):
        """Packages with titles, known dependencies (also all) and a capability some provider offers pass."""
        plan = plan_of([("P1", "First", [], ""), ("P2", "Speech", ["P1"], "speech"), ("FINAL", "Final", ["all"], "")])
        self.assertEqual(plans.check_plan(plan, {"speech"}), [])

    def test_each_problem_is_reported(self):
        """No package, a repeated id, a missing title, an unknown dependency, a cycle and an unknown capability are each named."""
        self.assertIn("the header lists no package", plans.check_plan(plan_of([]), set())[0])
        text = " ".join(plans.check_plan(plan_of([("A", "x", [], ""), ("A", "y", [], "")]), set()))
        self.assertIn("used more than once", text)
        self.assertIn("has no title", " ".join(plans.check_plan(plan_of([("A", " ", [], "")]), set())))
        self.assertIn("not in the plan", " ".join(plans.check_plan(plan_of([("A", "x", ["Z"], "")]), set())))
        self.assertIn("dependency cycle", " ".join(plans.check_plan(plan_of([("A", "x", ["B"], ""), ("B", "y", ["A"], "")]), set())))
        self.assertIn("no configured provider offers", " ".join(plans.check_plan(plan_of([("A", "x", [], "speech")]), {"images"})))

    def test_waiting_for_a_capability_package_is_warned_about(self):
        """A package without a capability that depends on one with a capability is reported; all, the capability package itself and independent packages are not."""
        plan = plan_of([("I1", "Images", [], "images"), ("W1", "Window", ["I1"], ""), ("S1", "Speech", ["I1"], "speech"), ("C1", "Code", [], ""), ("FINAL", "Final", ["all"], "")])
        warnings = plans.plan_warnings(plan)
        self.assertEqual(len(warnings), 1)
        self.assertIn("package W1 waits for I1", warnings[0])
        self.assertEqual(plans.check_plan(plan, {"images", "speech"}), [])   # a hint, not a blocking problem

    def test_submitted_gets_a_queen_but_is_not_startable(self):
        """SENT (old name SUBMITTED) is a known status that gets a Queen, while its (missing) packages are never startable."""
        self.assertEqual(plans.parse_plan_text('+++\nstatus = "submitted"\n+++\n# T\n').status, "SENT")
        for status in ("SENT", "PREPARING"):
            self.assertIn(status, plans.QUEEN_STATUSES)
            self.assertNotIn(status, plans.STARTABLE_STATUSES)


class PlansTableMigrationTests(unittest.TestCase):
    """An old database whose plans table does not know SUBMITTED keeps every row when it is migrated."""

    def test_old_plans_table_is_rebuilt_without_losing_rows(self):
        """The CHECK list gains the new statuses; projects, paths, titles and times of the existing plans stay as they were and old status names get their new name."""
        import sqlite3
        from haivequeen import db
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.execute("CREATE TABLE plans (project TEXT NOT NULL, path TEXT NOT NULL, title TEXT, status TEXT CHECK (status IN ('DRAFT','READY','IN_PROGRESS','DONE','PAUSED')), updated_at TEXT, PRIMARY KEY (project, path))")
        rows = [("P", "a.md", "A", "READY", "2026-11-01T10:00:00"), ("P", "b.md", "B", "IN_PROGRESS", "2026-11-02T10:00:00"), ("Q", "c.md", "C", "DONE", None)]
        conn.executemany("INSERT INTO plans VALUES (?,?,?,?,?)", rows)
        db.rebuild_plans_table(conn, db.SCHEMA_FILE.read_text(encoding="utf-8"))
        renamed = [("P", "a.md", "A", "QUEUED", "2026-11-01T10:00:00"), ("P", "b.md", "B", "RUNNING", "2026-11-02T10:00:00"), ("Q", "c.md", "C", "DONE", None)]
        self.assertEqual(conn.execute("SELECT project,path,title,status,updated_at FROM plans ORDER BY path").fetchall(), renamed)
        conn.execute("INSERT INTO plans VALUES ('P','d.md','D','SENT',NULL)")
        db.rebuild_plans_table(conn, db.SCHEMA_FILE.read_text(encoding="utf-8"))   # a second run changes nothing
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0], 4)


class CapabilityRoutingTests(unittest.TestCase):
    """A capability is whatever a provider lists, not only images on Codex."""

    def setUp(self):
        from bzztest import verified_tool_evidence
        verified_tool_evidence(self, {'b': ['speech']})


    def test_any_capability_routes_to_the_providers_that_offer_it(self):
        """A package needing "speech" runs on the provider that offers it, whatever its protocol, and escalates only among those."""
        providers = [{"name": "a", "protocol": "claude", "classes": {"medium": {"model": "m"}}, "capabilities": []},
                     {"name": "b", "protocol": "claude", "classes": {"medium": {"model": "n"}}, "capabilities": ["speech"]}]
        self.assertEqual([p["name"] for p in bees.route_candidates(providers, "a", ["speech"], False)], ["b"])
        self.assertEqual(bees.select_route(providers, {"a": 1, "b": 1}, "a", "medium", ["speech"], [], False), ("b", "medium"))
        self.assertIsNone(bees.select_route(providers, {"a": 1, "b": 0}, "a", "medium", ["speech"], [], False))


class SubmittedPlanTests(RoundCase):
    """The Queen of a SENT plan, bzz plan check and the start gate for Bees."""

    def check(self):
        """Run bzz plan check as the Queen and return (exit code, output)."""
        args = cli.build_parser().parse_args(["plan", "check", "--project", "testproj", "--plan", REL])
        output = io.StringIO()
        with mock.patch.dict("os.environ", {"BZZ_AGENT": "queen"}), mock.patch.object(cli.config, "load_config", return_value=self.machine), contextlib.redirect_stdout(output):
            code = cli.cmd_plan(args, self.conn)
        return code, output.getvalue()

    def queen_of(self, status, packages):
        """Write the plan with the given status, give it a Hive and a running Queen."""
        self.plan(status, packages, branch="fix/x", worktree=str(self.base / "wt-x"))
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("queen", phase="queen", status="running", provider="claude", pid=42)

    def test_a_submitted_plan_gets_a_queen_who_is_told_to_cut_it(self):
        """The Queen of a plan without packages starts and her task says to cut the plan and run bzz plan check."""
        self.plan("SUBMITTED", [])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        prompt = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertIn("This plan is SENT", prompt)
        self.assertIn("bzz plan check", prompt)

    def test_a_ready_plan_is_not_told_to_cut(self):
        """A plan whose header already lists packages runs as before."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        self.assertNotIn("This plan is SENT", (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig"))

    def test_a_good_cut_makes_the_plan_ready(self):
        """After the Queen wrote the packages, bzz plan check sets the file and the table to QUEUED and reads the packages."""
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second", "depends": ["P1"]}])
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertEqual(plans.load_plan(str(self.repo / REL)).status, "QUEUED")
        self.assertEqual([p["id"] for p in runtime.list_packages(self.conn, "testproj", REL)], ["P1", "P2"])
        self.assertEqual(next(p for p in runtime.list_plans(self.conn, "testproj") if p["path"] == REL)["status"], "QUEUED")

    def test_a_bad_cut_changes_nothing(self):
        """A dependency on a package that is not there is reported and the plan stays SENT."""
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "First", "depends": ["Z9"]}])
        code, output = self.check()
        self.assertNotEqual(code, 0)
        self.assertIn("Z9", output)
        self.assertEqual(plans.load_plan(str(self.repo / REL)).status, "SENT")

    def test_no_bee_starts_before_the_plan_is_cut(self):
        """bzz bee start is refused while the plan is SENT."""
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "First"}])
        self.assertFalse(bees.start_process(self.env(), REL, "P1", "Ada", "medium", "task", [], [], []))
        self.assertIn("not cut yet", bees.LAST_REFUSAL)


if __name__ == "__main__":
    unittest.main()
