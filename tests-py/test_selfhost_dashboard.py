"""Tests of the self-hosting additions: project HaiveQueen in its colour, the last self-update line and the K2 views (invented data, temp database)."""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import dashboard, db, views  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, 0)
SYS = {"cores": 12, "free_mb": 8192, "total_mb": 16384, "host": "h1", "compiler_count": None}
STUB_TABLE = ("CREATE TABLE self_updates (id INTEGER PRIMARY KEY, started TEXT, ended TEXT, old_commit TEXT, "
              "new_commit TEXT, result TEXT, note TEXT)")


class SelfhostDashboardTest(unittest.TestCase):
    """Dashboard and views with the project HaiveQueen; the table self_updates is a stub of the one the self-update step creates."""

    def setUp(self):
        """Temp database with the projects ODP (cyan) and HaiveQueen (magenta); no self_updates table yet."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        db.upsert_project(self.conn, "HaiveQueen", None, "magenta", 0)

    def tearDown(self):
        """Close and remove the database."""
        self.conn.close()
        self.tmp.cleanup()

    def render(self, color=False):
        """Render the whole dashboard."""
        return dashboard.render(self.conn, width=100, height=60, now=NOW, cfg={"buildJobs": 1}, sysinfo=dict(SYS), color=color)

    def add_update(self, result, old="1111111aaaa", new="2222222bbbb", ended="2026-10-29T11:02:00"):
        """Create the stub table if needed and insert one self-update row."""
        self.conn.execute("CREATE TABLE IF NOT EXISTS self_updates (id INTEGER PRIMARY KEY, started TEXT, ended TEXT, "
                          "old_commit TEXT, new_commit TEXT, result TEXT, note TEXT)")
        self.conn.execute("INSERT INTO self_updates (started, ended, old_commit, new_commit, result, note) VALUES (?,?,?,?,?,?)",
                          ("2026-10-29T10:58:00", ended, old, new, result, ""))
        self.conn.commit()

    def test_missing_table_is_tolerated(self):
        """Without the table self_updates the dashboard renders and shows no self-update line."""
        self.assertIsNone(db.last_self_update(self.conn))
        self.assertNotIn("self-update", self.render())

    def test_empty_table_shows_no_line(self):
        """An empty table gives no line either."""
        self.conn.execute(STUB_TABLE)
        self.assertNotIn("self-update", self.render())

    def test_last_update_ok(self):
        """The newest row is shown with result, end time and short new commit; older rows are not."""
        self.add_update("failed", new="9999999zzzz")
        self.add_update("ok")
        t = self.render()
        self.assertIn("self-update ok  29.10. 11:02  2222222", t)
        self.assertNotIn("9999999", t)

    def test_rollback_names_old_commit(self):
        """A rollback shows the commit it went back to."""
        self.add_update("rollback")
        self.assertIn("self-update rollback  29.10. 11:02  2222222 (back to 1111111)", self.render())

    def test_result_colours(self):
        """ok is green, rollback yellow, failed red (ANSI codes of the dashboard colours)."""
        for result, code in (("ok", "92"), ("rollback", "93"), ("failed", "91")):
            self.conn.execute("DROP TABLE IF EXISTS self_updates")
            self.add_update(result)
            self.assertIn("\x1b[%sm%s" % (code, result), self.render(color=True))

    def test_project_visible_with_colour(self):
        """HaiveQueen shows in the plans section in its own colour (magenta = 95), ODP in cyan (96)."""
        t = self.render(color=True)
        self.assertIn("\x1b[95mHaiveQueen", t)
        self.assertIn("\x1b[96mODP", t)

    def test_k2_views_are_written(self):
        """A workspace laid out as in the self-hosting plan gets the three views at the configured paths."""
        ws = self.root / "hq"
        (ws / ".haivequeen").mkdir(parents=True)
        (ws / ".haivequeen" / "project.toml").write_text(
            'name = "HaiveQueen"\n[views]\nqueue = "views/QUEUE.md"\nquestions = "views/OPEN-QUESTIONS.md"\n'
            'decisions = "views/DECISIONS.md"\n', encoding="utf-8")
        db.upsert_project(self.conn, "HaiveQueen", str(ws), "magenta", 0)
        written = views.write_views(self.conn, "HaiveQueen")
        self.assertEqual(sorted(written), ["decisions", "questions", "queue"])
        for name in ("QUEUE.md", "OPEN-QUESTIONS.md", "DECISIONS.md"):
            self.assertTrue((ws / "views" / name).read_text(encoding="utf-8").startswith(views.HEADER))


if __name__ == "__main__":
    unittest.main()
