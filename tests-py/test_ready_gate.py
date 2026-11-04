"""Tests of the ready gate: `bzz ready` of a Queen needs the requirement list (`- <id>: done | <evidence>` under '## Completeness' in the state
file) and a passed result of a fresh check agent. No real agent is started: the check agent runner is replaced."""
import argparse
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from bzztest import REL, RoundCase, runtime
from haivequeen import cli, readygate, roles

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

    def test_owner_check_does_not_block(self):
        """A requirement the plan names as an owner check is listed separately."""
        plan = PLAN_BODY.replace("third thing", "third thing (owner check)")
        res = self.check(completeness(*FULL[:2]), plan=plan)
        self.assertEqual(res.blocking, [])
        self.assertEqual(res.owner, ["G-3"])

    def test_set_aside_does_not_block(self):
        """An active decision naming the id sets it aside; a superseded one does not."""
        decision = {"id": 1, "text": "G-3 is set aside, not needed", "item": "G-3", "plan_path": REL, "status": "active", "source": "owner"}
        res = self.check(completeness(*FULL[:2]), decisions=[decision])
        self.assertEqual(res.blocking, [])
        self.assertEqual(res.aside, ["G-3"])
        res = self.check(completeness(*FULL[:2]), decisions=[dict(decision, status="superseded")])
        self.assertEqual([i for i, _ in res.blocking], ["G-3"])

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
        self.assertEqual(readygate.parse_verdict("no verdict")[0], "unavailable")

    def test_role_file(self):
        """The role file exists, names the verdict lines and holds no user specific value."""
        text = roles.role_text("ready-check")
        self.assertIn("READY-CHECK: PASSED", text)
        self.assertIn("READY-CHECK: FAILED", text)
        self.assertNotIn("c:\users", text.lower())


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
        text = (self.repo / REL).read_text(encoding="utf-8").replace("second thing", "second thing (owner check)")
        (self.repo / REL).write_text(text, encoding="utf-8")
        runtime.add_decision(self.conn, "testproj", "owner", "G-3 is set aside", plan=REL, item="G-3")
        self.state(FULL[0])
        with self.runner("passed"):
            code, res = self.ready()
        self.assertEqual(code, 0, res)
        self.assertIn("G-2", self.requests[0]["prompt"])

    def test_default_runner_without_start_is_unavailable(self):
        """The real runner reports unavailable when no agent can be started."""
        self.state(*FULL)
        with mock.patch("haivequeen.loop._do_start", return_value=0):
            verdict, text = readygate.start_check_agent(self.env(), self.queen, {"prompt": "x", "plan": REL, "cwd": str(self.wt)}, sleep=lambda s: None)
        self.assertEqual(verdict, "unavailable")


if __name__ == "__main__":
    unittest.main()
