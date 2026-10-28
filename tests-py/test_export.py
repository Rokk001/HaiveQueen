"""Tests of the daily export."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import db, export  # noqa: E402


class ExportTest(unittest.TestCase):
    """Export file naming, retention and the due rule."""

    def setUp(self):
        """Create a temp database and export folder."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dbfile = root / "data" / "orchestrator.db"
        conn = db.connect(self.dbfile)
        db.add_question(conn, "p", "x.md", "I1", "Q?", "A")
        conn.close()
        self.dir = root / "exports"

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def test_export_is_a_readable_copy(self):
        """The export opens as a database with the same data."""
        res = export.run_export(datetime(2026, 1, 5, 4, 0), self.dir, self.dbfile)
        self.assertEqual(Path(res["file"]).name, "orchestrator-2026-01-05.db")
        c = sqlite3.connect(res["file"])
        self.assertEqual(c.execute("SELECT question FROM questions").fetchone()[0], "Q?")
        c.close()

    def test_keep_14_and_foreign_files(self):
        """15 exports leave 14; other files in the folder stay."""
        self.dir.mkdir(parents=True)
        (self.dir / "notes.txt").write_text("x")
        (self.dir / "orchestrator-2026-01-01.db.bak").write_text("x")
        start = datetime(2026, 1, 1, 4, 0)
        for i in range(15):
            export.run_export(start + timedelta(days=i), self.dir, self.dbfile)
        names = sorted(p.name for p in self.dir.iterdir())
        dbs = [n for n in names if export.NAME_RE.match(n)]
        self.assertEqual(len(dbs), 14)
        self.assertNotIn("orchestrator-2026-01-01.db", dbs)
        self.assertIn("notes.txt", names)
        self.assertIn("orchestrator-2026-01-01.db.bak", names)

    def test_export_due(self):
        """Due from 03:30 on, only while today's file is missing."""
        self.dir.mkdir(parents=True)
        self.assertFalse(export.export_due(datetime(2026, 1, 5, 3, 29), self.dir))
        self.assertTrue(export.export_due(datetime(2026, 1, 5, 3, 30), self.dir))
        export.run_export(datetime(2026, 1, 5, 3, 31), self.dir, self.dbfile)
        self.assertFalse(export.export_due(datetime(2026, 1, 5, 12, 0), self.dir))
        self.assertTrue(export.export_due(datetime(2026, 1, 6, 3, 30), self.dir))

    def test_cli_force(self):
        """orch export --force writes into exportDir from config.toml."""
        import subprocess
        cfg = Path(self.tmp.name) / "cfg"
        cfg.mkdir()
        (cfg / "config.toml").write_text('exportDir = "%s"\n' % str(self.dir).replace("\\", "/"), encoding="utf-8")
        env = dict(os.environ, ORCH_CONFIG_DIR=str(cfg), ORCH_DATA_DIR=str(self.dbfile.parent))
        r = subprocess.run([sys.executable, "-I", str(ROOT / "orch.py"), "export", "--force"], env=env,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(list(self.dir.glob("orchestrator-*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
