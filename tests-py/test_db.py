"""Unit tests for orchlib.db and orchlib.config using a temporary database."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchlib import config, db  # noqa: E402


class DbTest(unittest.TestCase):
    """Tests against a fresh database in a temp folder."""

    def setUp(self):
        """Create a temp folder and open a database in it."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        """Close the database and remove the temp folder."""
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_and_pragmas(self):
        """All tables exist, WAL is on, busy_timeout is 10000."""
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("projects", "plans", "packages", "agents", "events", "questions", "decisions", "queue",
                  "build_measurements", "providers_usage", "handled", "nofollow"):
            self.assertIn(t, names)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 10000)

    def test_migrate_idempotent(self):
        """Opening twice keeps data and version."""
        db.add_question(self.conn, "p", "a.md", "1", "q?", "r")
        db.migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        self.assertEqual(len(db.open_questions(self.conn)), 1)

    def test_ready_upsert(self):
        """A second ready for the same branch updates the open row instead of adding one."""
        db.add_ready(self.conn, "p", "b1", "aaa", "x.md", "n1")
        db.add_ready(self.conn, "p", "b1", "bbb", "x.md", "n2")
        q = db.queue_rows(self.conn, "p")
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["tip"], "bbb")
        self.assertEqual(q[0]["status"], "ready")

    def test_question_answer_flow(self):
        """Answering sets status, answerer, and writes a decision; double answer fails."""
        qid = db.add_question(self.conn, "p", "x.md", "I1", "Which?", "A")
        db.answer_question(self.conn, qid, "A", "owner")
        q = db.open_questions(self.conn)[0]
        self.assertEqual((q["status"], q["answered_by"], q["answer"]), ("answered", "owner", "A"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
        with self.assertRaises(KeyError):
            db.answer_question(self.conn, qid, "B", "owner")
        with self.assertRaises(ValueError):
            db.answer_question(self.conn, qid, "B", "nobody")
        self.assertEqual(db.open_questions(self.conn, only_open=True), [])

    def test_finish_agent(self):
        """finish_agent marks the agent done and logs an event; unknown id raises."""
        aid = db.add_agent(self.conn, "p", name="a1", status="running")
        db.finish_agent(self.conn, aid, "line1\nall good")
        row = db.rows(self.conn, "SELECT * FROM agents WHERE id=?", (aid,))[0]
        self.assertEqual((row["status"], row["last_line"]), ("done", "all good"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='done'").fetchone()[0], 1)
        with self.assertRaises(KeyError):
            db.finish_agent(self.conn, 999, "x")

    def test_transaction_rollback(self):
        """An error inside a transaction rolls everything back."""
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                db.add_event(self.conn, "p", "note", "x")
                raise RuntimeError("boom")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_project_status(self):
        """Packages with a done agent and an integrated queue row count as integrated."""
        for pid in ("P1", "P2"):
            self.conn.execute("INSERT INTO packages (project, plan_path, id, title) VALUES ('p','x.md',?,?)",
                              (pid, "T" + pid))
        aid = db.add_agent(self.conn, "p", name="w1", plan_path="x.md", package="P1", status="running")
        db.finish_agent(self.conn, aid, "ok")
        db.add_ready(self.conn, "p", "b", "t", "x.md", "")
        self.conn.execute("UPDATE queue SET status='integrated'")
        st = db.project_status(self.conn, "p")[0]
        self.assertEqual((st["integrated"], st["total"], st["percent"]), (1, 2, 50))
        self.assertEqual([t["status"] for t in st["tasks"]], ["integriert", "offen"])


class ConfigTest(unittest.TestCase):
    """Tests for folder lookup and TOML defaults."""

    def test_env_override(self):
        """ORCH_CONFIG_DIR and ORCH_DATA_DIR override the standard folders."""
        old = dict(os.environ)
        try:
            os.environ["ORCH_CONFIG_DIR"] = "cfgx"
            os.environ["ORCH_DATA_DIR"] = "datax"
            self.assertEqual(config.config_dir(), Path("cfgx"))
            self.assertEqual(config.db_path(), Path("datax") / "orchestrator.db")
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_defaults_and_files(self):
        """Missing files give defaults; present files override them."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ORCH_CONFIG_DIR"] = tmp
            try:
                self.assertEqual(config.load_config()["slots"], 2)
                (Path(tmp) / "config.toml").write_text("slots = 5\n", encoding="utf-8")
                self.assertEqual(config.load_config()["slots"], 5)
                self.assertEqual(config.load_config()["ramReserveMb"], 2048)
            finally:
                del os.environ["ORCH_CONFIG_DIR"]
            ws = Path(tmp) / "myproj"
            (ws / ".orchestrator").mkdir(parents=True)
            self.assertEqual(config.load_project(ws)["name"], "myproj")
            (ws / ".orchestrator" / "project.toml").write_text('colour = "red"\n[views]\nqueue = "q.md"\n',
                                                               encoding="utf-8")
            pr = config.load_project(ws)
            self.assertEqual((pr["colour"], pr["views"]["queue"], pr["views"]["questions"]), ("red", "q.md", ""))


class OpenNewDatabaseTest(unittest.TestCase):
    """Many processes opening a brand-new database at the same moment must all succeed."""

    def test_concurrent_first_open(self):
        """Eight processes connect to a database that does not exist yet, in 60 rounds: none fails with 'database is locked'."""
        root = str(Path(__file__).resolve().parent.parent)
        code = "import sys; sys.path.insert(0, sys.argv[1]); from orchlib import db; db.connect(sys.argv[2]).close()"
        failed = []
        for rnd in range(60):
            with tempfile.TemporaryDirectory() as tmp:
                procs = [subprocess.Popen([sys.executable, "-I", "-c", code, root, str(Path(tmp) / "x.db")], stderr=subprocess.PIPE, text=True) for _ in range(8)]
                for p in procs:
                    err = p.communicate()[1]
                    if p.returncode:
                        failed.append(err.strip().splitlines()[-1] if err.strip() else "exit %s" % p.returncode)
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
