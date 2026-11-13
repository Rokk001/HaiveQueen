"""M2: the owner stops a single Agent: dashboard button, bzz agent stop and inbox entry share one function."""
import contextlib
import io
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase, db, runtime
from haivequeen import bees, cli, faults, dashboard_data, hives, inbox, owner, readygate


class StopAgentTest(RoundCase):
    def setUp(self):
        super().setUp()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='testproj' AND plan_path=?", (REL,))
        self.agent("Queen-A", phase="queen", status="running", started=5, pid=os.getpid(), cwd=str(self.wt))
        self.agent("Bea", phase="bee", package="P1", status="running", started=3, pid=999991)
        self.agent("Cleo", phase="bee", package="P2", status="running", started=3, pid=999992)
        self.agent("Queen-A-ready-check", phase="verify", package="READY-CHECK", status="running", started=2, pid=999993)

    def bzz(self, *argv, agent=None):
        buf = io.StringIO()
        env = {"BZZ_AGENT": agent} if agent else {}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(buf):
            if not agent:
                os.environ.pop("BZZ_AGENT", None)
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def status(self, name):
        return runtime.get_agent(self.conn, "testproj", name)["status"]

    def failures(self):
        bees.ensure_attempts(self.conn)
        return (self.conn.execute("SELECT COALESCE(SUM(failures),0) FROM bee_attempts").fetchone()[0],
                self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0])

    def assert_only_stopped(self, name, kill):
        kill.assert_called_once()
        self.assertNotEqual(self.status(name), "running")
        for other in {"Queen-A", "Bea", "Cleo", "Queen-A-ready-check"} - {name}:
            self.assertEqual(self.status(other), "running", other)
        self.assertEqual(self.failures(), (0, 0))

    def seed_fault_detection(self):
        faults._check_agents(self.env())

    def assert_no_missing_result_fault(self):
        faults._check_agents(self.env())
        self.assertEqual(db.rows(self.conn, "SELECT * FROM questions WHERE item='agent_no_result'"), [])

    def test_shared_function_stops_a_package_agent(self):
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True) as kill:
            owner.stop_agent(self.conn, "testproj", "Bea")
        self.assert_only_stopped("Bea", kill)
        self.assertEqual(kill.call_args[0][0], 999991)
        self.assertTrue(bees.package_stopped(self.conn, "testproj", REL, "P1"))
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "P2"))

    def test_cli_entry_stops_a_package_agent(self):
        self.seed_fault_detection()
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True) as kill:
            code, res = self.bzz("agent", "stop", "Cleo", "--project", "testproj")
        self.assertEqual(code, 0)
        self.assertTrue(res["ok"])
        self.assert_only_stopped("Cleo", kill)
        self.assert_no_missing_result_fault()

    def test_force_stop_cli_entry_preserves_single_agent_rules(self):
        self.agent('worker-control', phase='bee', package='P2', status='running', started=3, pid=999994)
        self.seed_fault_detection()
        with mock.patch('haivequeen.platform.kill_process_tree', return_value=True) as kill:
            code, res = self.bzz('agent', 'force-stop', 'worker-control', '--project', 'testproj')
        self.assertEqual(code, 0)
        self.assertTrue(res['ok'])
        self.assert_only_stopped('worker-control', kill)
        self.assertTrue(bees.package_stopped(self.conn, 'testproj', REL, 'P2'))
        self.assert_no_missing_result_fault()

    def test_force_stop_cli_keeps_owner_queen_and_subagent_restrictions(self):
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'Sub', 'medium', 'claude', queen='Queen-A')
        with mock.patch('haivequeen.platform.kill_process_tree') as kill:
            for name, caller in (('worker-control', 'Queen-A'), ('Queen-A', None), ('Sub', None)):
                with self.subTest(name=name, caller=caller):
                    code, res = self.bzz('agent', 'force-stop', name, '--project', 'testproj', agent=caller)
                    self.assertNotEqual(code, 0)
                    self.assertFalse(res['ok'])
        kill.assert_not_called()

    def test_agent_help_names_force_stop_and_compatibility_alias(self):
        parser = cli.build_parser()._subparsers._group_actions[0].choices['agent']
        help_text = ' '.join(parser.format_help().split())
        self.assertIn('bzz agent force-stop <name>', help_text)
        self.assertIn('compatibility alias', help_text)

    def test_cli_is_owner_only_and_refuses_the_queen(self):
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True) as kill:
            code, res = self.bzz("agent", "stop", "Bea", "--project", "testproj", agent="Queen-A")
            self.assertNotEqual(code, 0)
            self.assertIn("owner", res["error"])
            code, res = self.bzz("agent", "stop", "Queen-A", "--project", "testproj")
            self.assertNotEqual(code, 0)
            self.assertIn("dequeen", res["error"])
            code, res = self.bzz("agent", "stop", "Nobody", "--project", "testproj")
            self.assertNotEqual(code, 0)
        kill.assert_not_called()
        self.assertEqual(self.status("Queen-A"), "running")
        self.assertEqual(self.status("Bea"), "running")

    def test_inbox_entry_stops_a_ready_check_agent(self):
        self.seed_fault_detection()
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True)
        (box / "s.toml").write_text('[[stop_agent]]\nname = "Queen-A-ready-check"\n', encoding="utf-8")
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True) as kill:
            res = inbox.process_inbox(self.env())
        self.assertEqual(res["failed"], [])
        self.assert_only_stopped("Queen-A-ready-check", kill)
        self.assert_no_missing_result_fault()

    def test_queen_gets_the_owner_message_once(self):
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True):
            owner.stop_agent(self.conn, "testproj", "Bea")
        code, res = self.bzz("note", "--project", "testproj", "--text", "x", agent="Queen-A")
        self.assertEqual([m["text"] for m in res["owner_messages"]], ["Agent Bea stopped by the owner"])
        self.assertNotIn("owner_messages", self.bzz("note", "--project", "testproj", "--text", "x", agent="Queen-A")[1])
        source = self.conn.execute("SELECT source FROM decisions WHERE text LIKE '%stopped by the owner'").fetchone()[0]
        self.assertEqual(source, "owner")

    def test_stopped_package_is_not_restarted_asked_or_escalated_until_resumed(self):
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True):
            owner.stop_agent(self.conn, "testproj", "Bea")
        with mock.patch.object(bees, "valid_bee_worktree", return_value=True):
            self.assertFalse(bees.start_process(self.env(), REL, "P1", "Dina", "medium", "t", ["s"], [], "", cwd=str(self.wt)))
            self.assertIn("owner stopped", bees.LAST_REFUSAL)
            # a later Queen is held back too
            self.agent("Queen-A", phase="queen", status="done", ended=1)
            self.agent("Queen-B", phase="queen", status="running", started=0, pid=os.getpid(), cwd=str(self.wt))
            self.assertFalse(bees.start_process(self.env(), REL, "P1", "Dina", "medium", "t", ["s"], [], "", cwd=str(self.wt)))
        self.assertNotIn("P1", bees.startable_not_started(self.env(), REL))
        self.assertIn("P1", bees.stop_notice_text(self.conn, "testproj", REL))
        self.assertEqual(self.failures(), (0, 0))
        # an independent package goes on
        with mock.patch.object(bees, "valid_bee_worktree", return_value=True):
            bees.start_process(self.env(), REL, "P2", "Eli", "medium", "t", ["s"], [], "", cwd=str(self.wt))
        self.assertNotIn("owner stopped", bees.LAST_REFUSAL or "")
        bees.resume_package(self.conn, "testproj", REL, "P1")
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "P1"))

    def test_stopped_ready_check_counts_as_aborted_and_ready_is_not_repeated(self):
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True):
            owner.stop_agent(self.conn, "testproj", "Queen-A-ready-check")
        row = runtime.get_agent(self.conn, "testproj", "Queen-A-ready-check")
        verdict, text = readygate.ended_verdict(row, "READY-CHECK: PASSED")
        self.assertEqual(verdict, "aborted")
        self.assertIn("explicit", text)
        self.assertEqual(readygate.ended_verdict({"last_line": "x"}, "READY-CHECK: PASSED")[0], "passed")
        self.allow_ready()
        with mock.patch.object(readygate, "CHECK_RUNNER", lambda request: ("aborted", "stopped by the owner")):
            accepted, message, waiting = readygate.gate(self.env(), REL, runtime.get_agent(self.conn, "testproj", "Queen-A"))
        self.assertFalse(accepted)
        self.assertFalse(waiting)
        self.assertIn("aborted", message)
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "READY-CHECK"))

    def test_subagent_cannot_be_stopped_alone(self):
        bees.record_embedded_start(self.conn, "testproj", REL, "P1", "Sub", "medium", "claude", queen="Queen-A")
        reason = owner.EMBEDDED_REASON
        with mock.patch("haivequeen.platform.kill_process_tree", return_value=True) as kill:
            with self.assertRaises(ValueError) as ctx:
                owner.stop_agent(self.conn, "testproj", "Sub")
            self.assertEqual(str(ctx.exception), reason % "Sub")
            code, res = self.bzz("agent", "stop", "Sub", "--project", "testproj")
            self.assertNotEqual(code, 0)
            self.assertEqual(res["error"], reason % "Sub")
            box = inbox.inbox_dir(self.repo)
            box.mkdir(parents=True)
            (box / "s.toml").write_text('[[stop_agent]]\nname = "Sub"\n', encoding="utf-8")
            res = inbox.process_inbox(self.env())
            self.assertEqual(res["done"], [])
            self.assertIn(reason % "Sub", (box / "failed" / "s.error.txt").read_text(encoding="utf-8"))
        kill.assert_not_called()
        self.assertEqual(self.status("Queen-A"), "running")
        self.assertFalse(bees.package_stopped(self.conn, "testproj", REL, "P1"))

    def test_dashboard_rows_carry_the_stop_state(self):
        bees.record_embedded_start(self.conn, "testproj", REL, "P1", "Sub", "medium", "claude", queen="Queen-A")
        agents = [dict(a) for a in runtime.list_agents(self.conn, "testproj", "active")]
        rows = dashboard_data.bee_list(agents, {}, set(), datetime.now(), embedded=dashboard_data.embedded_rows(self.conn))
        by = {r["name"]: r for r in rows}
        self.assertEqual(by["Cleo"]["stop_agent"], "Cleo")
        self.assertEqual(by["Cleo"]["stop_disabled"], "")
        self.assertEqual(by["Sub"]["stop_disabled"], owner.EMBEDDED_REASON % "Sub")


class QueenRoleTextTest(unittest.TestCase):
    def test_role_text(self):
        text = (Path(__file__).resolve().parent.parent / "roles" / "queen.md").read_text(encoding="utf-8")
        self.assertIn("stopped by the owner", text)
        self.assertIn("explicit instruction of the owner", text)
        self.assertIn("bzz ready", text[text.index("stopped by the owner"):])


if __name__ == "__main__":
    unittest.main()
