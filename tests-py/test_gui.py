"""Dashboard snapshots built from invented rows in a temporary database."""
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import bees, db, dashboard_data, hives, runstate, runtime  # noqa: E402

NOW = datetime(2026, 11, 1, 12, 0, 0)
SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}
CFG = {"slots": 2, "providers": [{"name": "claude"}, {"name": "codex"}]}


# Build the iso time a number of minutes before NOW.
def iso(minutes_ago):
    """Time string a number of minutes before NOW."""
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


# Test the snapshot of invented data.
class SnapshotTest(unittest.TestCase):
    """One Project with one Plan, a running Queen and two running Agents."""

    # Create a temp database with a project, a Hive, a Queen, Bees, packages, an event and a queue row.
    def setUp(self):
        """Create the invented data."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        hives.create(self.conn, "ODP", plan, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        for pid in ("P1", "P2", "P3", "P4"):
            self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES ('ODP',?,?,?)", (plan, pid, pid))
        bees.set_package_state(self.conn, "ODP", plan, "P3", "done")
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": plan, "phase": "queen", "provider": "claude", "model": "claude-sonnet-5-5",
                                                              "effort": "medium", "status": "running", "startedAt": iso(65), "person": "Ava"})
        for name, status, pkg, tin in (("Sophia", "running", "P1", 120000), ("Emma", "running", "P2", 90000), ("Mia", "done", "P3", 50000)):
            runtime.upsert_agent(self.conn, "ODP", "ODP__" + name, {"plan": plan, "phase": "bee", "provider": "claude", "model": "claude-sonnet-5-5",
                                                                    "effort": "medium", "status": status, "startedAt": iso(12), "person": name,
                                                                    "package": pkg, "tokensIn": tin, "tokensOut": 3000})
        runtime.append_event(self.conn, "ODP", "accepted plan AI-LEVELS", kind="note", agent="Ava__queen", mirror="none")   # no log file: tests must not write outside their temp folders
        self.conn.execute("INSERT INTO queue(project,branch,tip,status,updated_at) VALUES ('ODP','b1','abc','ready',?)", (iso(1),))
        self.conn.commit()
        no_loop = mock.patch("haivequeen.loop.loop_running", return_value=0)   # the state must not depend on a real orchestrator on this machine
        no_loop.start()
        self.addCleanup(no_loop.stop)
        self.snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)

    # Close the database.
    def tearDown(self):
        """Release the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    # The tiles count what runs.
    def test_tiles(self):
        """One Project, one Plan, one Queen, two Agents: the busy tile counts all three against the General setting Agent slots."""
        t = self.snap["tiles"]
        self.assertEqual((t["apiaries"], t["hives"], t["queens"], t["bees"]), (1, 1, 1, 3))
        self.assertEqual(t["bee_capacity"], CFG["slots"])

    # The Hive row shows queen, bees, progress and runtime.
    def test_hive_row(self):
        """The Plan has queen Ava, 2 Agents running of 2 open, 1 of 4 packages done (25%) and runs for 1:05:00."""
        h = self.snap["hives"][0]
        self.assertEqual((h["name"], h["apiary"], h["queen"]), ("AI-LEVELS", "ODP", "Ava"))
        self.assertEqual((h["bees_running"], h["bees_total"], h["progress"]), (2, 2, 25))
        self.assertEqual((h["done"], h["total"]), (1, 4))
        self.assertEqual((h["status"], h["kind"], h["runtime"]), ("Running", "ok", "1:05:00"))

    # Package progress comes from persisted done states; waiting Bees come from assignments.
    def test_bee_tables_drive_progress_and_waiting_bees(self):
        """Done package states count as progress; a package with an Agent assignment but no process shows as a waiting Agent."""
        bees.ensure_attempts(self.conn)
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        self.conn.execute("INSERT INTO bee_completed VALUES ('ODP',?,'P1')", (plan,))
        self.conn.execute("INSERT INTO bee_completed VALUES ('ODP',?,'P2')", (plan,))
        bees.set_package_state(self.conn, "ODP", plan, "P1", "done")
        bees.set_package_state(self.conn, "ODP", plan, "P2", "done")
        self.conn.execute("INSERT INTO bee_assignments VALUES ('ODP',?,'P4','heavy','codex')", (plan,))
        self.conn.commit()
        snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        h = snap["hives"][0]
        self.assertEqual((h["done"], h["total"], h["progress"]), (3, 4, 75))   # P1, P2 by Bees and P3 by the finished Bee agent row
        self.assertEqual(h["bees_total"], 3)   # two running Bees and the waiting assignment of P4
        waiting = [b for b in snap["bees"] if b["status"] == "waiting"]
        self.assertEqual([(b["name"], b["hive"], b["model"], b["task"]) for b in waiting], [("–", "AI-LEVELS", "–", "P4")])   # no name and no stored provider before the Bee starts

    # A plan whose status is PAUSED shows as paused in its Hive row, without a Queen and with one.
    def test_paused_plan_shows_in_the_hive_row(self):
        """The Plan row of a PAUSED plan is flagged and says so; a plan that is not paused keeps its normal status."""
        self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','AI','PAUSED','2026-11-01T12:00:00')")
        self.conn.commit()
        snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        row = snap["hives"][0]
        self.assertTrue(row["paused"])
        self.assertIn("paused", row["status"])
        self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','AI','QUEUED','2026-11-01T12:00:00')")
        self.conn.commit()
        row = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)["hives"][0]
        self.assertFalse(row["paused"])
        self.assertNotIn("paused", row["status"])

    # Active Hives show running and waiting Hives only.
    def test_rejected_and_done_hives_are_not_active(self):
        """A Plan of a REJECTED or DONE plan, also one set in the plan file and synced, is absent from the active list; a done Plan state too."""
        plan = "docs/plans/AI-LEVELS-PLAN.md"
        def active():
            snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
            return snap["hives"], snap["tiles"]["hives"]
        self.assertEqual(len(active()[0]), 1)
        for status in ("CANCELLED", "DONE"):
            self.conn.execute("INSERT OR REPLACE INTO plans (project, path, title, status, updated_at) VALUES ('ODP',?,'AI',?,'2026-11-01T12:00:00')", (plan, status))
            self.conn.commit()
            self.assertEqual(active(), ([], 0), status)
        self.conn.execute("UPDATE plans SET status='QUEUED'")
        self.conn.execute("UPDATE hives SET state='done'")
        self.conn.commit()
        self.assertEqual(active()[0], [])

    # Only a running Queen starts Bees, so the assignments of a Hive without one are not waiting Bees.
    def test_assignments_of_a_hive_without_a_running_queen_are_not_waiting_bees(self):
        """With the Queen ended, the stored Agent assignment of the Plan is not shown as a waiting Agent and does not count as an Agent of the Plan."""
        from haivequeen import bees
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments VALUES ('ODP','docs/plans/AI-LEVELS-PLAN.md','P4','heavy','codex')")
        self.conn.execute("UPDATE agents SET status='done' WHERE phase='queen'")
        self.conn.commit()
        snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        self.assertEqual([b for b in snap["bees"] if b["status"] == "waiting"], [])
        self.assertEqual(snap["hives"][0]["bees_total"], snap["hives"][0]["bees_running"])

    # Context and tokens per Queen, Bee and Hive.
    def test_context_and_tokens(self):
        """The Queen and the Agents carry their context, its limit and their tokens; the Plan carries the total of all its agents."""
        h = self.snap["hives"][0]
        self.assertEqual(h["queen_ctx_limit"], dashboard_data.CONTEXT_FALLBACK)
        self.assertEqual((h["hive_tin"], h["hive_tout"]), (260000, 9000))
        bee = next(b for b in self.snap["bees"] if b["name"] == "Sophia")
        self.assertEqual((bee["tin"], bee["tout"]), (120000, 3000))
        self.assertEqual(bee["ctx_limit"], dashboard_data.CONTEXT_FALLBACK)

    # The model limits are one row per provider.
    def test_limits_are_rows_per_provider(self):
        """Every provider of the settings has one limit row with its reserve, slots, weekly line and 5-hour line."""
        self.assertEqual([r["name"] for r in self.snap["limits"]], ["Claude", "Codex"])
        for item in self.snap["limits"]:
            self.assertEqual(set(item), {"name", "model", "next", "reserve", "slots", "weekly", "five"})

    # Everything the window prints is English.

    # The Queen heads the list and her Bees follow directly below her.
    def test_bees(self):
        """The running Queen Ava comes first with her model and effort, then her two running Agents; the finished Agent is not listed."""
        rows = self.snap["bees"]
        self.assertEqual(rows[0]["kind"], "queen")
        self.assertEqual(rows[0]["name"], "Ava")
        self.assertEqual(sorted(b["name"] for b in rows[1:]), ["Emma", "Sophia"])
        self.assertEqual({b["kind"] for b in rows[1:]}, {"bee"})
        self.assertEqual({b["hive"] for b in rows}, {"AI-LEVELS"})
        self.assertEqual((rows[0]["model"], rows[0]["task"], rows[0]["status"]), ("Son 5.5 med.", "Queen", "running"))
        self.assertEqual(rows[0]["ctx_limit"], dashboard_data.CONTEXT_FALLBACK)

    # Without a running Queen the Bees stay in the list, without a Queen row.
    def test_bees_without_a_running_queen_have_no_queen_row(self):
        """With the Queen ended the two running Agents are still listed and no row is a Queen row."""
        self.conn.execute("UPDATE agents SET status='done' WHERE phase='queen'")
        self.conn.commit()
        snap = dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)
        self.assertEqual([b["kind"] for b in snap["bees"]], ["bee", "bee"])
        self.assertEqual(snap["tiles"]["queens"], 0)

    # Every log line has the id of its event.
    def test_log_ids_follow_the_log(self):
        """The ids run parallel to the lines, oldest first."""
        self.assertEqual(len(self.snap["log_ids"]), len(self.snap["log"]))
        self.assertEqual(self.snap["log_ids"], sorted(self.snap["log_ids"]))

    # Tokens of the Hive over seven days.
    def test_activity(self):
        """The Project row comes first, then the Plan row; with one Project and one Plan each holds 100% and the tokens of the agents."""
        apiary, hive = self.snap["activity"][0], self.snap["activity"][1]
        self.assertEqual((apiary["kind"], apiary["name"], apiary["pct"]), ("apiary", "ODP", 100))
        self.assertEqual((hive["kind"], hive["name"], hive["pct"]), ("hive", "AI-LEVELS", 100))
        self.assertEqual((hive["tin"], hive["tout"]), (260000, 9000))

    # Log lines carry their source tag.
    def test_log(self):
        """The Queen's note is tagged as a QUEEN line with its time."""
        stamp, tag, text = self.snap["log"][-1]
        self.assertTrue(tag.startswith("QUEEN"))
        self.assertIn("accepted plan", text)

    # The queue panel counts questions and queued rows.
    def test_queue(self):
        """One queued row labelled ODP#1 and no open question."""
        self.assertEqual((self.snap["queue"]["open"], self.snap["queue"]["queued"]), (0, 1))
        self.assertEqual(self.snap["queue"]["labels"], ["ODP#1"])

    # The window shows what the overview data holds.

    # The build line is part of the dashboard data and of the SYSTEM panel.
    def test_build_line_in_snapshot_without_build_table(self):
        """CFG has no buildJobs table: the STATUS panel SYSTEM section still carries the build line; the SYSTEM meter panel does not."""
        self.assertNotIn("buildJobs", CFG)
        self.assertNotIn("build_line", self.snap)
        lines = [t for title, ls in self.snap["info"] if title == "SYSTEM" for t, _c in ls]
        self.assertTrue(any(re.match(r"^Build jobs \d+ of \d+ cores \((running|next build)\) \| per job not measured yet", t) for t in lines), lines)

    # One state tells the user whether HaiveQueen runs, waits or stands still.
    def test_orchestrator_state(self):
        """No loop process is STOPPED, a pending stop is STOPPING, otherwise the run state decides the label and its colour level."""
        state = dashboard_data.orchestrator_state
        self.assertEqual(state("RUNNING", 0, False), {"label": "STOPPED", "level": "bad"})
        self.assertEqual(state("RUNNING", 5, True), {"label": "STOPPING", "level": "warn"})
        self.assertEqual(state("RUNNING", 5, False), {"label": "RUNNING", "level": "ok"})
        self.assertEqual(state("PAUSED", 5, False), {"label": "PAUSED", "level": "warn"})
        self.assertEqual(self.snap["orchestrator"]["label"], "STOPPED")   # the test runtime has no loop process

    # The run state is shown.
    def test_run_state(self):
        """A paused run shows PAUSED."""
        runstate.pause(self.conn, "test")
        self.assertEqual(dashboard_data.snapshot(self.conn, now=NOW, cfg=CFG, sysinfo=SYS)["run_state"], "PAUSED")

    # The Apiary filter narrows the Hives.
    def test_project_filter(self):
        """An unknown Project shows no Plans."""
        self.assertEqual(dashboard_data.snapshot(self.conn, "none", now=NOW, cfg=CFG, sysinfo=SYS)["hives"], [])

    # Formatting helpers.
    def test_formats(self):
        """Token counts and elapsed times use the compact forms of the mockup."""
        self.assertEqual((dashboard_data.fmt_tokens(964000), dashboard_data.fmt_tokens(1_100_000), dashboard_data.fmt_tokens(36000)), ("964k", "1.1M", "36k"))
        self.assertEqual(dashboard_data.fmt_elapsed(iso(1), NOW), "01:00")
        self.assertEqual(dashboard_data.hive_name("docs/plans/DEMO-PLAN.md"), "DEMO")


# Test the pure parts of the state button, the app icon and the repository link.


def size_supported(width, height, screen_width, screen_height):
    """Whether a requested window size fits on the available display."""
    return width <= screen_width and height <= screen_height


# Draw the window once when a display exists.


if __name__ == "__main__":
    unittest.main()
