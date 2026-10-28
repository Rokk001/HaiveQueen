"""Tests of the queue status 'withdrawn': v1 import, Markdown view, database migration and the loop."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchlib import db, legacy, runtime, views  # noqa: E402
from orchlib.runtime import CmdError  # noqa: E402
from orchtest import RoundCase  # noqa: E402

QUEUE = """# Integration state

## Queue (open)

| Branch | Tip | Plan | Status | Order | Date |
|---|---|---|---|---|---|
| feature/alpha | a1a1a1a1 | plans/ALPHA.md | ready | 1 | 2026-01-05 |
| feature/beta | b2b2b2b2 | plans/BETA.md | Zurückgezogen 2026-01-06, replaced by gamma | 2 | 2026-01-06 |
| feature/gamma | c3c3c3c3 | plans/GAMMA.md | zurueckgezogen | 3 | 2026-01-06 |
| feature/delta | d4d4d4d4 | plans/DELTA.md | superseded (in feature/gamma) | 4 | 2026-01-07 |
| feature/eps | e5e5e5e5 | plans/EPS.md | Ersetzt durch feature/gamma | 5 | 2026-01-07 |
| feature/zeta | f6f6f6f6 | plans/ZETA.md | VERWORFEN (owner) | 6 | 2026-01-08 |
| feature/eta | 07070707 | plans/ETA.md | zurückgestellt (later) | 7 | 2026-01-08 |
| feature/theta | 18181818 | plans/THETA.md | zurueckgestellt | 8 | 2026-01-09 |

## Integrated (history)

| Run | integration/all after | Branches | Date |
|---|---|---|---|
| until run 1 | 19e910414 | chain-one, chain-two | 2026-01-01 |
| run 2 | 29e910414 | feature/old | 2026-01-02 |

| Other | Table |
|---|---|
| without | heading |
"""


class QueueStatusWordsTest(unittest.TestCase):
    """The v1 status words that mean 'withdrawn' map to the new status."""

    def test_all_word_forms(self):
        """Every spelling (umlaut or not, any case, with a trailing date or bracket) maps to withdrawn; known words keep their status."""
        for cell in ("zurückgezogen", "Zurückgezogen 2026-01-06, x", "zurueckgezogen", "superseded (in x)", "ersetzt durch y",
                     "Ersetzt(x)", "verworfen (owner)", "VERWORFEN", "zurückgestellt (later)", "zurueckgestellt", "ZURÜCKGESTELLT"):
            self.assertEqual(legacy.queue_status(cell), "withdrawn", cell)
        self.assertEqual(legacy.queue_status("ready"), "ready")
        self.assertEqual(legacy.queue_status("blockiert (x)"), "blocked")
        self.assertEqual(legacy.queue_status("integriert (run 3)"), "integrated")
        self.assertIsNone(legacy.queue_status("supersededx"))

    def test_runtime_accepts_status(self):
        """The runtime lists withdrawn as a queue status and a queue upsert with it is stored."""
        self.assertIn("withdrawn", runtime.QUEUE_STATUSES)
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "o.db")
            try:
                row = runtime.queue_upsert(conn, "p", "b", "t1", "x.md", "withdrawn", "ersetzt durch z")
                self.assertEqual(row["status"], "withdrawn")
                with self.assertRaises(CmdError):
                    runtime.queue_upsert(conn, "p", "b", "t1", "x.md", "nonsense", "")
            finally:
                conn.close()


class QueueImportTest(unittest.TestCase):
    """Import of a v1 queue table: withdrawn rows keep the original status text as note; other tables are skipped."""

    def setUp(self):
        """Write the invented queue file and a temp data folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "queue.md").write_text(QUEUE, encoding="utf-8")
        self.env = dict(os.environ, ORCH_CONFIG_DIR=str(self.base / "cfg"), ORCH_DATA_DIR=str(self.base / "data"),
                        ORCH_LOG_DIR=str(self.base / "logs"))

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def test_parse_skips_other_tables(self):
        """Only the rows below the 'Branch' header are read."""
        rows = legacy.parse_queue(QUEUE)
        self.assertEqual([r["branch"] for r in rows],
                         ["feature/alpha", "feature/beta", "feature/gamma", "feature/delta", "feature/eps", "feature/zeta",
                          "feature/eta", "feature/theta"])

    def test_heading_ends_the_table(self):
        """A heading ends the queue table even when a later table has no header of its own."""
        text = "| Branch | Tip | Plan | Status |\n|---|---|---|---|\n| b1 | t1 | p | ready |\n\n## Next\n| b2 | t2 | p | ready |\n"
        self.assertEqual([r["branch"] for r in legacy.parse_queue(text)], ["b1"])

    def test_import_status_note_and_no_errors(self):
        """The import stores withdrawn with the original status text as note and counts no error for the history table."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "orch.py"), "import-legacy", "--project", "demo",
                            "--queue", str(self.base / "queue.md")], env=self.env, capture_output=True, text=True, encoding="utf-8")
        out = json.loads(r.stdout)
        self.assertEqual(out["sources"]["queue"]["errors"], 0)
        self.assertEqual(out["sources"]["queue"]["imported"], 8)
        conn = sqlite3.connect(str(self.base / "data" / "orchestrator.db"))
        conn.row_factory = sqlite3.Row
        try:
            st = {x["branch"]: dict(x) for x in conn.execute("SELECT * FROM queue")}
        finally:
            conn.close()
        self.assertEqual(st["feature/alpha"]["status"], "ready")
        for name in ("beta", "gamma", "delta", "eps", "zeta", "eta", "theta"):
            self.assertEqual(st["feature/" + name]["status"], "withdrawn", name)
        self.assertTrue(st["feature/beta"]["note"].startswith("Zurückgezogen 2026-01-06, replaced by gamma"))
        self.assertTrue(st["feature/zeta"]["note"].startswith("VERWORFEN (owner)"))


class QueueViewTest(unittest.TestCase):
    """The queue view shows a withdrawn row with the original status word of its note."""

    def test_view_shows_original_word(self):
        """A withdrawn row reads 'withdrawn (<word>)'; other rows show their plain status."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "o.db")
            try:
                runtime.queue_upsert(conn, "demo", "feature/beta", "b2", "p.md", "withdrawn", "ersetzt durch feature/gamma | 2")
                runtime.queue_upsert(conn, "demo", "feature/alpha", "a1", "p.md", "ready", "")
                runtime.queue_upsert(conn, "demo", "feature/bare", "c3", "p.md", "withdrawn", "")
                text = views.render_queue(conn, "demo")
            finally:
                conn.close()
        self.assertIn("| feature/beta | b2 | withdrawn (ersetzt) |", text)
        self.assertIn("| feature/alpha | a1 | ready |", text)
        self.assertIn("| feature/bare | c3 | withdrawn |", text)


class QueueMigrationTest(unittest.TestCase):
    """A database created before the status existed is migrated without losing rows."""

    OLD_QUEUE = ("CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, branch TEXT NOT NULL, tip TEXT, "
                 "plan_path TEXT, status TEXT NOT NULL CHECK (status IN ('ready','in_run','integrated','blocked')), note TEXT, "
                 "run TEXT, updated_at TEXT);"
                 "CREATE INDEX idx_queue_project ON queue (project, status);"
                 "INSERT INTO queue (id, project, branch, tip, plan_path, status, note, run, updated_at) "
                 "VALUES (7, 'p', 'b1', 't1', 'x.md', 'blocked', 'n1', 'r1', '2026-01-01T00:00:00'), "
                 "(9, 'p', 'b2', 't2', NULL, 'ready', NULL, NULL, NULL);"
                 "PRAGMA user_version = 3;")

    def test_old_database_is_migrated(self):
        """Rows and ids survive, the new status is accepted, the index exists and the version is current."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "o.db"
            raw = sqlite3.connect(path)
            raw.executescript(self.OLD_QUEUE)
            raw.commit()
            raw.close()
            conn = db.connect(path)
            try:
                rows = db.queue_rows(conn, "p")
                self.assertEqual([(r["id"], r["branch"], r["tip"], r["status"], r["note"], r["run"], r["updated_at"]) for r in rows],
                                 [(7, "b1", "t1", "blocked", "n1", "r1", "2026-01-01T00:00:00"),
                                  (9, "b2", "t2", "ready", None, None, None)])
                conn.execute("INSERT INTO queue (project, branch, status) VALUES ('p', 'b3', 'withdrawn')")
                self.assertEqual(conn.execute("SELECT id FROM queue WHERE branch='b3'").fetchone()[0], 10)
                names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
                self.assertIn("idx_queue_project", names)
                self.assertNotIn("queue_old", names)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
            finally:
                conn.close()
            conn = db.connect(path)   # a second open changes nothing
            try:
                self.assertEqual(len(db.queue_rows(conn, "p")), 3)
            finally:
                conn.close()


class LoopIgnoresWithdrawnTest(RoundCase):
    """A withdrawn row is never ready, blocked or in progress for the loop and is never repaired."""

    def test_no_fix_agent_and_not_open(self):
        """A round starts nothing for withdrawn rows (also one without a plan) and the loop's open-row filter does not match them."""
        import re
        from orchlib import routine
        self.plan("DONE", [{"id": "P1", "title": "first"}])
        runtime.queue_upsert(self.conn, "testproj", "chore/gone", "feed1234abcd", "keiner", "withdrawn", "zurückgezogen: replaced")
        runtime.queue_upsert(self.conn, "testproj", "chore/gone-too", "bead1234abcd", "docs/plans/dummy/DUMMY-PLAN.md", "withdrawn", "verworfen")
        self.round()
        self.assertEqual(runtime.list_agents(self.conn, "testproj"), [])
        for r in routine.queue_rows(self.env()).values():
            self.assertEqual(r["status"], "zurückgezogen")
            self.assertIsNone(re.match(r"^(ready|in Lauf|blockiert)", r["status"]))
        self.assertEqual(routine.state_done_packages(self.env(), "docs/plans/dummy/DUMMY-PLAN.md", ["P1"],
                                                     list(routine.queue_rows(self.env()).values())), {})


if __name__ == "__main__":
    unittest.main()
