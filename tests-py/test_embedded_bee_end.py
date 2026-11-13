"""End of embedded subagent Agents (E-1 to E-5): an Agent counts as ended when its Queen ended and her processes are absent; the running Queen reports
her own Agent with a result file; nothing is edited by hand. Fixture data only, no real process is touched."""
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bzztest import REL, RoundCase, runtime  # noqa: E402
from haivequeen import bees, cli, db, dashboard_data, hives, roles, scheduler  # noqa: E402


def ago(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


class EndCase(RoundCase):
    """A fake process table: `world` maps a pid to its identity (None = identity unreadable); `denied` are pids Windows refuses to open."""

    def setUp(self):
        super().setUp()
        self.plan("RUNNING", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}])
        hives.create(self.conn, "testproj", REL, "claude", "FF0000")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='testproj' AND plan_path=?", (REL,))
        self.world, self.denied, self.start_times = {}, set(), {}
        for target, fake in (("process_alive", lambda pid: int(pid) in self.world),
                             ("process_identity", lambda pid: self.world.get(int(pid))),
                             ("process_inaccessible", lambda pid: int(pid) in self.denied),
                             ("process_start_time", lambda pid: self.start_times.get(int(pid)))):
            patcher = mock.patch("haivequeen.platform." + target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def queen(self, name="Queen-A", status="done", pid=None, identity=None, cli_pid=None, cli_identity=None, started=60, ended=10, replace=False):
        """Insert a Queen row (always a new row, so one name can have several)."""
        extra = {k: v for k, v in (("processIdentity", identity), ("cliPid", cli_pid), ("cliIdentity", cli_identity)) if v}
        self.conn.execute(
            "INSERT INTO agents(project,name,plan_path,phase,provider,status,pid,cwd,started_at,ended_at,archived,extra_json) VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
            ("testproj", name, REL, "queen", "claude", status, pid, str(self.wt), ago(started), ago(ended) if status not in ("running", "starting") else None,
             json.dumps(extra) if extra else None))

    def bee(self, name="Cia", package="P1", queen="Queen-A", started=30):
        bees.record_embedded_start(self.conn, "testproj", REL, package, name, "medium", "claude", queen=queen)
        self.conn.execute("UPDATE embedded_bees SET started_at=? WHERE name=?", (ago(started), name))

    def open_names(self):
        return sorted(r["name"] for r in dashboard_data.embedded_rows(self.conn))

    def outcome(self, name):
        return self.conn.execute("SELECT outcome FROM embedded_bees WHERE name=?", (name,)).fetchone()[0]

    def close(self):
        return bees.close_ended_embedded(self.conn, "testproj")


    def args(self, outcome, attempt="Cia", evidence="", package="P1"):
        return types.SimpleNamespace(project="testproj", plan=REL, package=package, cls="medium", action="result", outcome=outcome, attempt=attempt,
                                     open_requirements="[]", error="", blocked_by="", blocked_ref="", ended_evidence=evidence)

    def cli_as(self, name):
        env = self.env()
        stack = __import__("contextlib").ExitStack()
        stack.enter_context(mock.patch.dict("os.environ", {"BZZ_AGENT": name}))
        stack.enter_context(mock.patch.object(scheduler, "load_projects", return_value=[env]))
        stack.enter_context(mock.patch("haivequeen.config.load_config", return_value={}))
        stack.enter_context(mock.patch.object(cli, "out"))
        stack.enter_context(mock.patch.object(cli, "fail", side_effect=lambda text: text))
        return stack


class QueenEndTests(EndCase):
    def test_an_ended_queen_with_absent_process_closes_the_row_as_interrupted_and_frees_place_and_name(self):
        self.queen(pid=4001, identity="win:1")
        self.bee()
        self.assertEqual(bees.free_bee_places(self.env(), REL, 5), 2)
        self.assertEqual(self.close(), 1)
        self.assertEqual(self.outcome("Cia"), "interrupted")
        self.assertEqual(self.open_names(), [])
        self.assertEqual(bees.free_bee_places(self.env(), REL, 5), 3)
        self.assertNotIn("Cia", bees.open_embedded_names(self.conn))

    def test_a_round_closes_the_row(self):
        self.queen(pid=4001, identity="win:1")
        self.bee()
        self.round()
        self.assertEqual(self.outcome("Cia"), "interrupted")

    def test_a_running_queen_keeps_the_row(self):
        self.queen(status="running", pid=4001, identity="win:1")
        self.world[4001] = "win:1"
        self.bee()
        self.assertEqual(self.close(), 0)
        self.assertEqual(self.open_names(), ["Cia"])

    def test_an_active_queen_row_keeps_the_row_even_without_process(self):
        self.queen(status="running", pid=4001, identity="win:1")
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_a_living_queen_process_keeps_the_row(self):
        self.queen(pid=4001, identity="win:1")
        self.world[4001] = "win:1"
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_neither_pid_nor_identity_keeps_the_row(self):
        self.queen()
        self.bee()
        self.assertEqual(self.close(), 0)
        self.assertEqual(self.open_names(), ["Cia"])

    def test_no_matching_queen_row_keeps_the_row(self):
        self.queen(name="Other-1", pid=4001, identity="win:1")
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_an_empty_queen_name_keeps_the_row(self):
        self.queen(pid=4001, identity="win:1")
        self.bee(queen="")
        self.assertEqual(self.close(), 0)

    def test_a_queen_that_started_after_the_bee_does_not_count(self):
        self.queen(pid=4001, identity="win:1", started=5, ended=1)
        self.bee(started=30)
        self.assertEqual(self.close(), 0)

    def test_an_unreadable_identity_keeps_the_row(self):
        self.queen(pid=4001, identity="win:1")
        self.world[4001] = None   # alive, identity cannot be read
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_access_denied_without_recorded_identity_keeps_the_row(self):
        self.queen(pid=4001)
        self.world[4001] = None
        self.denied.add(4001)
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_a_live_runner_with_a_dead_cli_child_keeps_the_row(self):
        self.queen(pid=4001, identity="win:1", cli_pid=4002, cli_identity="win:2")
        self.world[4001] = "win:1"
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_a_dead_runner_with_a_live_cli_child_keeps_the_row(self):
        self.queen(pid=4001, identity="win:1", cli_pid=4002, cli_identity="win:2")
        self.world[4002] = "win:2"
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_both_processes_gone_closes_the_row(self):
        self.queen(pid=4001, identity="win:1", cli_pid=4002, cli_identity="win:2")
        self.bee()
        self.assertEqual(self.close(), 1)

    def test_a_reused_pid_with_another_identity_counts_as_absent(self):
        self.queen(pid=4001, identity="win:1")
        self.world[4001] = "win:99"
        self.bee()
        self.assertEqual(self.close(), 1)

    def test_a_reused_pid_without_identity_is_absent_when_the_start_time_proves_it(self):
        self.queen(pid=4001, ended=10)
        self.world[4001] = "win:5"
        self.start_times[4001] = datetime.now()   # started after the Queen ended
        self.bee()
        self.assertEqual(self.close(), 1)

    def test_a_live_pid_without_identity_and_no_proof_of_reuse_keeps_the_row(self):
        self.queen(pid=4001, ended=10)
        self.world[4001] = "win:5"
        self.start_times[4001] = datetime.now() - timedelta(minutes=50)
        self.bee()
        self.assertEqual(self.close(), 0)

    def test_a_result_file_left_by_the_bee_still_closes_as_interrupted_without_attempt_or_state_change(self):
        self.queen(pid=4001, identity="win:1")
        self.bee()
        (self.wt / "result.md").write_text("done", encoding="utf-8")
        bees.set_package_state(self.conn, "testproj", REL, "P1", "working", "queen", "")
        attempts = self.conn.execute("SELECT COUNT(*) FROM bee_attempts").fetchone()[0]
        state = self.conn.execute("SELECT * FROM package_progress").fetchall()
        self.close()
        self.assertEqual(self.outcome("Cia"), "interrupted")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM bee_attempts").fetchone()[0], attempts)
        if True:
            self.assertEqual(self.conn.execute("SELECT * FROM package_progress").fetchall(), state)

    def has_table(self, name):
        return bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone())

    def test_a_stopping_bee_is_closed_as_stopped(self):
        self.queen(pid=4001, identity="win:1")
        self.bee()
        bees.stop_package(self.conn, "testproj", REL, "P1")
        self.assertEqual(self.close(), 1)
        self.assertEqual(self.outcome("Cia"), "stopped")
        self.assertEqual(self.open_names(), [])

    def test_a_queen_name_used_twice_is_matched_to_the_latest_start_before_the_bee(self):
        self.queen(pid=4001, identity="win:1", started=120, ended=100)   # an older run of the same name, long gone
        self.queen(status="running", pid=4005, identity="win:7", started=60)
        self.world[4005] = "win:7"
        self.bee(started=30)
        self.assertEqual(self.close(), 0)   # matched to the running one
        self.conn.execute("DELETE FROM agents WHERE pid=4005")
        self.world.pop(4005)
        self.queen(pid=4005, identity="win:7", started=60, ended=20)
        self.assertEqual(self.close(), 1)

    def test_the_six_open_rows_are_freed_and_a_running_queen_keeps_hers(self):
        names = {"Queen-1": "Bee-1", "Queen-2": "Bee-2", "Queen-3": "Bee-4", "Queen-4": "Bee-5", "Queen-5": "Bee-6"}
        for i, queen in enumerate(names):
            self.queen(name=queen, pid=5000 + i, identity="win:%d" % i)
        self.bee("Bee-1", "P1", "Queen-1")
        self.bee("Bee-2", "P2", "Queen-2")
        self.bee("Bee-3", "P1", "Queen-2")
        self.bee("Bee-4", "P2", "Queen-3")
        self.bee("Bee-5", "P1", "Queen-4")
        self.bee("Bee-6", "P2", "Queen-5")
        self.assertEqual(self.close(), 6)
        self.assertEqual(self.open_names(), [])
        # again, with one Queen still running
        for n in ("Bee-1", "Bee-2", "Bee-3", "Bee-4", "Bee-5", "Bee-6"):
            self.conn.execute("DELETE FROM embedded_bees WHERE name=?", (n,))
        self.conn.execute("UPDATE agents SET status='running', ended_at=NULL WHERE name='Queen-5'")
        self.world[5004] = "win:4"
        for n, p, q in (("Bee-1", "P1", "Queen-1"), ("Bee-2", "P2", "Queen-2"), ("Bee-3", "P1", "Queen-2"), ("Bee-4", "P2", "Queen-3"), ("Bee-5", "P1", "Queen-4"), ("Bee-6", "P2", "Queen-5")):
            self.bee(n, p, q)
        self.assertEqual(self.close(), 5)
        self.assertEqual(self.open_names(), ["Bee-6"])

    def test_a_later_queen_can_report_the_package_as_succeeded_after_the_close(self):
        self.queen(pid=4001, identity="win:1")
        self.bee()
        self.close()
        self.queen("Queen-B", status="running", pid=os.getpid(), started=1)
        args = self.args("succeeded", attempt="Cia")
        with self.cli_as("Queen-B"):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0)


class OwnQueenResultTests(EndCase):
    def setUp(self):
        super().setUp()
        self.queen(status="running", pid=os.getpid(), started=60)
        self.bee(started=30)

    def run_result(self, outcome, evidence=""):
        with self.cli_as("Queen-A"):
            return cli.cmd_bee(self.args(outcome, evidence=evidence), self.conn)

    def result_file(self, age_minutes=0, name="result.md"):
        f = self.wt / name
        f.write_text("done", encoding="utf-8")
        stamp = (datetime.now() - timedelta(minutes=age_minutes)).timestamp()
        os.utime(f, (stamp, stamp))
        return str(f)

    def test_succeeded_with_a_fresh_result_file_is_accepted_and_frees_the_place(self):
        self.assertEqual(self.run_result("succeeded", self.result_file()), 0)
        self.assertEqual(self.open_names(), [])
        self.assertEqual(bees.free_bee_places(self.env(), REL, 5), 3)

    def test_failed_with_a_fresh_result_file_is_accepted(self):
        args = self.args("failed", evidence=self.result_file())
        args.error = "boom"
        with self.cli_as("Queen-A"):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0)
        self.assertEqual(self.open_names(), [])

    def test_a_bee_of_an_earlier_run_of_the_same_queen_name_is_not_taken_as_hers(self):
        self.conn.execute("DELETE FROM embedded_bees")
        self.bee("Old", started=90)   # started before this run of the Queen name: it belongs to an earlier run
        self.assertIn("no matching agent row", self.run_result("succeeded", self.result_file()))
        self.assertEqual(self.open_names(), ["Old"])

    def test_without_a_file_it_is_refused(self):
        self.assertIn("--ended-evidence", self.run_result("succeeded"))
        self.assertEqual(self.open_names(), ["Cia"])

    def test_a_file_older_than_the_bee_is_refused(self):
        self.assertIn("before", self.run_result("succeeded", self.result_file(age_minutes=45)))
        self.assertEqual(self.open_names(), ["Cia"])

    def test_a_missing_file_is_refused(self):
        self.assertIn("does not exist", self.run_result("succeeded", str(self.wt / "missing.md")))

    def test_interrupted_is_accepted_without_a_file(self):
        self.assertEqual(self.run_result("interrupted"), 0)
        self.assertEqual(self.open_names(), [])

    def test_interrupted_for_a_stopping_bee_closes_it_as_stopped(self):
        bees.stop_package(self.conn, "testproj", REL, "P1")
        self.assertEqual(self.run_result("interrupted"), 0)
        self.assertEqual(self.outcome("Cia"), "stopped")


class TextTests(EndCase):
    def test_the_queen_role_text_describes_the_rules(self):
        text = (Path(roles.__file__).resolve().parent.parent / "roles" / "queen.md").read_text(encoding="utf-8")
        for phrase in ("--ended-evidence", "its Queen has ended", "your own subagent Agent", "closes its row", "bzz agent result --outcome succeeded"):
            self.assertTrue(phrase in text, phrase)

    def test_the_open_bees_text_describes_the_rules(self):
        self.queen(pid=4001, identity="win:1")
        self.queen("Queen-B", status="running", pid=os.getpid(), started=1)
        self.world[4001] = "win:1"
        self.bee()
        text = bees.open_bees_text(self.conn, "testproj", REL, runtime.get_agent(self.conn, "testproj", "Queen-B"))
        self.assertIn("Queen-A", text)
        self.assertTrue("closes its row" in text)
        self.assertIn("--outcome succeeded", text)
        self.assertIn("result file", text)


if __name__ == "__main__":
    unittest.main()
