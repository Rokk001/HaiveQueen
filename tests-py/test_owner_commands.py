"""Regression coverage for owner-only command entry points."""
import contextlib
import io
import json
import os
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, cli, db, runtime, plans, inbox, servers, config, owner, hives


class OwnerCommands(RoundCase):
    def invoke(self, *args, agent=None):
        with mock.patch.dict(os.environ, {"BZZ_AGENT": agent} if agent else {}, clear=False):
            if agent is None:
                os.environ.pop("BZZ_AGENT", None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                try:
                    parsed = cli.build_parser().parse_args(args)
                    code = parsed.fn(parsed, self.conn)
                except SystemExit as exc:
                    return exc.code, {}
            return code, json.loads(output.getvalue())

    def waiting_for_owner(self):
        self.conn.execute("INSERT INTO queue(project, branch, tip, plan_path, status, updated_at) VALUES (?,?,?,?,?,?)", ("testproj", "plan-branch", "abcdef012345", REL, "integrated", db.now()))
        ready = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.execute("INSERT OR REPLACE INTO hives(project, plan_path, provider, colour, state, ready_id) VALUES (?,?,?,?,?,?)", ("testproj", REL, "test", "blue", "owner_check", ready))

    def test_early_owner_check_cannot_be_decided(self):
        self.plan("REVIEW")
        db.add_question(self.conn, "testproj", REL, "owner-check", "Early?", "")
        common = ("--project", "testproj", "--plan", REL)
        for action, extra in (("accept", ()), ("reject", ("--text", "fix"))):
            self.assertNotEqual(self.invoke("plan", action, *common, *extra)[0], 0)

    def test_recovered_integrated_owner_check_can_be_accepted(self):
        self.plan("REVIEW")
        self.waiting_for_owner()
        self.conn.execute("UPDATE hives SET ready_id=NULL WHERE project=? AND plan_path=?", ("testproj", REL))
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        self.assertEqual(self.invoke("plan", "accept", "--project", "testproj", "--plan", REL)[0], 0)

    def test_rejected_integration_cannot_be_accepted_again(self):
        self.plan("REVIEW")
        self.waiting_for_owner()
        ready = self.conn.execute("SELECT ready_id FROM hives WHERE project=? AND plan_path=?", ("testproj", REL)).fetchone()[0]
        runtime.kv_set(self.conn, "testproj", "hive-rejected-ready:" + REL, str(ready))
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        self.assertNotEqual(self.invoke("plan", "accept", "--project", "testproj", "--plan", REL)[0], 0)

    def test_inbox_conflict_rolls_back_provider_and_plan(self):
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        original = '[[providers]]\nname="account"\nprotocol="codex"\naccess="subscription"\nenabled=true\n'
        path.write_text(original, encoding="utf-8")
        self.plan("RUNNING")
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "conflict.toml").write_text('[[provider]]\nprovider="account"\naction="disable"\n[[pause]]\nplan="%s"\n[[pause]]\nplan="%s"\n' % (REL, REL), encoding="utf-8")
        self.assertEqual(inbox.process_inbox(self.env())["failed"], ["conflict.toml"])
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "RUNNING")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_inbox_provider_write_is_restored_when_later_action_fails(self):
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        original = '[[providers]]\nname="account"\nprotocol="codex"\naccess="subscription"\nenabled=true\n'
        path.write_text(original, encoding="utf-8")
        self.plan("RUNNING", packages=[{"id": "P1", "class": "medium"}])
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "conflict.toml").write_text('[[provider]]\nprovider="account"\naction="disable"\n[[unassign]]\nplan="%s"\npackage="P1"\n' % REL, encoding="utf-8")
        apply = owner.apply
        def fail_after_provider(conn, action, *args, **kwargs):
            if action == "unassign":
                raise ValueError("later failure")
            return apply(conn, action, *args, **kwargs)
        with mock.patch.object(owner, "apply", side_effect=fail_after_provider):
            self.assertEqual(inbox.process_inbox(self.env())["failed"], ["conflict.toml"])
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_inbox_failure_preserves_concurrent_provider_edit(self):
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        original = '[[providers]]\nname="account"\nprotocol="codex"\naccess="subscription"\nenabled=true\n'
        path.write_text(original, encoding="utf-8")
        self.plan("RUNNING", packages=[{"id": "P1", "class": "medium"}])
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "conflict.toml").write_text('[[provider]]\nprovider="account"\naction="disable"\n[[unassign]]\nplan="%s"\npackage="P1"\n' % REL, encoding="utf-8")
        apply = owner.apply
        concurrent = original + '# concurrent edit\n'
        def fail_after_edit(conn, action, *args, **kwargs):
            if action == "unassign":
                path.write_text(concurrent, encoding="utf-8")
                raise ValueError("later failure")
            return apply(conn, action, *args, **kwargs)
        with mock.patch.object(owner, "apply", side_effect=fail_after_edit):
            inbox.process_inbox(self.env())
        self.assertEqual(path.read_text(encoding="utf-8"), concurrent)

    def test_inbox_duplicate_accept_has_no_effect(self):
        self.plan("REVIEW")
        self.waiting_for_owner()
        qid = db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "duplicate.toml").write_text(('[[accept]]\nplan="%s"\n' % REL) * 2, encoding="utf-8")
        self.assertEqual(inbox.process_inbox(self.env())["failed"], ["duplicate.toml"])
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "REVIEW")
        self.assertEqual(self.conn.execute("SELECT status FROM questions WHERE id=?", (qid,)).fetchone()[0], "open")
        self.assertIn('REVIEW', (self.repo / REL).read_text(encoding="utf-8"))

    def test_new_project_default_uses_current_terminology(self):
        from haivequeen import gui_config
        window = mock.Mock()
        window.collect.return_value = True
        window.data = {"projects": []}
        window.entries = [("apiaries", 0)]
        gui_config.ConfigWindow.add_apiary(window)
        self.assertEqual(window.data["projects"][0]["name"], "new-project")

    def test_every_owner_command_refuses_agents(self):
        common = ("--project", "testproj", "--plan", REL)
        commands = [("plan", action, *common) for action in ("accept", "pause", "resume", "release")]
        commands += [("plan", "reject", *common, "--text", "finding", "--package", "P1"),
                     ("package", "unassign", *common, "--package", "P1")]
        commands += [("provider", action, "--provider", "local") for action in ("enable", "disable", "test")]
        for command in commands:
            with self.subTest(command=command):
                code, result = self.invoke(*command, agent="worker")
                self.assertNotEqual(code, 0)
                self.assertIn("owner", result.get("error", "").lower())

    def test_pause_resume_and_refused_statuses(self):
        common = ("--project", "testproj", "--plan", REL)
        self.plan("IN_PROGRESS")
        self.assertEqual(self.invoke("plan", "pause", *common)[0], 0)
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "PAUSED")
        self.assertNotEqual(self.invoke("plan", "pause", *common)[0], 0)
        self.assertEqual(self.invoke("plan", "resume", *common)[0], 0)
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "RUNNING")
        self.assertNotEqual(self.invoke("plan", "resume", *common)[0], 0)
        for status in ("DRAFT", "DONE", "CANCELLED"):
            with self.subTest(status=status):
                self.plan(status)
                self.assertNotEqual(self.invoke("plan", "pause", *common)[0], 0)

    def test_accept_requires_owner_check_and_closes_all_checks(self):
        self.plan("REVIEW")
        self.waiting_for_owner()
        common = ("--project", "testproj", "--plan", REL)
        self.assertNotEqual(self.invoke("plan", "accept", *common)[0], 0)
        ids = [db.add_question(self.conn, "testproj", REL, item, "Check?", "")
               for item in ("owner-check", "owner-check-other", "queen-ended", "stall", "STALL-worker")]
        blocker = db.add_question(self.conn, "testproj", REL, "other", "Question?", "")
        code, result = self.invoke("plan", "accept", *common)
        self.assertNotEqual(code, 0)
        self.assertIn("other", result.get("error", ""))
        db.answer_question(self.conn, blocker, "resolved", "owner")
        self.assertEqual(self.invoke("plan", "accept", *common)[0], 0)
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "DONE")
        for qid in ids:
            self.assertEqual(self.conn.execute("SELECT status FROM questions WHERE id=?", (qid,)).fetchone()[0], "answered")

    def test_unassign_keeps_escalated_class_and_is_idempotent(self):
        self.plan("IN_PROGRESS", packages=[{"id": "P1", "class": "medium"}])
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments VALUES (?,?,?,?,?)", ("testproj", REL, "P1", "heavy", "codex"))
        self.conn.execute("INSERT INTO bee_attempts VALUES (?,?,?,?,?)", ("testproj", REL, "P1", "medium", 2))
        self.agent("running-package", plan_path=REL, phase="bee", package="P1", status="running", pid=401)
        args = ("package", "unassign", "--project", "testproj", "--plan", REL, "--package", "P1")
        self.assertEqual(self.invoke(*args)[0], 0)
        row = self.conn.execute("SELECT class, provider FROM bee_assignments WHERE project=? AND plan_path=? AND package='P1'", ("testproj", REL)).fetchone()
        self.assertEqual(row[0], "heavy")
        self.assertFalse(row[1])
        self.assertEqual(bees.failure_count(self.conn, "testproj", REL, "P1", "medium"), 2)
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "running-package")["status"], "running")
        self.assertEqual(self.invoke(*args)[0], 0)

    def test_unassign_running_package_selects_provider_on_next_start(self):
        self.mock_agent_worktrees()
        self.machine["providers"][0]["slots"] = 1
        self.plan("IN_PROGRESS", packages=[{"id": "P1", "title": "First", "class": "medium"}])
        hives.create(self.conn, "testproj", REL, "claude", "123456")
        self.agent("queen", phase="queen", status="running", provider="claude", pid=42)
        self.agent("worker", phase="bee", package="P1", status="running", provider="claude", pid=43)
        bees.ensure_attempts(self.conn)
        self.conn.execute("INSERT INTO bee_assignments VALUES (?,?,?,?,?)", ("testproj", REL, "P1", "medium", "claude"))
        self.assertEqual(self.invoke("package", "unassign", "--project", "testproj", "--plan", REL, "--package", "P1")[0], 0)
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "worker")["status"], "running")
        runtime.upsert_agent(self.conn, "testproj", "worker", {"status": "done"})
        wt = self.base / "bee-wt"
        wt.mkdir()
        with mock.patch("haivequeen.bees.valid_bee_worktree", return_value=True), mock.patch("haivequeen.loop._do_start", return_value=True):
            result = bees.start_process(self.env(), REL, "P1", "Ada", "medium", "task", ["one"], [], [], cwd=str(wt))
        self.assertEqual((result["kind"], result["provider"]), ("process", "codex"))
        self.assertEqual(self.conn.execute("SELECT provider FROM bee_assignments WHERE package='P1'").fetchone()[0], "codex")

    def test_reject_requires_text_and_preserves_pause(self):
        self.plan("REVIEW", packages=[{"id": "P1", "class": "medium"}])
        self.waiting_for_owner()
        common = ("--project", "testproj", "--plan", REL)
        self.assertNotEqual(self.invoke("plan", "reject", *common, "--text", "finding")[0], 0)
        qid = db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        self.assertNotEqual(self.invoke("plan", "reject", *common, "--text", " ")[0], 0)
        self.assertEqual(self.invoke("plan", "pause", *common)[0], 0)
        self.assertEqual(self.invoke("plan", "reject", *common, "--text", "fix this", "--package", "P1")[0], 0)
        question = self.conn.execute("SELECT status, answer, owner_check_result FROM questions WHERE id=?", (qid,)).fetchone()
        self.assertEqual(tuple(question), ("answered", "fix this", "rejected"))
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "PAUSED")
        self.assertIn("P1", bees.rejected_packages(self.conn, "testproj", REL))

    def test_owner_check_free_text_is_only_a_note(self):
        self.plan("REVIEW")
        qid = db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        db.answer_question(self.conn, qid, "looks fine", "owner")
        row = self.conn.execute("SELECT status, answer, owner_check_result FROM questions WHERE id=?", (qid,)).fetchone()
        self.assertEqual(tuple(row), ("open", "looks fine", None))

    def test_release_rejects_terminal_plans(self):
        common = ("--project", "testproj", "--plan", REL)
        for status in ("DONE", "CANCELLED"):
            with self.subTest(status=status):
                self.plan(status)
                self.assertNotEqual(self.invoke("plan", "release", *common)[0], 0)

    def test_release_replaces_previous_decision(self):
        self.plan("REVIEW")
        common = ("--project", "testproj", "--plan", REL)
        runtime.add_decision(self.conn, "testproj", "owner", "any commit", REL, "owner-release")
        self.assertEqual(self.invoke("plan", "release", *common, "--commit", "abcdef1234567")[0], 0)
        active = runtime.list_decisions(self.conn, "testproj", plan=REL, item="owner-release", active_only=True)
        self.assertEqual(len(active), 1)
        self.assertIn("abcdef1234567", active[0]["text"])

    def test_accept_refuses_open_integration_step(self):
        self.plan("REVIEW", packages=[{"id": plans.STEP_PACKAGES["Deploy"], "class": "medium"}])
        self.waiting_for_owner()
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        code, result = self.invoke("plan", "accept", "--project", "testproj", "--plan", REL)
        self.assertNotEqual(code, 0)
        self.assertIn(plans.STEP_PACKAGES["Deploy"], result.get("error", ""))

    def test_reject_ends_release_and_delivers_finding(self):
        self.plan("REVIEW")
        self.waiting_for_owner()
        common = ("--project", "testproj", "--plan", REL)
        self.assertEqual(self.invoke("plan", "release", *common)[0], 0)
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        self.assertEqual(self.invoke("plan", "reject", *common, "--text", "Fix the output")[0], 0)
        self.assertFalse(runtime.list_decisions(self.conn, "testproj", plan=REL, item="owner-release", active_only=True))
        findings = runtime.list_questions(self.conn, "testproj", status="answered", plan=REL, item="owner-check-answered")
        self.assertEqual(findings[-1]["answer"], "Fix the output")

    def test_provider_commands_save_switch_and_test_local_connection(self):
        from haivequeen import gui_config
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[[providers]]\nname="local"\nprotocol="codex"\naccess="llm"\naddress="http://localhost:1234"\n', encoding="utf-8")
        self.assertEqual(self.invoke("provider", "disable", "--provider", "local")[0], 0)
        self.assertFalse(gui_config.load_migrated(path)["providers"][0]["enabled"])
        self.assertEqual(self.invoke("provider", "enable", "--provider", "local")[0], 0)
        self.assertTrue(gui_config.load_migrated(path)["providers"][0]["enabled"])
        with mock.patch.object(servers, "run_test", return_value={"reachable": True}) as probe, mock.patch.object(servers, "summary", return_value="Connection usable"):
            self.assertEqual(self.invoke("provider", "test", "--provider", "local")[0], 0)
            probe.assert_called_once()

    def test_inbox_owner_actions(self):
        self.plan("REVIEW", packages=[{"id": "P1", "class": "medium"}])
        self.waiting_for_owner()
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        def send(action, extra=""):
            (folder / "owner.toml").write_text('[[%s]]\nproject="testproj"\nplan="%s"\n%s' % (action, REL, extra), encoding="utf-8")
            self.assertEqual(inbox.process_inbox(self.env())["failed"], [])
        send("pause")
        send("resume")
        send("release")
        send("unassign", 'package="P1"\n')
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        send("reject", 'text="fix it"\npackages=["P1"]\nby="coordinator"\n')
        self.assertIn("P1", bees.rejected_packages(self.conn, "testproj", REL))
        runtime.set_plan_status(self.conn, "testproj", REL, "REVIEW")
        self.waiting_for_owner()
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check again?", "")
        send("accept")
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "DONE")

    def test_paused_accept_and_every_pausable_status(self):
        common = ("--project", "testproj", "--plan", REL)
        for status in ("SENT", "PREPARING", "QUEUED", "RUNNING", "WAITING", "REVIEW"):
            with self.subTest(status=status):
                self.plan(status)
                self.assertEqual(self.invoke("plan", "pause", *common)[0], 0)
                self.assertEqual(self.invoke("plan", "resume", *common)[0], 0)
                self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], status)
        self.waiting_for_owner()
        db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        self.invoke("plan", "pause", *common)
        self.assertEqual(self.invoke("plan", "accept", *common)[0], 0)

    def test_running_owner_check_cannot_be_decided(self):
        self.plan("RUNNING")
        qid = db.add_question(self.conn, "testproj", REL, "owner-check", "Check?", "")
        common = ("--project", "testproj", "--plan", REL)
        self.assertNotEqual(self.invoke("plan", "accept", *common)[0], 0)
        self.assertNotEqual(self.invoke("plan", "reject", *common, "--text", "finding")[0], 0)
        self.assertIsNone(db.owner_check_result(self.conn, qid))

    def test_legacy_release_replaces_command_release(self):
        self.plan("REVIEW")
        common = ("--project", "testproj", "--plan", REL)
        self.assertEqual(self.invoke("plan", "release", *common)[0], 0)
        old = runtime.list_decisions(self.conn, "testproj", plan=REL, item="owner-release", active_only=True)
        self.assertEqual(old[0]["text"], "any commit")
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "legacy.toml").write_text('[[decision]]\nsource="owner"\nplan="%s"\nitem="owner-release"\ntext="abcdef1234567"\n' % REL, encoding="utf-8")
        self.assertFalse(inbox.process_inbox(self.env())["failed"])
        active = runtime.list_decisions(self.conn, "testproj", plan=REL, item="owner-release", active_only=True)
        self.assertEqual([d["text"] for d in active], ["abcdef1234567"])

    def test_inbox_provider_and_nonlocal_test(self):
        from haivequeen import gui_config
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[[providers]]\nname="account"\nprotocol="codex"\naccess="subscription"\n', encoding="utf-8")
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        for operation in ("disable", "enable", "test"):
            (folder / "provider.toml").write_text('[[provider]]\nprovider="account"\naction="%s"\n' % operation, encoding="utf-8")
            self.assertFalse(inbox.process_inbox(self.env())["failed"])
        self.assertTrue(gui_config.load_migrated(path)["providers"][0]["enabled"])
        with mock.patch.object(servers, "run_test") as probe:
            code, result = self.invoke("provider", "test", "--provider", "account")
            self.assertEqual(code, 0)
            self.assertIn("nothing to test", result["message"].lower())
            probe.assert_not_called()

    def test_invalid_inbox_action_does_not_store_other_decisions(self):
        self.plan("RUNNING")
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / "bad.toml").write_text('[[decision]]\nsource="owner"\ntext="Do not store"\n[[reject]]\nplan="%s"\ntext="finding"\n' % REL, encoding="utf-8")
        self.assertEqual(inbox.process_inbox(self.env())["failed"], ["bad.toml"])
        self.assertFalse(runtime.list_decisions(self.conn, "testproj"))

    def test_local_test_stores_connection_result(self):
        path = config.config_dir() / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[[providers]]\nname="local"\nprotocol="codex"\naccess="llm"\naddress="http://localhost:1234"\n', encoding="utf-8")
        with mock.patch.object(servers, "_request", return_value=(200, '{"data":[{"id":"model"}]}')), mock.patch.object(servers, "summary", return_value="Tested connection"):
            code, result = self.invoke("provider", "test", "--provider", "local")
        self.assertEqual(code, 0)
        stored = servers._read_tests()["local"]
        self.assertEqual(stored, result["result"])
        self.assertEqual(stored["models"], ["model"])
        self.assertTrue(stored["reachable"])
