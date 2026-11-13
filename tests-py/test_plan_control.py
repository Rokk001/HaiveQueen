"""Plan status CANCELLED (formerly REJECTED) and Hive control: pause, stop, cancel and start again keep the status the plan had and ask first."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bzztest import REL, RoundCase, runtime  # noqa: E402
from haivequeen import db, hives, plans  # noqa: E402

OLD_PLANS_TABLE = ("CREATE TABLE plans (project TEXT NOT NULL, path TEXT NOT NULL, title TEXT, status TEXT CHECK (status IN "
                   "('DRAFT','SUBMITTED','READY','IN_PROGRESS','DONE','PAUSED')), updated_at TEXT, PRIMARY KEY (project, path))")


class PlanControlTests(RoundCase):
    """The status changes of the plan control and their effect on the scheduler."""

    def live(self):
        """Active Queen and Bee rows of the dummy plan."""
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]

    def status(self):
        """Status of the dummy plan in the table."""
        return self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]

    def file_text(self):
        """Text of the dummy plan file."""
        return (self.repo / REL).read_text(encoding="utf-8")

    def test_rejected_is_read_and_written(self):
        """CANCELLED is a known status and the old name REJECTED is read as it: read from the plan file, stored in the table and written back to the file."""
        self.assertIn("CANCELLED", plans.STATUSES)
        self.assertEqual(plans.normalize_status("rejected"), "CANCELLED")
        self.plan("REJECTED", [{"id": "P1", "title": "First"}])
        self.assertEqual(self.status(), "CANCELLED")
        self.plan("READY", [{"id": "P1", "title": "First"}])
        runtime.set_plan_status(self.conn, "testproj", REL, "CANCELLED")
        self.assertIn('status = "CANCELLED"', self.file_text())
        self.assertEqual(self.status(), "CANCELLED")

    def test_old_database_accepts_rejected(self):
        """A plans table created before CANCELLED existed is rebuilt so that the status can be stored."""
        conn = db.connect(Path(self.base) / "old.db")
        conn.execute("DROP TABLE plans")
        conn.execute(OLD_PLANS_TABLE)
        conn.execute("PRAGMA user_version = 9")
        db.migrate(conn)
        conn.execute("INSERT INTO plans (project, path, status) VALUES ('p','x.md','CANCELLED')")
        conn.close()

    def test_a_rejected_plan_never_starts(self):
        """No Queen starts for a REJECTED plan, round after round."""
        self.plan("REJECTED", [{"id": "P1", "title": "First"}])
        self.round()
        self.round()
        self.assertEqual(self.live(), [])
        self.assertEqual(self.names(), [])

    def test_start_again_on_rejected_sets_draft(self):
        """Starting a rejected plan again sets DRAFT in table and file, and a draft does not start by itself."""
        self.plan("REJECTED", [{"id": "P1", "title": "First"}])
        hives.start_again(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "DRAFT")
        self.assertIn('status = "DRAFT"', self.file_text())
        self.round()
        self.assertEqual(self.live(), [])

    def test_pause_keeps_the_earlier_status_and_start_again_restores_it(self):
        """Pause sets PAUSED and keeps RUNNING (also over a second pause); start again gives RUNNING back."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        hives.pause_plan(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "PAUSED")
        runtime.sync_plans(self.conn, "testproj", str(self.repo))   # as after a restart
        hives.pause_plan(self.conn, "testproj", REL)   # a second pause must not overwrite the kept status
        hives.start_again(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "RUNNING")
        self.assertIn('status = "RUNNING"', self.file_text())

    def test_pause_starts_nothing_new_but_keeps_the_queen(self):
        """After a pause a running Queen is not ended."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        self.assertEqual(len(self.live()), 1)
        hives.pause_plan(self.conn, "testproj", REL)
        self.round()
        self.assertEqual(len(self.live()), 1)

    def test_stop_ends_queen_and_bees_and_pauses(self):
        """Stop ends the Queen and her Bees, sets PAUSED and keeps plan, packages and the earlier status."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        self.agent("Bee1", plan_path=REL, phase="bee", status="running", pid=401)
        before = self.status()   # a started plan is RUNNING
        stopped = []
        hives.stop_hive(self.conn, "testproj", REL, stop=lambda pid: stopped.append(pid) or True)
        self.assertEqual(self.live(), [])
        self.assertIn(401, stopped)
        self.assertEqual(self.status(), "PAUSED")
        self.assertIsNotNone(hives.get(self.conn, "testproj", REL))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM packages WHERE project='testproj'").fetchone()[0], 1)
        self.round()
        self.assertEqual(self.live(), [])
        hives.start_again(self.conn, "testproj", REL)
        self.assertEqual(self.status(), before)

    def test_reject_only_for_a_stopped_hive(self):
        """Reject works on a stopped Hive (PAUSED, no live Queen) and refuses a running or a merely paused one with a Queen."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        with self.assertRaises(ValueError):
            hives.reject_plan(self.conn, "testproj", REL)
        hives.pause_plan(self.conn, "testproj", REL)
        with self.assertRaises(ValueError):
            hives.reject_plan(self.conn, "testproj", REL)
        hives.stop_hive(self.conn, "testproj", REL, stop=lambda pid: True)
        hives.reject_plan(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "CANCELLED")
        self.assertIsNotNone(hives.get(self.conn, "testproj", REL))   # nothing is deleted
        self.round()
        self.assertEqual(self.live(), [])


if __name__ == "__main__":
    unittest.main()
