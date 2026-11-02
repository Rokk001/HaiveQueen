"""Tests of the Queens-and-Bees table and the keys of the graphical dashboard: the Queen row with her Bees below, Tab and the arrow keys, the Queen menu,
X on a Queen or a waiting Bee, the log clear and the footer buttons."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import bees, db, hives, runtime  # noqa: E402

PLAN = "docs/plans/AI-LEVELS-PLAN.md"


class RowsTest(unittest.TestCase):
    """A window on a temp database with one Hive, its running Queen Ava and her running Bee Sophia."""

    # Build the window; skip without a display.
    def setUp(self):
        """A temp data folder with one Hive, its Queen and a Bee, and an open window on it."""
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0.0)   # fully transparent: the test window must not flash on the user's screen
        except tk.TclError:
            self.skipTest("no display")

        class QuietToplevel(tk.Toplevel):
            """A Toplevel that is fully transparent from the start."""

            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.attributes("-alpha", 0.0)

        quiet = mock.patch("tkinter.Toplevel", QuietToplevel)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(os.environ, {"BZZ_DATA_DIR": self.tmp.name, "BZZ_LOG_DIR": str(Path(self.tmp.name) / "logs")})
        self.patch.start()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        hives.create(self.conn, "ODP", PLAN, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": PLAN, "phase": "queen", "provider": "claude", "status": "running",
                                                              "startedAt": db.now(), "person": "Ava"})
        runtime.upsert_agent(self.conn, "ODP", "ODP__bee", {"plan": PLAN, "phase": "bee", "provider": "claude", "status": "running",
                                                            "startedAt": db.now(), "person": "Sophia", "package": "P1"})
        from haivequeen import gui
        gui.REFRESH_MS = 10 ** 9
        self.gui = gui
        self.root.geometry("1672x901")
        self.win = gui.Dashboard(self.root, self.conn)
        self.root.update()

    # Close the window and remove the temp folder.
    def tearDown(self):
        """Destroy the window, close the database and remove the temp folder."""
        self.root.destroy()
        self.conn.close()
        self.patch.stop()
        self.tmp.cleanup()

    # Press a key of the window.
    def key(self, keysym):
        """Send the key `keysym` to the window's key handler."""
        self.win.on_key(mock.Mock(keysym=keysym))

    # The Queen heads her Bees.
    def test_queen_row_leads_and_her_bees_follow(self):
        """The Bees table lists the running Queen first, then her Bee."""
        rows = self.win.snap["bees"]
        self.assertEqual([(r["kind"], r["name"]) for r in rows], [("queen", "Ava"), ("bee", "Sophia")])

    # A Bee that runs as a subagent of the Queen has no agent row but shows up under her.
    def test_embedded_bee_shows_under_its_queen_until_it_reports(self):
        """An embedded Bee is listed below its Queen with its name and runs until the Queen reports its result."""
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P2", "Mona", "heavy", "claude", "claude-opus-5-5", "medium")
        self.win.refresh()
        self.assertEqual([(r["kind"], r["name"], r["status"]) for r in self.win.snap["bees"]],
                         [("queen", "Ava", "running"), ("bee", "Sophia", "running"), ("bee", "Mona", "running")])
        bees.close_embedded(self.conn, "ODP", PLAN, "P2", "succeeded")
        self.win.refresh()
        self.assertEqual([r["name"] for r in self.win.snap["bees"]], ["Ava", "Sophia"])

    # The footer only offers what has a purpose.
    def test_footer_has_five_buttons(self):
        """The state button, the apiary filter, questions, config and help are the only clickable parts of the footer."""
        footer = [h for h in self.win.hits if h[1] >= 860 * self.win.scale]
        self.assertEqual(len(footer), 5)

    # Tab picks the table, the arrow keys move in it.
    def test_tab_and_arrow_keys_work_on_the_active_table(self):
        """Tab makes the Queens-and-Bees table the active one; Up and Down then move its selection and Tab goes back to the Hives."""
        self.assertEqual(self.win.table, "hives")
        self.key("Tab")
        self.assertEqual(self.win.table, "bees")
        self.key("Down")
        self.assertEqual(self.win.sel["bees"], 1)
        self.key("Up")
        self.assertEqual(self.win.sel["bees"], 0)
        self.key("Tab")
        self.assertEqual(self.win.table, "hives")

    # The Queen row belongs to its Hive, a Bee row to none.
    def test_selected_hive(self):
        """For a Hive row and for the Queen row the Hive is found; for a Bee row there is none."""
        self.assertEqual(self.win.selected_hive()["name"], "AI-LEVELS")
        self.key("Tab")
        self.assertEqual(self.win.selected_hive()["name"], "AI-LEVELS")
        self.key("Down")
        self.assertIsNone(self.win.selected_hive())

    # The Queen menu opens on a Hive row and a Queen row, not on a Bee row.
    def test_queen_menu_opens_for_hive_and_queen_but_not_for_a_bee(self):
        """The menu lists restart, end, a separator and pause for the Hive; on a Bee row nothing opens."""
        with mock.patch("haivequeen.gui_dialog.popup_menu") as popup:
            self.win.hive_menu()
            self.key("Tab")
            self.win.hive_menu()
            self.assertEqual(popup.call_count, 2)
            items = popup.call_args[0][1]
            self.assertEqual([i if i is None else i[0].split(" ")[0] for i in items], ["Restart", "End", None, "Pause"])
            self.key("Down")
            self.win.hive_menu()
            self.assertEqual(popup.call_count, 2)

    # X stops Bees only, and only started ones.
    def test_x_on_a_queen_or_a_waiting_bee_only_informs(self):
        """X on the Queen row and on a Bee that has not started shows a hint and kills nothing."""
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments VALUES ('ODP',?,'P4','heavy','codex')", (PLAN,))
        self.conn.commit()
        self.win.refresh()
        self.assertEqual([r["status"] for r in self.win.snap["bees"]], ["running", "running", "waiting"])
        self.key("Tab")
        with mock.patch.object(self.win, "inform") as inform, mock.patch.object(self.gui.osplat, "kill_process_tree") as kill:
            self.key("x")   # the Queen row
            self.key("Down")
            self.key("Down")
            self.key("x")   # the waiting Bee
            self.assertEqual(inform.call_count, 2)
            kill.assert_not_called()

    # Clear hides what exists, new lines show up even when the window is full.
    def test_clear_log_by_event_id(self):
        """After clearing, the old lines stay hidden and a line with a newer id shows although the window holds a fixed number of lines."""
        self.win.snap["log"] = [("12:00", "SYSTEM", "a"), ("12:01", "SYSTEM", "b")]
        self.win.snap["log_ids"] = [1, 2]
        self.win.clear_log()
        self.assertEqual(self.win.visible_log(), [])
        self.win.snap["log"] = [("12:01", "SYSTEM", "b"), ("12:02", "SYSTEM", "c")]   # the oldest line left the window, a new one came
        self.win.snap["log_ids"] = [2, 3]
        self.assertEqual(self.win.visible_log(), [("12:02", "SYSTEM", "c")])


if __name__ == "__main__":
    unittest.main()
