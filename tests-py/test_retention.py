"""Configurable cleanup of expired HaiveQueen history and artifacts."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from haivequeen import config, db, retention


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        self.runs = self.data / "runs"
        self.conn = db.connect(self.base / "state.db")
        self.addCleanup(self.conn.close)
        self.now = datetime(2026, 11, 5, 12)
        self.machine = {"retention": {"logsDays": 30, "runsDays": 30, "databaseDays": 90, "inboxDays": 30}}

    def old(self):
        return (self.now - timedelta(days=91)).isoformat(timespec="seconds")

    def recent(self):
        return (self.now - timedelta(days=2)).isoformat(timespec="seconds")

    def touch(self, path, age_days):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
        stamp = (self.now - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_old_and_recent_logs_and_crash_files(self):
        for folder, name in ((self.data / "logs" / "p", "ROUNDS.log"),
                             (self.data / "logs" / "p", "ORCH-EVENTS.log"),
                             (self.runs, "beekeeper.log")):
            self.touch(folder / name, 31)
            self.touch(folder / ("recent-" + name), 2)
        self.touch(self.data / "crash-old.txt", 31)
        self.touch(self.data / "crash-recent.txt", 2)
        retention.cleanup(self.conn, self.machine, now=self.now, data_dir=self.data, runs_dir=self.runs)
        self.assertFalse((self.data / "logs/p/ROUNDS.log").exists())
        self.assertFalse((self.data / "logs/p/ORCH-EVENTS.log").exists())
        self.assertFalse((self.runs / "beekeeper.log").exists())
        self.assertFalse((self.data / "crash-old.txt").exists())
        self.assertTrue((self.data / "logs/p/recent-ROUNDS.log").exists())
        self.assertTrue((self.runs / "recent-beekeeper.log").exists())
        self.assertTrue((self.data / "crash-recent.txt").exists())

    def test_expired_runs_removed_but_active_plan_runs_preserved(self):
        self.conn.execute("INSERT INTO projects(name) VALUES ('p')")
        self.conn.execute("INSERT INTO plans(project,path,title,status,updated_at) VALUES ('p','done.md','Done','DONE',?)", (self.old(),))
        self.conn.execute("INSERT INTO plans(project,path,title,status,updated_at) VALUES ('p','open.md','Open','RUNNING',?)", (self.old(),))
        for name, plan in (("old-done", "done.md"), ("old-open", "open.md")):
            self.conn.execute("INSERT INTO agents(project,name,plan_path,status,ended_at) VALUES ('p',?,?, 'done',?)", (name, plan, self.old()))
            (self.runs / "p" / name).mkdir(parents=True)
            (self.runs / "p" / name / "final.txt").write_text("evidence")
        self.conn.execute("INSERT INTO agents(project,name,plan_path,status,ended_at) VALUES ('p','recent-done','done.md','done',?)", (self.recent(),))
        (self.runs / "p" / "recent-done").mkdir(parents=True)
        result = retention.cleanup(self.conn, self.machine, now=self.now, data_dir=self.data, runs_dir=self.runs)
        self.assertFalse((self.runs / "p/old-done").exists())
        self.assertTrue((self.runs / "p/old-open").exists())
        self.assertTrue((self.runs / "p/recent-done").exists())
        self.assertEqual(result["runs"], 1)

    def test_expired_db_rows_deleted_but_questions_decisions_and_benchmarks_survive(self):
        self.conn.execute("INSERT INTO events(ts,project,kind,text) VALUES (?, 'p', 'note', 'old')", (self.old(),))
        self.conn.execute("INSERT INTO events(ts,project,kind,text) VALUES (?, 'p', 'note', 'new')", (self.recent(),))
        self.conn.execute("INSERT INTO providers_usage(ts,project,kind) VALUES (?, 'p', 'brain')", (self.old(),))
        self.conn.execute("INSERT INTO providers_usage(ts,project,kind) VALUES (?, 'p', 'brain')", (self.recent(),))
        self.conn.execute("INSERT INTO start_contexts(project,kind,provider,started_at,tokens) VALUES ('p','queen','c',?,1)", (self.old(),))
        self.conn.execute("INSERT INTO start_contexts(project,kind,provider,started_at,tokens) VALUES ('p','queen','c',?,1)", (self.recent(),))
        self.conn.execute("INSERT INTO start_benchmarks(provider,project,mode,tokens,measured_at) VALUES ('c','p','hq',1,?)", (self.old(),))
        self.conn.execute("INSERT INTO questions(project,question,created_at) VALUES ('p','keep',?)", (self.old(),))
        self.conn.execute("INSERT INTO decisions(project,ts,source,text) VALUES ('p',?,'owner','keep')", (self.old(),))
        retention.cleanup(self.conn, self.machine, now=self.now, data_dir=self.data, runs_dir=self.runs)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT text FROM events")], ["new"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM providers_usage").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM start_contexts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM start_benchmarks").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)

    def test_done_and_failed_inbox_files_expire_independently(self):
        inbox = self.base / "workspace/.haivequeen/inbox"
        for state in ("done", "failed"):
            self.touch(inbox / state / "old.toml", 31)
            self.touch(inbox / state / "recent.toml", 2)
        retention.cleanup(self.conn, self.machine, now=self.now, data_dir=self.data, runs_dir=self.runs,
                          projects=[{"name": "p", "workspace": str(self.base / "workspace")}])
        for state in ("done", "failed"):
            self.assertFalse((inbox / state / "old.toml").exists())
            self.assertTrue((inbox / state / "recent.toml").exists())

    def test_retention_periods_are_configurable_and_zero_disables_each_category(self):
        cfg = {"retention": {"logsDays": 0, "runsDays": 0, "databaseDays": 0, "inboxDays": 0}}
        log = self.touch(self.data / "logs/p/ROUNDS.log", 400)
        self.conn.execute("INSERT INTO events(ts,project,kind,text) VALUES (?, 'p', 'note', 'keep')", (self.old(),))
        retention.cleanup(self.conn, cfg, now=self.now, data_dir=self.data, runs_dir=self.runs)
        self.assertTrue(log.exists())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_hive_migration_entry_points_and_marker_are_removed(self):
        from haivequeen import hives, runstate
        self.assertFalse(hasattr(hives, "migration_targets"))
        self.assertFalse(hasattr(hives, "migrate_hives"))
        self.assertFalse(hasattr(runstate, "resume") and "hives-migration" in runstate.resume.__code__.co_consts)
        self.assertNotIn("hives-migration", db.SCHEMA_FILE.read_text(encoding="utf-8"))

    def test_schema_upgrade_clears_obsolete_migration_markers(self):
        self.conn.execute("INSERT INTO kv(project,key,value) VALUES('*','hives-migration','armed')")
        self.conn.execute("INSERT INTO kv(project,key,value) VALUES('hq','hives-migrated','done')")
        self.conn.execute("PRAGMA user_version=12")
        db.migrate(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM kv WHERE key IN ('hives-migration','hives-migrated')").fetchone()[0], 0)

    def test_default_retention_settings_are_documented_and_loaded(self):
        self.assertEqual(config.DEFAULT_CONFIG["retention"], self.machine["retention"])


if __name__ == "__main__":
    unittest.main()
