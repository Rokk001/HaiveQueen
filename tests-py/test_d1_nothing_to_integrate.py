"""A plan whose Queen reports completion and that has nothing left to integrate closes the normal way (check, owner check, DONE)."""
import argparse
import contextlib
import io
import json
import os
import subprocess
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import cli, db, hives, plans, readygate, runtime

BODY = "\n## Requirements\n\n- G-1: first thing\n"
STATE = REL.replace("DUMMY-PLAN.md", "DUMMY-STATE.md")
LIST = "# State\n\n## Requirements\n\n- G-1: first thing\n\n## Completeness\n\n- G-1: done | commit abc1234\n"


class NothingLeftCase(RoundCase):
    """A running Queen of the dummy plan in a real Git repository whose integration branch is main."""

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo)] + list(args), check=True, capture_output=True, text=True).stdout.strip()

    def setUp(self):
        super().setUp()
        self.plan("READY", [{"id": "P1", "title": "First"}])
        (self.repo / REL).write_text((self.repo / REL).read_text(encoding="utf-8") + BODY, encoding="utf-8")
        (self.repo / STATE).write_text(LIST, encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "-A")
        self.git("commit", "-qm", "first")
        self.round()
        self.queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        self.requests = []

    def runner(self, verdict, text=""):
        def run(request):
            self.requests.append(request)
            return verdict, text
        return mock.patch.object(readygate, "CHECK_RUNNER", run)

    def ready(self, tip="abc1234"):
        saved = os.environ.get("BZZ_AGENT")
        os.environ["BZZ_AGENT"] = self.queen["name"]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_ready(argparse.Namespace(project="testproj", branch="dummy", tip=tip, plan=REL, note=""), self.conn)
            return code, json.loads(buf.getvalue())
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    def rows(self):
        return self.conn.execute("SELECT * FROM queue WHERE project='testproj' ORDER BY id").fetchall()

    def queen_ends(self):
        self.finish(self.queen["name"], final="Open requirements: 0")

    def questions(self, item=None):
        return runtime.list_questions(self.conn, "testproj", plan=REL, item=item) if item else runtime.list_questions(self.conn, "testproj", plan=REL)

    def queens(self):
        return [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]

    def status(self):
        return plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status

    def answer_check(self, result):
        qid = self.questions("owner-check")[-1]["id"]
        db.answer_question(self.conn, qid, "x", "owner")
        self.conn.execute("UPDATE questions SET status='answered',owner_check_result=? WHERE id=?", (result, qid))

    def close_normally(self):
        """Ready, the Queen ends, the round asks the owner check; returns the owner check questions."""
        with self.runner("passed"):
            self.assertEqual(self.ready()[0], 0)
        self.queen_ends()
        self.round(alive=lambda pid: False)
        return self.questions("owner-check")


class CloseTests(NothingLeftCase):
    def assert_closes(self):
        count = len(self.queens())
        asked = self.close_normally()
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(len(self.queens()), count)
        self.assertNotEqual(self.status(), "DONE")
        self.answer_check("accepted")
        self.round(alive=lambda pid: False)
        self.assertEqual(self.status(), "DONE")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "done")
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(len(self.queens()), count)

    def test_no_branch_no_row(self):
        self.assert_closes()
        self.assertEqual(self.rows(), [])

    def test_owner_check_none_closes_after_the_check(self):
        with mock.patch.object(hives, "owner_check_wanted", return_value=False):
            with self.runner("passed"):
                self.assertEqual(self.ready()[0], 0)
            self.queen_ends()
            self.round(alive=lambda pid: False)
        self.assertEqual(self.questions("owner-check"), [])
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(self.status(), "DONE")

    def test_branch_fully_contained(self):
        self.git("branch", "dummy")
        self.assert_closes()
        self.assertEqual(self.rows(), [])

    def test_withdrawn_row_with_integrated_tip(self):
        tip = self.git("rev-parse", "HEAD")
        runtime.queue_upsert(self.conn, "testproj", "other", tip=tip, plan=REL, status="withdrawn", note="free text")
        self.assert_closes()
        self.assertEqual([r["status"] for r in self.rows()], ["withdrawn"])

    def test_ready_row_with_integrated_tip_is_withdrawn(self):
        tip = self.git("rev-parse", "HEAD")
        for status in ("ready", "blocked"):
            self.conn.execute("DELETE FROM queue")
            runtime.queue_upsert(self.conn, "testproj", "other", tip=tip, plan=REL, status=status)
            with self.runner("passed"):
                self.assertEqual(self.ready()[0], 0)
            self.assertEqual([r["status"] for r in self.rows()], ["withdrawn"])
            self.assertIn("already integrated", self.rows()[0]["note"])

    def test_branch_contained_whatever_the_row_says(self):
        self.git("branch", "dummy")
        tip = self.git("rev-parse", "HEAD")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=tip, plan=REL, status="blocked")
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual((code, res.get("nothing_to_integrate")), (0, True))

    def test_no_compile_check_build_or_deploy(self):
        with mock.patch("haivequeen.integration.compile_check_missing", return_value="never run") as missing, self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 0, res)
        missing.assert_not_called()
        self.assertEqual(self.rows(), [])


class NeverCloseTests(NothingLeftCase):
    def side_commit(self, branch):
        self.git("checkout", "-q", "-b", branch)
        (self.repo / (branch + ".txt")).write_text("x", encoding="utf-8")
        self.git("add", branch + ".txt")
        self.git("commit", "-qm", "work")
        tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return tip

    def test_branch_with_new_commit_never_closes_whatever_the_row_says(self):
        tip = self.side_commit("dummy")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=tip, plan=REL, status="integrated")
        self.assertFalse(hives.nothing_to_integrate(self.conn, self.env(), REL)[0])
        with self.runner("passed"):
            code, res = self.ready()
        self.assertNotIn("nothing_to_integrate", res)
        self.assertIsNone(hives.completion_report(self.conn, "testproj", REL))

    def test_integrated_row_cannot_close_unmerged_branch(self):
        tip = self.side_commit("dummy")
        row = runtime.queue_upsert(self.conn, "testproj", "dummy", tip=tip, plan=REL, status="integrated")
        self.queen_ends()
        for ready_id in (None, row["id"]):
            self.conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (ready_id, "testproj", REL))
            self.assertIsNone(hives.integrated_ready(self.conn, self.env(), hives.get(self.conn, "testproj", REL)))
            self.round(alive=lambda pid: False)
            self.assertNotEqual(self.status(), "DONE")
            self.assertEqual(self.questions("owner-check"), [])

    def test_integrated_row_closes_after_branch_is_merged(self):
        tip = self.side_commit("dummy")
        row = runtime.queue_upsert(self.conn, "testproj", "dummy", tip=tip, plan=REL, status="integrated")
        self.git("merge", "--no-edit", "dummy")
        self.queen_ends()
        self.assertIsNotNone(hives.integrated_ready(self.conn, self.env(), hives.get(self.conn, "testproj", REL)))
        self.conn.execute("UPDATE hives SET ready_id=? WHERE project=? AND plan_path=?", (row["id"], "testproj", REL))
        self.round(alive=lambda pid: False)
        self.assertEqual(len(self.questions("owner-check")), 1)
        self.answer_check("accepted")
        self.round(alive=lambda pid: False)
        self.assertEqual(self.status(), "DONE")

    def test_withdrawn_row_with_foreign_tip_does_not_count(self):
        tip = self.side_commit("side")
        runtime.queue_upsert(self.conn, "testproj", "side", tip=tip, plan=REL, status="withdrawn", note="already integrated")
        self.assertFalse(hives.nothing_to_integrate(self.conn, self.env(), REL)[0])

    def test_no_branch_row_tip_not_integrated(self):
        runtime.queue_upsert(self.conn, "testproj", "gone", tip="0123456789abcdef0123456789abcdef01234567", plan=REL, status="ready")
        self.assertFalse(hives.nothing_to_integrate(self.conn, self.env(), REL)[0])

    def test_integration_run_in_progress(self):
        self.git("branch", "dummy")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=self.git("rev-parse", "HEAD"), plan=REL, status="in_run")
        self.assertFalse(hives.nothing_to_integrate(self.conn, self.env(), REL)[0])

    def test_integrated_row_plan_closes_as_before(self):
        """An integrated row never needs a completion report."""
        self.git("branch", "dummy")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=self.git("rev-parse", "HEAD"), plan=REL, status="integrated")
        self.assertIsNone(hives.completion_report(self.conn, "testproj", REL))


class CheckTests(NothingLeftCase):
    def test_failed_check_in_ready_is_refused_without_report(self):
        with self.runner("failed", "point one"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("point one", res["error"])
        self.assertIsNone(hives.completion_report(self.conn, "testproj", REL))

    def test_check_that_cannot_start_is_repeated_by_the_round(self):
        with self.runner("unavailable", "no slot"):
            code, res = self.ready()
        self.assertEqual((code, res.get("waiting")), (1, True))
        self.queen_ends()
        count = len(self.queens())
        with self.runner("unavailable", "no slot"):
            self.round(alive=lambda pid: False)
        self.assertEqual(self.questions(), [])
        self.assertEqual(len(self.queens()), count)
        self.assertEqual(hives.completion_report(self.conn, "testproj", REL)["state"], "pending")
        with self.runner("passed"):
            self.round(alive=lambda pid: False)
        self.assertEqual(len(self.questions("owner-check")), 1)
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(len(self.queens()), count)

    def test_integrated_row_does_not_bypass_a_pending_completion_check(self):
        with self.runner("unavailable", "no slot"):
            self.assertEqual(self.ready()[1].get("waiting"), True)
        self.git("branch", "dummy")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=self.git("rev-parse", "HEAD"), plan=REL, status="integrated")
        self.queen_ends()
        with self.runner("unavailable", "no slot"):
            self.round(alive=lambda pid: False)
        self.assertEqual(self.questions("owner-check"), [])
        self.assertEqual(hives.completion_report(self.conn, "testproj", REL)["state"], "pending")
        with self.runner("passed"):
            self.round(alive=lambda pid: False)
        self.assertEqual(len(self.questions("owner-check")), 1)

    def test_integrated_row_with_failed_pending_check_reopens_work(self):
        with self.runner("unavailable", "no slot"):
            self.ready()
        self.git("branch", "dummy")
        runtime.queue_upsert(self.conn, "testproj", "dummy", tip=self.git("rev-parse", "HEAD"), plan=REL, status="integrated")
        self.queen_ends()
        count = len(self.queens())
        with self.runner("failed", "missing point"):
            self.round(alive=lambda pid: False)
        self.assertEqual(self.status(), "RUNNING")
        self.assertEqual(len(self.queens()), count + 1)
        self.assertEqual(self.questions("owner-check"), [])
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertIsNone(hives.integrated_ready(self.conn, self.env(), hives.get(self.conn, "testproj", REL)))

    def test_failed_check_after_the_queen_ended_starts_a_new_queen(self):
        with self.runner("unavailable", "no slot"):
            self.ready()
        self.queen_ends()
        count = len(self.queens())
        with self.runner("failed", "missing point"):
            self.round(alive=lambda pid: False)
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(self.status(), "RUNNING")
        self.assertEqual(len(self.queens()), count + 1)
        self.assertIsNone(hives.completion_report(self.conn, "testproj", REL))

    def test_rejection_sends_the_plan_back_without_a_loop(self):
        asked = self.close_normally()
        self.assertEqual(len(asked), 1)
        self.assertTrue(asked[0]["question"].startswith("Check the result of the plan"))
        count = len(self.queens())
        self.answer_check("rejected")
        self.round(alive=lambda pid: False)
        self.assertEqual(self.status(), "RUNNING")
        self.assertEqual(self.questions("queen-ended"), [])
        self.assertEqual(len(self.queens()), count + 1)
        self.assertIsNone(hives.completion_report(self.conn, "testproj", REL))
