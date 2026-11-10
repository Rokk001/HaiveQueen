"""K5: a Project from before the integration switches keeps deploy on, a new one starts with it off, on every way to register it; step packages need an Agent or the owner."""
import gc
import argparse
import io
import json
import os
import sqlite3
import tempfile
import tkinter
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from bzztest import REL
from test_k5_integration import IntegrationCase, provider
from haivequeen import bees, cli, config, db, gui_config, hives, init, integration, runtime, scheduler


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
        """The deploy switch of the Project as the round and the gates read it."""
        env = next(e for e in scheduler.load_projects(conn, {}) if e.name == name)
        return integration.switches(env.settings)["deploy"]

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


class ExistingApiaryTests(ApiaryCase):
    """At the change existing Projects keep compile check, build and deploy."""

    def test_migration_keeps_deploy_on_for_an_apiary_of_the_old_database(self):
        conn = db.connect(self.old_database("old"))
        self.addCleanup(conn.close)
        env = next(e for e in scheduler.load_projects(conn, {}) if e.name == "old")
        self.assertEqual(integration.switches(env.settings), {"compileCheck": False, "build": True, "deploy": True})

    def test_apiary_registered_after_the_migration_is_new(self):
        conn = db.connect(self.old_database("old"))
        self.addCleanup(conn.close)
        other = self.base / "later"
        other.mkdir()
        init.register_project(conn, other)
        self.assertTrue(self.deploy(conn, "old"))
        self.assertFalse(self.deploy(conn, "later"))

    def test_second_open_marks_nothing_more(self):
        path = self.old_database("old")
        conn = db.connect(path)
        other = self.base / "later"
        other.mkdir()
        init.register_project(conn, other)
        conn.close()
        for version in (13, db.SCHEMA_VERSION):   # also when another process repeats the migration
            raw = sqlite3.connect(path)
            raw.execute("PRAGMA user_version = %d" % version)
            raw.close()
            conn = db.connect(path)
            self.assertFalse(self.deploy(conn, "later"))
            conn.close()

    def test_register_again_keeps_the_mark(self):
        conn = db.connect(self.old_database("ws"))
        self.addCleanup(conn.close)
        init.register_project(conn, self.workspace)
        self.assertTrue(self.deploy(conn, "ws"))

    def test_entry_of_the_owner_wins_over_the_mark(self):
        conn = db.connect(self.old_database("old"))
        self.addCleanup(conn.close)
        (self.workspace / ".haivequeen").mkdir()
        (self.workspace / ".haivequeen" / "project.toml").write_text("deployEnabled = false\n", encoding="utf-8")
        self.assertFalse(self.deploy(conn, "old"))

    def test_page_shows_deploy_on_for_an_old_apiary_only(self):
        self.assertIs(gui_config.apiary_settings({}, True)["deployEnabled"], True)
        self.assertIs(gui_config.apiary_settings({}, False)["deployEnabled"], False)
        self.assertIs(gui_config.apiary_settings({"deployEnabled": False}, True)["deployEnabled"], False)


class NewApiaryTests(ApiaryCase):
    """A new Project has compile check and build on and deploy off, however it was registered."""

    def test_fresh_database_marks_nothing(self):
        conn = self.open_db()
        init.register_project(conn, self.workspace)
        self.assertEqual(conn.execute("SELECT legacy_deploy FROM projects").fetchone()[0], 0)

    def test_registered_without_a_project_file(self):
        conn = self.open_db()
        init.register_project(conn, self.workspace)
        self.assertFalse((self.workspace / ".haivequeen" / "project.toml").exists())
        env = next(iter(scheduler.load_projects(conn, {})))
        self.assertEqual(integration.switches(env.settings), {"compileCheck": False, "build": True, "deploy": False})

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

    def test_apiary_without_a_project_file_gets_a_settings_page(self):
        conn = self.open_db()
        db.upsert_project(conn, "bare", str(self.workspace), "white", 0)
        db.upsert_project(conn, "nofolder", str(self.base / "missing"), "white", 0)
        (self.base / "config.toml").write_text("slots = 2\n", encoding="utf-8")
        try:
            root = tkinter.Tk()
        except tkinter.TclError as exc:
            self.skipTest("no display: %s" % exc)
        root.withdraw()
        self.addCleanup(gc.collect)   # Tk objects are freed in the main thread, never by the collector in a reading thread
        self.addCleanup(root.destroy)
        win = gui_config.ConfigWindow(root, conn, self.base, wait=True)
        self.assertEqual([a["path"] for a in win.apiaries], [self.workspace / ".haivequeen" / "project.toml"])


class SettingsWindowTests(ApiaryCase):
    """The settings window registers a project added on its page and writes the switches of a Project without a file."""

    def setUp(self):
        super().setUp()
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as exc:
            self.skipTest("no display: %s" % exc)
        self.root.withdraw()
        self.addCleanup(gc.collect)   # Tk objects are freed in the main thread, never by the collector in a reading thread
        self.addCleanup(self.root.destroy)
        (self.base / "config.toml").write_text('slots = 2\n', encoding="utf-8")

    def test_save_of_the_machine_file_registers_the_project(self):
        conn = self.open_db()
        win = gui_config.ConfigWindow(self.root, conn, self.base, wait=True)
        win.collect()
        win.data.setdefault("projects", []).append({"name": "paged", "workspace": str(self.workspace)})
        win.fill_nodes()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertFalse(self.deploy(conn, "paged"))

    def test_deploy_can_be_switched_on_for_an_apiary_without_a_file(self):
        conn = self.open_db()
        db.upsert_project(conn, "bare", str(self.workspace), "white", 0)
        win = gui_config.ConfigWindow(self.root, conn, self.base, wait=True)
        win.select(win.entries.index(("apiaries", 0)))
        self.assertIs({path[-1]: like for path, _getter, like in win.getters}["deployEnabled"], False)
        win.collect()
        win.target()["file"].update({"deployEnabled": True})
        win.build_form()
        win.save()
        self.assertEqual(win.bottom_note.cget("text"), "Saved.")
        self.assertTrue(self.deploy(conn, "bare"))


class StepPackageCloseTests(IntegrationCase):
    """A step package is not closed by a word of the Queen alone."""

    def queen(self):
        """Register the running Queen of the Plan."""
        runtime.upsert_agent(self.conn, "testproj", "Ada", {"status": "running", "phase": "queen", "plan_path": REL, "pid": 4242, "provider": "claude"})
        patcher = mock.patch.dict(os.environ, {"BZZ_AGENT": "Ada"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, fn, **values):
        """Run a command with the test machine settings; returns (exit code, printed JSON)."""
        buffer = io.StringIO()
        with mock.patch("haivequeen.config.load_config", return_value=self.machine), redirect_stdout(buffer):
            code = fn(argparse.Namespace(project="testproj", plan=REL, **values), self.conn)
        return code, json.loads(buffer.getvalue().strip().splitlines()[-1])

    def set_done(self, package):
        """Call bzz package with the state done."""
        return self.call(cli.cmd_package, package=package, state="done", reason="")

    def result(self, package):
        """Call bzz agent result with the outcome succeeded."""
        return self.call(cli.cmd_bee, action="result", package=package, cls="medium", attempt="a1", outcome="succeeded", open_requirements="[]", error="", blocked_by="", blocked_ref="",
                         provider="", name="", task="", steps="[]", paths="[]", rules="[]", cwd="", no_subagents=False)

    def test_queen_cannot_set_a_delegated_step_done(self):
        self.use(provider("claude", "Coding", "Queen"), provider("builder", "Build"), deployEnabled=False)
        env = self.hive()
        self.queen()
        integration.sync(env, REL, "claude")
        code, result = self.set_done(integration.BUILD)
        self.assertEqual(code, 1)
        self.assertIn(integration.BUILD, result["error"])
        self.assertEqual(integration.open_steps(env, REL), [integration.BUILD])

    def test_result_of_a_step_package_needs_a_started_bee(self):
        self.use(provider("claude", "Coding", "Queen"), provider("builder", "Build"), deployEnabled=False)
        env = self.hive()
        self.queen()
        integration.sync(env, REL, "claude")
        code, result = self.result(integration.BUILD)
        self.assertEqual(code, 1)
        self.assertIn("no Agent was started", result["error"])
        self.assertEqual(integration.open_steps(env, REL), [integration.BUILD])

    def test_result_of_the_started_bee_closes_the_step(self):
        self.use(provider("claude", "Coding", "Queen"), provider("builder", "Build"), deployEnabled=False)
        env = self.hive()
        self.queen()
        integration.sync(env, REL, "claude")
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?)", ("testproj", REL, integration.BUILD, "medium", "builder"))
        code, _result = self.result(integration.BUILD)
        self.assertEqual(code, 0)
        self.assertEqual(integration.open_steps(env, REL), [])

    def test_answer_of_the_owner_lets_the_queen_close_the_step(self):
        self.use(provider("claude", "Coding", "Queen"), deployEnabled=False)
        env = self.hive()
        self.queen()
        integration.sync(env, REL, "claude")
        code, _result = self.set_done(integration.BUILD)
        self.assertEqual(code, 1)
        asked = runtime.list_questions(self.conn, "testproj", status="open", plan=REL, item=integration.BUILD)
        db.answer_question(self.conn, asked[0]["id"], "built by hand", "owner")
        code, _result = self.set_done(integration.BUILD)
        self.assertEqual(code, 0)
        self.assertEqual(integration.open_steps(env, REL), [])

    def test_automatic_answer_does_not_count_as_the_owner(self):
        self.use(provider("claude", "Coding", "Queen"), deployEnabled=False)
        self.hive()
        integration_env = self.env()
        integration.sync(integration_env, REL, "claude")
        asked = runtime.list_questions(self.conn, "testproj", status="open", plan=REL, item=integration.BUILD)
        db.answer_question(self.conn, asked[0]["id"], "no longer needed", "auto")
        self.assertIn(integration.BUILD, integration.done_refusal(self.conn, "testproj", REL, integration.BUILD))

    def test_normal_package_is_not_affected(self):
        self.use(provider("claude", "Coding", "Queen"), deployEnabled=False)
        self.hive()
        self.assertEqual(integration.done_refusal(self.conn, "testproj", REL, "P1"), "")
        self.assertEqual(integration.result_refusal(self.conn, "testproj", REL, "P1"), "")


if __name__ == "__main__":
    unittest.main()
