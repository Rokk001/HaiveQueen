"""The Hives list: every plan of every Apiary with its status, and the right-click actions with their rules."""
import inspect
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bzztest import REL, RoundCase, write_plan  # noqa: E402
from haivequeen import plan_view, hives  # noqa: E402

PKG = [{"id": "P1", "title": "First"}]
OTHER = "docs/plans/dummy/OTHER.md"


def yes(_text):
    """A confirmation that always agrees."""
    return True


class ListTests(RoundCase):
    """The rows of the list."""

    def test_every_plan_of_every_apiary_is_listed_with_its_status(self):
        """Plans with and without a Hive, in two Apiaries, a plan file never read into the table included."""
        ws2 = self.add_project("second")
        self.plan("READY", PKG)
        self.plan("DONE", PKG, project="second")
        write_plan(self.repo / OTHER, "DRAFT", [], "Never read")
        write_plan(ws2 / OTHER, "REJECTED", [], "Rejected one")
        rows = {(r["apiary"], r["plan_path"]): r["status"] for r in plan_view.plan_rows(self.conn)}
        self.assertEqual(rows, {("testproj", REL): "QUEUED", ("testproj", OTHER): "DRAFT", ("second", REL): "DONE", ("second", OTHER): "CANCELLED"})

    def test_a_plan_with_a_hive_is_marked(self):
        """Only a plan that had a Hive says so."""
        self.plan("READY", PKG)
        write_plan(self.repo / OTHER, "DRAFT", [], "No plan")
        self.round()
        rows = {r["plan_path"]: r["has_hive"] for r in plan_view.plan_rows(self.conn)}
        self.assertTrue(rows[REL])
        self.assertFalse(rows[OTHER])


class ActionTests(RoundCase):
    """Which actions a status offers and what they do."""

    def row(self):
        """The list row of the dummy plan."""
        return next(r for r in plan_view.plan_rows(self.conn) if r["plan_path"] == REL)

    def offered(self, status):
        """Action names offered for the dummy plan in this status."""
        self.plan(status, PKG)
        return [a for _, a in plan_view.actions(self.row())]

    def status(self):
        """Status of the dummy plan in the table."""
        return self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]

    def test_offered_actions_by_status(self):
        """The rules per status."""
        self.assertEqual(self.offered("DRAFT"), ["start", "reject"])
        self.assertEqual(self.offered("QUEUED"), ["pause"])
        self.assertEqual(self.offered("SENT"), ["pause"])
        self.assertEqual(self.offered("RUNNING"), ["pause"])
        self.assertEqual(self.offered("DONE"), [])
        self.assertEqual(self.offered("CANCELLED"), ["start", "draft"])

    def test_a_stopped_hive_offers_start_reject_and_draft(self):
        """PAUSED without a live Queen is a stopped Hive."""
        self.plan("READY", PKG)
        hives.pause_plan(self.conn, "testproj", REL)
        self.assertEqual([a for _, a in plan_view.actions(self.row())], ["start", "reject", "draft"])

    def test_every_action_asks_first(self):
        """Declining changes nothing, confirming does it."""
        self.plan("DRAFT", PKG)
        self.assertFalse(plan_view.run_action(self.conn, self.row(), "start", lambda text: False))
        self.assertEqual(self.status(), "DRAFT")
        self.assertTrue(plan_view.run_action(self.conn, self.row(), "start", yes))
        self.assertEqual(self.status(), "SENT")

    def test_start_draft_ready_stopped_before_and_rejected_draft(self):
        """Start: a stopped plan gets its earlier status, a cancelled plan DRAFT, a draft SENT."""
        self.plan("IN_PROGRESS", PKG)
        hives.pause_plan(self.conn, "testproj", REL)
        plan_view.run_action(self.conn, self.row(), "start", yes)
        self.assertEqual(self.status(), "RUNNING")
        self.plan("REJECTED", PKG)
        plan_view.run_action(self.conn, self.row(), "start", yes)
        self.assertEqual(self.status(), "DRAFT")

    def test_pause_and_reject(self):
        """Pause a READY plan; reject a stopped one and a draft; a running one cannot be rejected."""
        self.plan("READY", PKG)
        plan_view.run_action(self.conn, self.row(), "pause", yes)
        self.assertEqual(self.status(), "PAUSED")
        plan_view.run_action(self.conn, self.row(), "reject", yes)
        self.assertEqual(self.status(), "CANCELLED")
        self.plan("DRAFT", PKG)
        plan_view.run_action(self.conn, self.row(), "reject", yes)
        self.assertEqual(self.status(), "CANCELLED")
        self.plan("READY", PKG)
        with self.assertRaises(ValueError):
            hives.reject_plan(self.conn, "testproj", REL)

    def test_back_to_draft_only_for_stopped_or_rejected(self):
        """A stopped and a rejected plan become DRAFT (in the file too); a running one is refused."""
        self.plan("READY", PKG)
        with self.assertRaises(ValueError):
            hives.back_to_draft(self.conn, "testproj", REL)
        hives.pause_plan(self.conn, "testproj", REL)
        hives.back_to_draft(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "DRAFT")
        self.assertIn('status = "DRAFT"', (self.repo / REL).read_text(encoding="utf-8"))
        self.plan("REJECTED", PKG)
        hives.back_to_draft(self.conn, "testproj", REL)
        self.assertEqual(self.status(), "DRAFT")

    def test_action_on_a_plan_the_table_does_not_know(self):
        """A plan file never read into the table can be started."""
        write_plan(self.repo / OTHER, "DRAFT", PKG, "Never read")
        row = next(r for r in plan_view.plan_rows(self.conn) if r["plan_path"] == OTHER)
        plan_view.run_action(self.conn, row, "start", yes)
        self.assertEqual(self.conn.execute("SELECT status FROM plans WHERE path=?", (OTHER,)).fetchone()[0], "SENT")


if __name__ == "__main__":
    unittest.main()
