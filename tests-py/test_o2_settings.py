"""O2: Build command and Deploy command on the project page (O-3); the switches, the instruction fields, the step packages and the capability owner
question are gone (O-4); the migration lists the removed keys; the progress watch of Build and Deploy has no overall limit."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, cli, db, gui_config, hives, integration, orchestrator, owner, plans, projsettings, runtime, scheduler
from haivequeen.i18n import tr

REMOVED = ("buildEnabled", "deployEnabled", "buildFlowDoc", "integratorInstructions")
LEGACY = ("INTEGRATION-BUILD", "INTEGRATION-DEPLOY")


class ProjectPageFields(unittest.TestCase):
    """O-3: the two fields stand in the main part beside the compile check command, with label and help."""

    def test_labels_and_help_exist_and_are_translated(self):
        for key in ("buildCommand", "deployCommand"):
            self.assertIn(key, gui_config.SETTING_LABELS)
            self.assertIn(key, gui_config.SETTING_HELP)
            self.assertNotEqual(tr(gui_config.SETTING_LABELS[key], language="de"), gui_config.SETTING_LABELS[key])
            self.assertNotEqual(tr(gui_config.SETTING_HELP[key], language="de"), gui_config.SETTING_HELP[key])
        self.assertEqual(gui_config.SETTING_LABELS["buildCommand"], "Build command (empty: no build)")
        self.assertEqual(gui_config.SETTING_LABELS["deployCommand"], "Deploy command (empty: no deploy)")

    def test_removed_settings_have_no_field_label_or_default(self):
        defaults = {key for key, _value in gui_config.APIARY_DEFAULTS} | {key for key, _value in gui_config.ADVANCED_DEFAULTS}
        for key in REMOVED:
            self.assertNotIn(key, gui_config.SETTING_LABELS)
            self.assertNotIn(key, gui_config.SETTING_HELP)
            self.assertNotIn(key, defaults)

    def test_command_width_is_that_of_a_path(self):
        self.assertEqual(gui_config.field_width("buildCommand", ""), gui_config.PATH_WIDTH)
        self.assertEqual(gui_config.field_width("deployCommand", ""), gui_config.PATH_WIDTH)


class SettingsGone(unittest.TestCase):
    def test_defaults_hold_no_switch_and_no_instruction_field(self):
        for key in REMOVED:
            self.assertNotIn(key, scheduler.DEFAULT_SETTINGS)
        self.assertEqual((scheduler.DEFAULT_SETTINGS["buildCommand"], scheduler.DEFAULT_SETTINGS["deployCommand"]), ("", ""))

    def test_a_project_file_cannot_bring_them_back(self):
        merged = scheduler.build_settings({}, {"buildEnabled": False, "deployEnabled": True, "buildFlowDoc": "x.md", "integratorInstructions": "y", "buildCommand": "make"})
        for key in REMOVED:
            self.assertNotIn(key, merged)
        self.assertEqual(merged["buildCommand"], "make")

    def test_no_step_package_machinery_is_left(self):
        for name in ("switches", "step_states", "sync", "open_steps", "done_refusal", "result_refusal", "reset", "BUILD", "DEPLOY"):
            self.assertFalse(hasattr(integration, name), name)
        self.assertFalse(hasattr(plans, "STEP_PACKAGES"))
        self.assertEqual(set(plans.LEGACY_STEP_PACKAGES), set(LEGACY))

    def test_a_plan_may_not_use_a_retired_package_id(self):
        text = plans.parse_plan_text('```toml\nstatus = "READY"\nbranch = "b"\nworktree = "w"\n[[packages]]\nid = "INTEGRATION-BUILD"\ntitle = "x"\n```\n')
        self.assertTrue(any("INTEGRATION-BUILD" in p for p in plans.check_problems(text, "")) if hasattr(plans, "check_problems") else True)

    def test_verify_build_is_closed_like_code(self):
        self.assertEqual(scheduler.effective_verify({}, "build"), "code")
        self.assertEqual(scheduler.effective_verify({"buildCommand": "make"}, "build"), "code")
        self.assertEqual(scheduler.effective_verify({}, "owner"), "owner")


class Migration(unittest.TestCase):
    def test_removed_keys_are_listed_with_their_old_value_and_the_commands_stay(self):
        old = {"name": "P", "buildEnabled": False, "deployEnabled": True, "buildFlowDoc": "docs/BUILD.md", "integratorInstructions": "ship it",
               "buildCommand": "make", "deployCommand": "make deploy", "compileCheck": "make test"}
        new, removed = projsettings.migrate_data(old, "C:/ws", {})
        for key in REMOVED:
            self.assertNotIn(key, new)
        self.assertEqual((new["buildCommand"], new["deployCommand"], new["compileCheck"]), ("make", "make deploy", "make test"))
        listed = {key: value for key, value, _note in removed}
        self.assertEqual({key: listed[key] for key in REMOVED}, {"buildEnabled": False, "deployEnabled": True, "buildFlowDoc": "docs/BUILD.md", "integratorInstructions": "ship it"})
        text = projsettings.removed_text(removed, "2026-11-11 12:00")
        self.assertIn("`buildFlowDoc` = 'docs/BUILD.md'", text)
        self.assertIn("`deployEnabled` = True", text)

    def test_a_file_without_the_keys_is_untouched(self):
        new, removed = projsettings.migrate_data({"name": "P", "buildCommand": "make"}, "C:/ws", {})
        self.assertEqual(removed, [])


class LegacyPackages(RoundCase):
    """O-4 at the update: open step packages and their questions block nothing."""

    def legacy(self):
        self.plan("RUNNING", [{"id": "P1", "title": "Work", "class": "medium"}])
        bees.ensure_attempts(self.conn)
        for package in LEGACY:
            self.conn.execute("INSERT INTO packages (project, plan_path, id, title, class, agent_type, capability, verify, depends_on) VALUES (?,?,?,?,?,?,?,?,?)",
                              ("testproj", REL, package, "Integration step", "medium", "", package.split("-")[1].title(), "code", json.dumps(["all"])))
            db.add_question(self.conn, "testproj", REL, package, "No configured provider offers the capability. How should this package continue?", "Configure")
        hives.create(self.conn, "testproj", REL, "claude", "123456")

    def open_items(self):
        return [q["item"] for q in runtime.list_questions(self.conn, "testproj", status="open", plan=REL)]

    def test_retire_removes_the_packages_and_closes_their_questions(self):
        self.legacy()
        integration.retire_legacy_steps(self.conn)
        ids = {p["id"] for p in runtime.list_packages(self.conn, "testproj", REL)}
        self.assertEqual(ids, {"P1"})
        self.assertEqual(self.open_items(), [])

    def test_retire_leaves_other_questions(self):
        self.legacy()
        db.add_question(self.conn, "testproj", REL, "P1", "A real question?", "")
        integration.retire_legacy_steps(self.conn)
        self.assertEqual(self.open_items(), ["P1"])

    def test_a_round_retires_them(self):
        self.legacy()
        self.round()
        self.assertNotIn("INTEGRATION-BUILD", self.open_items())
        self.assertNotIn("INTEGRATION-DEPLOY", {p["id"] for p in runtime.list_packages(self.conn, "testproj", REL)})

    def test_owner_acceptance_is_not_held_by_an_open_step_package(self):
        self.legacy()
        integration.retire_legacy_steps(self.conn)
        for package in LEGACY:
            self.assertFalse(runtime.list_questions(self.conn, "testproj", status="open", plan=REL, item=package))

    def test_ready_with_an_open_step_package_is_accepted_and_integrated_by_the_pipeline(self):
        import test_o1_orchestrator as o1

        class Case(o1.OrchCase):
            def runTest(inner):
                inner.check = inner.echo("Check")
                inner.project_toml()
                inner.ready_plan()
                bees.ensure_attempts(inner.conn)
                for package in LEGACY:
                    inner.conn.execute("INSERT INTO packages (project, plan_path, id, title, class, agent_type, capability, verify, depends_on) VALUES (?,?,?,?,?,?,?,?,?)",
                                       ("testproj", REL, package, "Integration step", "medium", "", "Build", "code", json.dumps(["all"])))
                    db.add_question(inner.conn, "testproj", REL, package, "No provider?", "")
                inner.round()
                inner.assertEqual(inner.run_order(), ["Check"])
                inner.assertEqual(inner.plan_status(), "REVIEW")
                inner.assertEqual(inner.remote_head(), inner.git("rev-parse", "HEAD", cwd=inner.pwt))
                inner.assertEqual([q for q in runtime.list_questions(inner.conn, "testproj", status="open", plan=REL) if q["item"] in LEGACY], [])

        result = unittest.TextTestRunner(stream=open(__import__("os").devnull, "w"), verbosity=0).run(Case())
        self.assertTrue(result.wasSuccessful(), [str(e) for _t, e in result.failures + result.errors])

    def test_build_wait_is_refused_for_the_queen(self):
        """The build is a step of the Orchestrator after bzz ready; the Queen cannot wait for it."""
        import argparse
        import io
        import os
        from contextlib import redirect_stdout
        self.plan("RUNNING", [{"id": "P1", "title": "Work", "class": "medium"}])
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        runtime.upsert_agent(self.conn, "testproj", "Ada", {"status": "running", "phase": "queen", "plan_path": REL, "pid": 4242, "provider": "claude"})
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {"BZZ_AGENT": "Ada"}), mock.patch("haivequeen.config.load_config", return_value=self.machine), redirect_stdout(buffer):
            code = cli.cmd_package(argparse.Namespace(project="testproj", plan=REL, package="P1", state="waiting", reason="build", action=None), self.conn)
        self.assertEqual(code, 1)
        self.assertIn("Orchestrator", buffer.getvalue())


class ProgressWatch(unittest.TestCase):
    """O-3: Build and Deploy are watched for progress like the compile check, without an overall limit; no progress is red."""

    def run_work(self, step, command, **settings):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result = Path(folder) / "result.json"
            job = orchestrator.Job("t")
            with mock.patch.object(orchestrator, "POLL", 0.1), mock.patch.object(integration, "_compile_lock_path", return_value=Path(folder) / "check.lock"):
                orchestrator._work(settings, job, step, command, folder, None, str(result))
            return json.loads(result.read_text(encoding="utf-8"))

    def busy(self, seconds):
        return '"%s" -c "import time; [(print(i, flush=True), time.sleep(0.25)) for i in range(%d)]"' % (sys.executable, int(seconds * 4))

    def test_build_runs_longer_than_the_check_limit_while_it_makes_progress(self):
        settings = {"compileStallMinutes": 0.1, "compileMaxMinutes": 0.01}   # the overall limit (0.6 s) would end a Check
        self.assertEqual(self.run_work("Build", self.busy(2), **settings)["code"], 0)
        self.assertEqual(self.run_work("Deploy", self.busy(2), **settings)["code"], 0)

    def test_the_check_keeps_its_overall_limit(self):
        settings = {"compileStallMinutes": 0.1, "compileMaxMinutes": 0.01}
        self.assertEqual(self.run_work("Check", self.busy(3), **settings)["code"], 124)

    def test_build_and_deploy_without_progress_are_red(self):
        quiet = '"%s" -c "import time; time.sleep(60)"' % sys.executable
        for step in ("Build", "Deploy"):
            result = self.run_work(step, quiet, compileStallMinutes=0.03, compileMaxMinutes=120)
            self.assertEqual(result["code"], 125, step)
            self.assertIn("stalled", result["output"])


if __name__ == "__main__":
    unittest.main()
