"""The Hives list: every plan of every Apiary with its status, and the right-click actions with their rules."""
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bzztest import REL, RoundCase, write_plan  # noqa: E402
from haivequeen import gui, gui_hives, hives  # noqa: E402

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
        rows = {(r["apiary"], r["plan_path"]): r["status"] for r in gui_hives.plan_rows(self.conn)}
        self.assertEqual(rows, {("testproj", REL): "READY", ("testproj", OTHER): "DRAFT", ("second", REL): "DONE", ("second", OTHER): "REJECTED"})

    def test_a_plan_with_a_hive_is_marked(self):
        """Only a plan that had a Hive says so."""
        self.plan("READY", PKG)
        write_plan(self.repo / OTHER, "DRAFT", [], "No hive")
        self.round()
        rows = {r["plan_path"]: r["has_hive"] for r in gui_hives.plan_rows(self.conn)}
        self.assertTrue(rows[REL])
        self.assertFalse(rows[OTHER])


class ActionTests(RoundCase):
    """Which actions a status offers and what they do."""

    def row(self):
        """The list row of the dummy plan."""
        return next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == REL)

    def offered(self, status):
        """Action names offered for the dummy plan in this status."""
        self.plan(status, PKG)
        return [a for _, a in gui_hives.actions(self.row())]

    def status(self):
        """Status of the dummy plan in the table."""
        return self.conn.execute("SELECT status FROM plans WHERE project='testproj' AND path=?", (REL,)).fetchone()[0]

    def test_offered_actions_by_status(self):
        """The rules per status."""
        self.assertEqual(self.offered("DRAFT"), ["start", "reject"])
        self.assertEqual(self.offered("READY"), ["pause"])
        self.assertEqual(self.offered("SUBMITTED"), ["pause"])
        self.assertEqual(self.offered("IN_PROGRESS"), ["pause"])
        self.assertEqual(self.offered("DONE"), [])
        self.assertEqual(self.offered("REJECTED"), ["start", "draft"])

    def test_a_stopped_hive_offers_start_reject_and_draft(self):
        """PAUSED without a live Queen is a stopped Hive."""
        self.plan("READY", PKG)
        hives.pause_plan(self.conn, "testproj", REL)
        self.assertEqual([a for _, a in gui_hives.actions(self.row())], ["start", "reject", "draft"])

    def test_every_action_asks_first(self):
        """Declining changes nothing, confirming does it."""
        self.plan("DRAFT", PKG)
        self.assertFalse(gui_hives.run_action(self.conn, self.row(), "start", lambda text: False))
        self.assertEqual(self.status(), "DRAFT")
        self.assertTrue(gui_hives.run_action(self.conn, self.row(), "start", yes))
        self.assertEqual(self.status(), "READY")

    def test_start_draft_ready_stopped_before_and_rejected_draft(self):
        """Start: a stopped plan gets its earlier status, a rejected plan DRAFT, a draft READY."""
        self.plan("IN_PROGRESS", PKG)
        hives.pause_plan(self.conn, "testproj", REL)
        gui_hives.run_action(self.conn, self.row(), "start", yes)
        self.assertEqual(self.status(), "IN_PROGRESS")
        self.plan("REJECTED", PKG)
        gui_hives.run_action(self.conn, self.row(), "start", yes)
        self.assertEqual(self.status(), "DRAFT")

    def test_pause_and_reject(self):
        """Pause a READY plan; reject a stopped one and a draft; a running one cannot be rejected."""
        self.plan("READY", PKG)
        gui_hives.run_action(self.conn, self.row(), "pause", yes)
        self.assertEqual(self.status(), "PAUSED")
        gui_hives.run_action(self.conn, self.row(), "reject", yes)
        self.assertEqual(self.status(), "REJECTED")
        self.plan("DRAFT", PKG)
        gui_hives.run_action(self.conn, self.row(), "reject", yes)
        self.assertEqual(self.status(), "REJECTED")
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
        row = next(r for r in gui_hives.plan_rows(self.conn) if r["plan_path"] == OTHER)
        gui_hives.run_action(self.conn, row, "start", yes)
        self.assertEqual(self.conn.execute("SELECT status FROM plans WHERE path=?", (OTHER,)).fetchone()[0], "READY")


class WindowTests(unittest.TestCase):
    """The button and the window."""

    def test_footer_has_a_hives_button(self):
        """The footer names the button and the Dashboard opens the list through open_hives."""
        self.assertTrue(callable(gui.Dashboard.open_hives))
        self.assertIn('"hives", self.open_hives', inspect.getsource(gui.Dashboard.footer))

    def test_open_hives_creates_the_window_once(self):
        """A second click brings the open window to the front."""
        win = mock.Mock(spec=gui.Dashboard)
        win.root, win.conn, win.mono = object(), object(), "x"
        win.hives_window = None
        with mock.patch.object(gui_hives, "HivesWindow") as cls:
            gui.Dashboard.open_hives(win)
            cls.assert_called_once()
            win.hives_window = cls.return_value
            cls.return_value.win.winfo_exists.return_value = True
            gui.Dashboard.open_hives(win)
            cls.assert_called_once()
            cls.return_value.win.lift.assert_called_once()

    def test_menu_items_confirm_and_call(self):
        """The right-click menu lists the actions of the row; choosing one runs it with the confirmation."""
        row = {"apiary": "A", "plan_path": "p.md", "name": "X", "status": "REJECTED", "stopped": False, "has_hive": False}
        win = mock.Mock(spec=gui_hives.HivesWindow)
        win.conn = object()
        win.on_changed = None
        win.apply.side_effect = lambda r, a: gui_hives.HivesWindow.apply(win, r, a)
        with mock.patch.object(gui_hives, "run_action", return_value=True) as run:
            items = gui_hives.HivesWindow.menu_items(win, row)
            self.assertEqual([label for label, _ in items], ["Start", "Back to draft"])
            items[1][1]()
            run.assert_called_once_with(win.conn, row, "draft", win.confirm)


if __name__ == "__main__":
    unittest.main()
