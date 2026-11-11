"""Tests of the ready gate: `bzz ready` of a Queen needs the requirement list (`- <id>: done | <evidence>` under '## Completeness' in the state
file) and a passed result of a fresh check agent. No real agent is started: the check agent runner is replaced."""
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import cli, plans, readygate, roles

PLAN_BODY = "\n# Dummy\n\n## Requirements\n\n- G-1: first thing\n- G-2: second thing\n- G-3: third thing\n"
STATE = REL.replace("DUMMY-PLAN.md", "DUMMY-STATE.md")


def completeness(*lines):
    """A state file with the requirement section the Queen writes and a completeness section."""
    return "# State\n\n## Requirements\n\n- G-1: first thing\n- G-2: second thing\n- G-3: third thing\n\n## Completeness\n\n" + "\n".join(lines) + "\n"


FULL = ("- G-1: done | haivequeen/a.py:10", "- G-2: done | commit abc1234", "- G-3: done | tests-py/test_a.py")


class ListTests(unittest.TestCase):
    """The evaluation of the list without any database."""

    def check(self, state, plan=PLAN_BODY, decisions=()):
        """Evaluate a state file against a plan text."""
        return readygate.evaluate(plan, state, list(decisions))

    def test_complete_list_is_accepted(self):
        """Every id with status done and evidence passes."""
        res = self.check(completeness(*FULL))
        self.assertEqual(res.blocking, [])
        self.assertFalse(res.no_ids)

    def test_missing_id_is_refused(self):
        """An id absent from the list blocks."""
        res = self.check(completeness(*FULL[:2]))
        self.assertEqual([i for i, _ in res.blocking], ["G-3"])

    def test_missing_evidence_is_refused(self):
        """Status done without evidence blocks, with and without the bar."""
        for line in ("- G-3: done", "- G-3: done |", "- G-3: done |   "):
            res = self.check(completeness(*FULL[:2], line))
            self.assertEqual([i for i, _ in res.blocking], ["G-3"], line)

    def test_open_and_partial_are_refused(self):
        """Status open, partial and teilweise block even with evidence."""
        for status in ("open", "partial", "teilweise", "offen"):
            res = self.check(completeness(*FULL[:2], "- G-3: %s | something" % status))
            self.assertEqual([i for i, _ in res.blocking], ["G-3"], status)

    def test_repeated_id_is_refused(self):
        """An id listed twice blocks."""
        res = self.check(completeness(*FULL, "- G-1: done | again"))
        self.assertEqual([i for i, _ in res.blocking], ["G-1"])

    def test_count_line_does_not_decide(self):
        """The old count line says nothing: with it and without the list the gate refuses."""
        res = self.check("# State\n\nOffene Anforderungen: 0\n")
        self.assertEqual(sorted(i for i, _ in res.blocking), ["G-1", "G-2", "G-3"])

    def test_owner_check_words_do_not_exempt(self):
        """Words in the requirement do not exempt it from evidence."""
        plan = PLAN_BODY.replace("third thing", "third thing (owner check)")
        res = self.check(completeness(*FULL[:2]), plan=plan)
        self.assertEqual([i for i, _ in res.blocking], ["G-3"])
        self.assertEqual(res.owner, [])

    def test_set_aside_does_not_block(self):
        """An active decision naming the id sets it aside; a superseded one does not."""
        decision = {"id": 1, "text": "G-3 is set aside, not needed", "item": "G-3", "plan_path": REL, "status": "active", "source": "owner"}
        res = self.check(completeness(*FULL[:2]), decisions=[decision])
        self.assertEqual(res.blocking, [])
        self.assertEqual(res.aside, ["G-3"])
        res = self.check(completeness(*FULL[:2]), decisions=[dict(decision, status="superseded")])
        self.assertEqual([i for i, _ in res.blocking], ["G-3"])

    def test_decision_text_mention_does_not_set_aside(self):
        decision = {"item": "other", "text": "G-3 is discussed", "status": "active"}
        res = self.check(completeness(*FULL[:2]), decisions=[decision])
        self.assertEqual([i for i, _ in res.blocking], ["G-3"])

    def test_item_of_several_ids_applies_to_each(self):
        """A decision whose item names two ids sets both aside; an item naming other ids sets none."""
        decision = {"item": "G-2, G-3", "text": "set aside", "status": "active"}
        res = self.check(completeness(FULL[0]), decisions=[decision])
        self.assertEqual((res.blocking, sorted(res.aside)), ([], ["G-2", "G-3"]))
        other = {"item": "G-22,G-33", "text": "set aside", "status": "active"}
        res = self.check(completeness(FULL[0]), decisions=[other])
        self.assertEqual([i for i, _ in res.blocking], ["G-2", "G-3"])

    def test_plan_without_ids_is_refused(self):
        """No requirement id anywhere: refused with the hint that requirements need ids."""
        res = readygate.evaluate("# Plan\n\nDo things.\n", "# State\n", [])
        self.assertTrue(res.no_ids)
        self.assertIn("need ids", readygate.refusal_text(res))

    def test_ids_of_plan_and_state_both_count(self):
        """An id only the plan names, and one only the Queen's list names, must both be done."""
        state = "# State\n\n## Requirements\n\n- Q-1: queen's own\n\n## Completeness\n\n- G-1: done | x\n- G-2: done | y\n- G-3: done | z\n"
        res = self.check(state)
        self.assertEqual([i for i, _ in res.blocking], ["Q-1"])

    def test_verdict_parsing(self):
        """The verdict is the last READY-CHECK line; points follow it."""
        self.assertEqual(readygate.parse_verdict("x\nREADY-CHECK: PASSED\n"), ("passed", ""))
        verdict, points = readygate.parse_verdict("READY-CHECK: PASSED\nmore\nREADY-CHECK: FAILED\n- G-2 not in code\n")
        self.assertEqual((verdict, points), ("failed", "- G-2 not in code"))
        self.assertEqual(readygate.parse_verdict("READY-CHECK: FAILED - G-2 missing"), ("failed", "- G-2 missing"))
        self.assertEqual(readygate.parse_verdict("no verdict")[0], "unavailable")

    def test_role_file(self):
        """The role file exists, names the verdict lines and holds no user specific value."""
        text = roles.role_text("ready-check")
        self.assertIn("READY-CHECK: PASSED", text)
        self.assertIn("READY-CHECK: FAILED", text)
        self.assertNotIn("users\\", text.lower())


class GateTests(RoundCase):
    """`bzz ready` of a Queen, end to end with a fake check agent."""

    def setUp(self):
        """A running Queen of the dummy plan."""
        super().setUp()
        self.plan("READY", [{"id": "P1", "title": "First"}])
        (self.repo / REL).write_text((self.repo / REL).read_text(encoding="utf-8") + PLAN_BODY, encoding="utf-8")
        self.round()
        self.queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        self.requests = []
        self.submit()

    def submit(self):
        """The user submits the plan as it is in the file now."""
        runtime.record_submission(self.conn, "testproj", REL, (self.repo / REL).read_text(encoding="utf-8"))

    def state(self, *lines):
        """Write the Queen's state file beside the plan."""
        (self.repo / STATE).write_text(completeness(*lines), encoding="utf-8")

    def runner(self, verdict, text=""):
        """A fake check agent runner that records its request."""
        def run(request):
            self.requests.append(request)
            return verdict, text
        return mock.patch.object(readygate, "CHECK_RUNNER", run)

    def ready(self):
        """Call bzz ready as the Queen; returns (exit code, json output)."""
        saved = os.environ.get("BZZ_AGENT")
        os.environ["BZZ_AGENT"] = self.queen["name"]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_ready(argparse.Namespace(project="testproj", branch="branch", tip="abc", plan=REL, note=""), self.conn)
            return code, json.loads(buf.getvalue())
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    def queued(self):
        """Rows of the integration queue."""
        return self.conn.execute("SELECT * FROM queue WHERE project='testproj'").fetchall()

    def git_repo(self, folder):
        """Make folder a Git repository with one commit; returns the HEAD commit."""
        def git(*args):
            return subprocess.run(["git", "-C", str(folder)] + list(args), check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("add", "-A")
        git("commit", "-qm", "first", "--allow-empty")
        return git("rev-parse", "HEAD")

    def release(self, text, plan=None, item="owner-release"):
        """File an owner decision for the plan."""
        runtime.add_decision(self.conn, "testproj", "owner", text, plan=REL if plan is None else plan, item=item)

    def test_latest_submission_is_the_reference_of_both_checks(self):
        """Submitted, changed by the user in two requirements, submitted again: the list check and the check agent use the latest submission."""
        text = (self.repo / REL).read_text(encoding="utf-8")
        (self.repo / REL).write_text(text.replace("third thing", "third thing, now different").replace("- G-2: second thing\n", "- G-4: fourth thing\n"), encoding="utf-8")
        self.state(*FULL)
        with self.runner("passed"):
            code, res = self.ready()   # the first submission is the reference: G-2 and the old text of G-3
        self.assertEqual(code, 0, res)
        self.assertIn("G-2: second thing", self.requests[-1]["prompt"])
        self.assertIn("G-3: third thing\n", self.requests[-1]["prompt"] + "\n")
        self.submit()
        self.requests.clear()
        self.state(*FULL)
        with self.runner("passed"):
            code, res = self.ready()   # the second submission: G-4 is required by the list check
        self.assertEqual(code, 1)
        self.assertIn("G-4", res["error"])
        self.assertEqual(self.requests, [])
        self.state(*FULL[0:1], "- G-3: done | t", "- G-4: done | t", "- G-2: done | t")
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 0, res)
        prompt = self.requests[-1]["prompt"]
        self.assertIn("G-3: third thing, now different", prompt)
        self.assertIn("G-4: fourth thing", prompt)
        self.assertNotIn("G-3: third thing\n", prompt + "\n")

    def test_new_generation_does_not_reuse_submission(self):
        runtime.reset_cut_assignments(self.conn, "testproj", REL)
        self.assertIsNone(runtime.get_submission(self.conn, "testproj", REL))

    def test_status_line_set_to_sent_in_the_file_is_a_submission(self):
        """The user sets the status line of the plan to SENT in the file: the next sync records that version as the submission."""
        path = self.repo / REL
        text = path.read_text(encoding="utf-8")
        self.conn.execute("DELETE FROM kv WHERE key LIKE 'submission:%'")
        self.assertIsNone(runtime.get_submission(self.conn, "testproj", REL))
        path.write_text(re.sub(r'(?m)^status = ".*"$', 'status = "SENT"', text).replace("- G-3: third thing", "- G-3: third thing, sent"), encoding="utf-8")
        runtime.sync_plans(self.conn, "testproj", workspace=self.repo)
        self.assertIn("G-3: third thing, sent", runtime.get_submission(self.conn, "testproj", REL))

    def test_unknown_submission_stops_the_ready_check(self):
        """Without a submission record no other version takes its place: understandable message, no check agent."""
        self.conn.execute("DELETE FROM kv WHERE key LIKE 'submission:%'")
        self.state(*FULL)
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("submitted again", res["error"])
        self.assertEqual((self.requests, self.queued()), ([], []))

    def test_check_agent_gets_the_decisions_of_owner_and_coordinator(self):
        """The text of an owner decision and of a coordinator decision reaches the check agent; one with an item of two ids sets both aside."""
        runtime.add_decision(self.conn, "testproj", "owner", "owner says alpha", plan=REL, item="G-1")
        runtime.add_decision(self.conn, "testproj", "coordinator", "coordinator says beta", plan=REL, item="G-2,G-3")
        self.state(*FULL[:1])
        with self.runner("passed"):
            code, res = self.ready()   # G-2 and G-3 are not listed: the coordinator decision names both, as before only a named id is set aside
        self.assertEqual(code, 0, res)
        prompt = self.requests[-1]["prompt"]
        self.assertIn("owner says alpha", prompt)
        self.assertIn("coordinator says beta", prompt)

    def test_owner_release_for_the_head_commit_accepts_without_the_check_agent(self):
        """An active owner-release of this plan naming the HEAD commit (full or 7 characters) accepts; the list is still required."""
        folder = self.queen.get("cwd") or str(self.repo)
        head = self.git_repo(folder)
        for named in (head, head[:7]):
            self.release("released for commit %s" % named)
            self.state(*FULL)
            with self.runner("failed", "must not be asked"):
                code, res = self.ready()
            self.assertEqual(code, 0, res)
            self.assertEqual(self.requests, [])
            self.conn.execute("DELETE FROM decisions")
            self.conn.execute("DELETE FROM queue")
            self.conn.commit()
        self.release("released for any commit")
        with self.runner("failed", "must not be asked"):
            code, res = self.ready()
        self.assertEqual((code, self.requests), (0, []))
        self.conn.execute("DELETE FROM decisions")
        self.conn.commit()
        self.release("released for any commit")
        self.state(*FULL[:2])   # G-3 missing: the list stays mandatory
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("G-3", res["error"])

    def test_owner_release_of_another_plan_or_commit_takes_the_normal_way(self):
        """A release for another plan, another commit, another item or a short id under 7 characters does not skip the check agent."""
        folder = self.queen.get("cwd") or str(self.repo)
        head = self.git_repo(folder)
        other = ("0" if head[0] != "0" else "1") * 40
        self.state(*FULL)
        for plan, text, item in (("plans/OTHER-PLAN.md", "any commit", "owner-release"), (REL, "released for commit %s" % other, "owner-release"),
                                 (REL, "released for commit %s" % head[:6], "owner-release"), (REL, "any commit", "something-else")):
            self.release(text, plan=plan, item=item)
            self.requests.clear()
            with self.runner("failed", "- G-2 missing"):
                code, res = self.ready()
            self.assertEqual(code, 1, (plan, text, item))
            self.assertEqual(len(self.requests), 1, (plan, text, item))
            self.conn.execute("DELETE FROM decisions")
            self.conn.commit()

    def test_list_refusal_starts_no_check_agent(self):
        """A bad list is refused with the ids and the check agent is not asked."""
        self.state(FULL[0], "- G-2: partial | half", "- G-3: done")
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("G-2", res["error"])
        self.assertIn("G-3", res["error"])
        self.assertNotIn("G-1", res["error"])
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.queued()), 0)

    def test_no_state_file_is_refused(self):
        """Without a state file every id is missing."""
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("G-1", res["error"])

    def test_plan_without_ids_is_refused(self):
        """A plan without requirement ids is refused with the hint."""
        (self.repo / REL).write_text((self.repo / REL).read_text(encoding="utf-8").replace(PLAN_BODY, "\nno ids here\n"), encoding="utf-8")
        self.submit()
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("need ids", res["error"])

    def test_passed_result_is_accepted(self):
        """Complete list and a passed check agent: the branch is registered."""
        self.state(*FULL)
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual((code, res["ok"]), (0, True))
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertIn("DUMMY-STATE.md", request["prompt"])
        self.assertIn("DUMMY-PLAN.md", request["prompt"])
        self.assertEqual(len(self.queued()), 1)

    def test_check_agent_is_told_the_language(self):
        """The ready-check prompt names English by default and the configured language otherwise."""
        self.state(*FULL)
        with self.runner("passed"):
            self.ready()
        self.assertIn("state file entries) in English", self.requests[0]["prompt"])

    def test_check_agent_is_told_the_configured_language(self):
        """A language setting reaches the ready-check prompt."""
        self.state(*FULL)
        with self.runner("passed"), mock.patch("haivequeen.config.load_config", return_value=self.machine | {"language": "Deutsch"}):
            self.ready()
        self.assertIn("state file entries) in Deutsch", self.requests[0]["prompt"])

    def test_failed_result_returns_missing_points(self):
        """A failed check agent sends its points back to the Queen and registers nothing."""
        self.state(*FULL)
        with self.runner("failed", "- G-2: the code does not do it"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertIn("G-2: the code does not do it", res["error"])
        self.assertEqual(len(self.queued()), 0)

    def test_unavailable_agent_waits(self):
        """When the check agent cannot run, ready registers nothing and says it waits; the next call runs it again."""
        self.state(*FULL)
        with self.runner("unavailable", "provider limit"):
            code, res = self.ready()
        self.assertEqual(code, 1)
        self.assertTrue(res.get("waiting"))
        self.assertEqual(len(self.queued()), 0)
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 0)

    def test_owner_check_and_set_aside_do_not_block(self):
        """An owner check of the plan and a set aside decision let the branch pass."""
        text = (self.repo / REL).read_text(encoding="utf-8").replace('[[packages]]', '[[packages]]\nverify = "owner"\nrequirements = ["G-2"]', 1)
        (self.repo / REL).write_text(text, encoding="utf-8")
        runtime.save_cut_assignments(self.conn, "testproj", REL, plans.parse_plan_text(text).packages, text, replace=True)
        runtime.add_decision(self.conn, "testproj", "owner", "G-3 is set aside", plan=REL, item="G-3")
        self.state(FULL[0])
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 0, res)
        self.assertIn("G-2", self.requests[0]["prompt"])

    def test_runner_uses_free_provider_and_configured_class(self):
        env = self.env()
        env.settings["readyCheckClass"] = "heavy"
        env.providers = [dict(p, name=name, endpoint="", classes={"heavy": {"model": "m"}}) for p, name in zip([env.providers[0]] * 2, ("busy", "free"))]
        chosen = []
        def start(ctx, action, forced_pick):
            chosen.append((action["type"], action["class"]))
            ctx["state"]["names"].append("check")
            return 1
        with mock.patch("haivequeen.scheduler.provider_free", return_value={"busy": 0, "free": 1}), mock.patch("haivequeen.usage.limit_windows", return_value=[]), mock.patch("haivequeen.loop._do_start", side_effect=start), mock.patch.object(runtime, "get_agent", return_value={"status": "done"}), mock.patch.object(readygate, "parse_verdict", return_value=("passed", "")):
            readygate.start_check_agent(env, self.queen, {"prompt": "x", "plan": REL, "cwd": str(self.wt)}, sleep=lambda _: None)
        self.assertEqual(chosen, [("free", "heavy")])

    def test_runner_waits_for_row_and_reads_codex_failed_verdict(self):
        env = self.env()
        def start(ctx, action, forced_pick):
            ctx["state"]["names"].append("check")
            return 1
        calls = []
        def pause(_):
            calls.append(1)
            if len(calls) == 2:
                folder = env.run_dir("check")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "final.txt").write_text("READY-CHECK: FAILED\n- G-2 missing\n", encoding="utf-8")
        with mock.patch("haivequeen.loop._do_start", side_effect=start), mock.patch.object(runtime, "get_agent", side_effect=[None, {"status": "done"}, {"status": "done"}]), mock.patch("haivequeen.agent_process.is_live", side_effect=[True, False]):
            verdict = readygate.start_check_agent(env, self.queen, {"prompt": "x", "plan": REL, "cwd": str(self.wt)}, sleep=pause)
        self.assertEqual(verdict, ("failed", "- G-2 missing"))
        self.assertEqual(len(calls), 2)

    def test_default_runner_without_start_is_unavailable(self):
        """The real runner reports unavailable when no agent can be started."""
        self.state(*FULL)
        with mock.patch("haivequeen.loop._do_start", return_value=0):
            verdict, text = readygate.start_check_agent(self.env(), self.queen, {"prompt": "x", "plan": REL, "cwd": str(self.wt)}, sleep=lambda s: None)
        self.assertEqual(verdict, "unavailable")

    def test_ready_check_uses_configured_class(self):
        env = self.env()
        env.settings["readyCheckClass"] = "heavy"
        with mock.patch("haivequeen.scheduler.select_provider", return_value=None) as route:
            verdict, text = readygate.start_check_agent(env, self.queen, {"prompt": "x", "plan": REL, "cwd": str(self.wt)})
        self.assertEqual(route.call_args.args[1], "heavy")
        self.assertIn("heavy", text)


if __name__ == "__main__":
    unittest.main()
