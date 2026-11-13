"""K5: a new Project starts without Build and Deploy command, on every way to register it."""
import gc
import argparse
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from haivequeen import config, db, settings, init, scheduler


class ApiaryCase(unittest.TestCase):
    """A temporary settings folder, database and project folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "ws"
        self.workspace.mkdir()
        patcher = mock.patch.object(config, "config_dir", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_db(self, name="new.db"):
        """A migrated database that closes with the test."""
        conn = db.connect(self.base / name)
        self.addCleanup(conn.close)
        return conn

    def deploy(self, conn, name):
        """Whether the Project has a Deploy command, as the Orchestrator reads it."""
        env = next(e for e in scheduler.load_projects(conn, {}) if e.name == name)
        return bool(env.settings.get("deployCommand"))

    def old_database(self, *names):
        """A database as schema version 13 left it, with the Projects `names` on the project folder."""
        path = self.base / "old.db"
        conn = db.connect(path)
        for name in names:
            db.upsert_project(conn, name, str(self.workspace), "white", 0)
        conn.execute("ALTER TABLE projects DROP COLUMN legacy_deploy")
        conn.execute("PRAGMA user_version = 13")
        conn.close()
        return path


class NewApiaryTests(ApiaryCase):
    """A new Project has no compile check, Build or Deploy command, however it was registered."""

    def test_fresh_database_marks_nothing(self):
        conn = self.open_db()
        init.register_project(conn, self.workspace)
        self.assertEqual(conn.execute("SELECT legacy_deploy FROM projects").fetchone()[0], 0)

    def test_registered_without_a_project_file(self):
        conn = self.open_db()
        init.register_project(conn, self.workspace)
        self.assertFalse((self.workspace / ".haivequeen" / "project.toml").exists())
        env = next(iter(scheduler.load_projects(conn, {})))
        self.assertEqual((env.settings["compileCheck"], env.settings["buildCommand"], env.settings["deployCommand"]), ("", "", ""))

    def test_project_of_the_settings_page_is_registered_as_new(self):
        conn = self.open_db()
        added = init.register_listed(conn, [{"name": "paged", "workspace": str(self.workspace)}])
        self.assertEqual(added, ["paged"])
        self.assertFalse(self.deploy(conn, "paged"))

    def test_settings_page_skips_what_cannot_run_yet(self):
        conn = self.open_db()
        entries = [{"name": "new-project", "workspace": ""}, {"name": "gone", "workspace": str(self.base / "missing")}]
        self.assertEqual(init.register_listed(conn, entries), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 0)

    def test_settings_page_changes_no_registered_row(self):
        conn = self.open_db()
        db.upsert_project(conn, "known", str(self.workspace), "cyan", 3)
        other = self.base / "elsewhere"
        other.mkdir()
        self.assertEqual(init.register_listed(conn, [{"name": "known", "workspace": str(other)}, {"name": "twin", "workspace": str(self.workspace)}]), [])
        self.assertEqual(tuple(conn.execute("SELECT name, workspace, priority FROM projects").fetchone()), ("known", str(self.workspace), 3))
