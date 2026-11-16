"""The Queen start embeds what is open and the owner's decisions of her plan; history and technical notes stay retrievable."""
import tempfile
import unittest
from pathlib import Path

import bzztest  # noqa: F401  (redirects the real folders)
from haivequeen import bees, db, profile, runtime

PLAN = "plans/X-PLAN.md"
STATE = """# X state

## Requirements

- X-1: one
- X-2: two

## Completeness

- X-1: done | commit abc with a long evidence text that the start does not need
- X-2: partial | still failing at the switch

Ada-1 handoff: long history paragraph that belongs to the file, not to the start.

## Handoff Ada-1

old handoff text

## Handoff Bea-2

newest handoff text
"""


class QueenStartSlimTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "plans").mkdir()
        (self.root / PLAN).write_text("# X\n\n## Requirements\n\n- X-1: one\n", encoding="utf-8")
        (self.root / "plans" / "X-STATE.md").write_text(STATE, encoding="utf-8")
        self.conn = db.connect(self.root / "o.db")
        self.addCleanup(self.conn.close)

    def load(self, predecessor=None):
        return profile.queen_load(self.conn, "p", self.root, PLAN, predecessor=predecessor)

    def test_state_keeps_open_entries_and_latest_handoff_only(self):
        text = self.load()
        self.assertIn("X-2: partial | still failing", text)
        self.assertIn("Done (evidence in the state file): X-1", text)
        self.assertNotIn("long evidence text", text)
        self.assertNotIn("long history paragraph", text)
        self.assertNotIn("old handoff text", text)
        self.assertIn("newest handoff text", text)
        self.assertIn("1 older handoff sections, 1 handoff notes", text)

    def test_predecessor_handoff_replaces_the_other_handoff_sections(self):
        text = self.load(predecessor="Bea-2")
        self.assertIn("# Handoff of your predecessor Bea-2", text)
        self.assertIn("newest handoff text", text)
        self.assertNotIn("old handoff text", text)

    def test_owner_decisions_of_the_plan_stay_complete_and_long_notes_are_summaries(self):
        owner = runtime.add_decision(self.conn, "p", "owner", "Owner rule. " + "x" * 900, plan=PLAN, item="P1")
        note = runtime.add_decision(self.conn, "p", "coordinator", "Technical answer first sentence. " + "y" * 900, plan=PLAN, item="P2")
        short = runtime.add_decision(self.conn, "p", "coordinator", "Short technical answer.", plan=PLAN, item="P3")
        project = runtime.add_decision(self.conn, "p", "owner", "Project-wide rule. Details " + "z" * 300)
        text = self.load()
        self.assertIn("- #%d (plan %s): Owner rule. %s" % (owner, PLAN, "x" * 900), text)
        self.assertIn("- #%d (plan %s) [summary, item P2]: Technical answer first sentence." % (note, PLAN), text)
        self.assertNotIn("y" * 900, text)
        self.assertIn("- #%d (plan %s): Short technical answer." % (short, PLAN), text)
        self.assertIn("- #%d [summary]: Project-wide rule." % project, text)
        self.assertIn("bzz decisions list", text)

    def test_newer_note_for_the_same_item_supersedes_older_notes_but_never_owner_decisions(self):
        owner = runtime.add_decision(self.conn, "p", "owner", "Owner choice", plan=PLAN, item="P1")
        first = runtime.add_decision(self.conn, "p", "coordinator", "Provider returned no valid diagnosis.", plan=PLAN, item="P1")
        second = runtime.add_decision(self.conn, "p", "coordinator", "Provider returned no valid diagnosis.", plan=PLAN, item="P1")
        other = runtime.add_decision(self.conn, "p", "coordinator", "Other package note", plan=PLAN, item="P2")
        status = {d["id"]: d["status"] for d in runtime.list_decisions(self.conn, "p")}
        self.assertEqual((status[owner], status[first], status[second], status[other]), ("active", "superseded", "active", "active"))

    def test_finished_package_ends_its_notes(self):
        note = runtime.add_decision(self.conn, "p", "coordinator", "Continue the package", plan=PLAN, item="P1")
        owner = runtime.add_decision(self.conn, "p", "owner", "Owner choice", plan=PLAN, item="P1")
        db.retire_notes(self.conn, "p", PLAN, "P1")
        status = {d["id"]: d["status"] for d in runtime.list_decisions(self.conn, "p")}
        self.assertEqual((status[note], status[owner]), ("superseded", "active"))

    def test_existing_database_keeps_the_newest_note_per_item_and_none_for_done_packages_once(self):
        path = self.root / "old.db"
        conn = db.connect(path)
        bees.ensure_attempts(conn)
        ids = [conn.execute("INSERT INTO decisions (project, ts, source, text, plan_path, item) VALUES ('p', ?, ?, ?, ?, ?)",
                            (db.now(), source, text, PLAN, item)).lastrowid
               for source, text, item in [("coordinator", "a", "P1"), ("coordinator", "b", "P1"), ("owner", "c", "P1"),
                                          ("auto", "d", "P2")]]
        conn.execute("INSERT INTO package_progress (project, plan_path, package, state, agent, updated_at) VALUES ('p', ?, 'P2', 'done', 'x', ?)", (PLAN, db.now()))
        conn.execute("DELETE FROM kv WHERE key='decision-notes-retired'")   # a database from before this rule
        conn.commit()
        conn.close()
        conn = db.connect(path)
        try:
            status = dict(conn.execute("SELECT id, status FROM decisions").fetchall())
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 17)   # older versions keep opening it
        finally:
            conn.close()
        self.assertEqual([status[i] for i in ids], ["superseded", "active", "active", "superseded"])


if __name__ == "__main__":
    unittest.main()
