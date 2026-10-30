"""Tests of two fixes: the package lock loop (a verify answer without a count line is read from report.md, counts once as a failure, and a
released package is not locked again in the same round) and the skipped release (only the release phase registers a branch as ready, and only
after a whole-plan verify with 'Offene Anforderungen: 0')."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from bzztest import REL, RoundCase, db, runtime
from haivequeen import agent_runner as ar
from haivequeen import cli, routine, triage
from test_agent_runner import RunnerBase, claude_stream


class FinalAnswerTest(RoundCase):
    """The answer of a verify agent is final.txt, completed by the report the agent wrote into its run folder."""

    def setUp(self):
        """One finished verify agent of package P1."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "pilot"}])
        self.agent("DUMMY-P1-verify", package="P1", phase="verify", started=10, ended=5)

    def report(self, text):
        """Write report.md into the run folder of the verify agent."""
        d = self.env().run_dir("DUMMY-P1-verify")
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text(text, encoding="utf-8")

    def test_report_supplies_missing_count_line(self):
        """final.txt holds only a first message; the count line in report.md decides (0 open requirements)."""
        self.finish("DUMMY-P1-verify", "Ich lese den Auftrag vollstaendig und pruefe die Vorgaben.")
        self.report("- Punkt 1 erledigt: README.md:64\n\nOffene Anforderungen: 0\n")
        env = self.env()
        self.assertEqual(routine.verify_open_count(env, "DUMMY-P1-verify"), 0)
        self.assertTrue(routine.verify_zero(env, "DUMMY-P1-verify"))

    def test_count_line_of_final_text_wins(self):
        """A count line in final.txt is used as it stands, the report is not consulted."""
        self.finish("DUMMY-P1-verify", "Offene Anforderungen: 2")
        self.report("Offene Anforderungen: 0")
        self.assertEqual(routine.verify_open_count(self.env(), "DUMMY-P1-verify"), 2)

    def test_report_without_count_line_changes_nothing(self):
        """Neither file has a count line: the answer stays without one (-1)."""
        self.finish("DUMMY-P1-verify", "Layout: split the file")
        self.report("Notes only.")
        self.assertEqual(routine.verify_open_count(self.env(), "DUMMY-P1-verify"), -1)


class CodexFinalMessageTest(unittest.TestCase):
    """The final answer of a Codex run is its last non-empty agent message."""

    def test_last_message_is_final_and_empty_closer_is_ignored(self):
        """Two real messages and an empty closing one: the second real one is the final answer."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BZZ_DATA_DIR"] = str(Path(tmp) / "data")
            try:
                conn = db.connect(Path(tmp) / "data" / "o.db")
                runtime.upsert_agent(conn, "p", "X-1", {"status": "running"})
                (Path(tmp) / "run").mkdir()
                run = ar.AgentRun(conn, "p", "X-1", "codex", "codex", tmp, Path(tmp) / "run", sessions=tmp)
                with contextlib.redirect_stdout(io.StringIO()):
                    for text in ("I read the task first.", "Offene Anforderungen: 0", ""):
                        run.handle_event({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
                self.assertEqual(run.final, "Offene Anforderungen: 0")
                conn.close()
            finally:
                os.environ.pop("BZZ_DATA_DIR", None)


class LockLoopTest(RoundCase):
    """Verifies without a count line lock a package once; after the release it gets new attempts instead of being locked again at once."""

    def setUp(self):
        """K1 has three finished work/verify pairs; no verify answer carries a count line (and there is no report)."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "stubborn task"}])
        for i, n in enumerate(("", "-2", "-3")):
            t = 60 - 20 * i
            self.agent("DUMMY-K1-work" + n, package="K1", started=t, ended=t - 5)
            self.agent("DUMMY-K1-verify" + n, type="codex", provider="codex", package="K1", phase="verify", started=t - 10, ended=t - 15)
            self.finish("DUMMY-K1-verify" + n, "Layout: split the file")

    def locked(self):
        """True when package K1 is locked."""
        return [p for p in runtime.list_packages(self.conn, "testproj", REL) if p["id"] == "K1"][0]["locked"]

    def test_verify_without_count_line_is_one_failure_each(self):
        """Three verifies without a count line are three failures, one per verify, whatever the number of rounds."""
        env = self.env()
        self.assertEqual(routine.package_failures(env, routine.all_agents(env), REL, "K1"), 3)
        self.round()
        self.round()
        self.assertEqual(routine.package_failures(env, routine.all_agents(env), REL, "K1"), 3)

    # Verify old verify failures no longer trigger a package lock or triage agent.
    def test_old_verify_failures_do_not_lock_or_start_triage(self):
        """Repeated rounds keep the package unlocked and do not start triage."""
        self.round()
        self.assertFalse(self.locked())
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"])
        self.round()
        self.assertFalse(self.locked())
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj") if a["phase"] == "triage"])

class ReadyRefusalTest(RoundCase):
    """Only the release phase registers a branch as ready, and only after a whole-plan verify with 'Offene Anforderungen: 0'."""

    def setUp(self):
        """A plan with one package."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "pilot"}])

    def call_ready(self, agent):
        """Run `bzz ready` for an agent (BZZ_AGENT set as the agent runner does); returns (exit code, JSON answer)."""
        os.environ["BZZ_AGENT"] = agent
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cli.cmd_ready(argparse.Namespace(project="testproj", branch="b1", tip="abc", plan=REL, note="n"), self.conn)
        finally:
            os.environ.pop("BZZ_AGENT", None)
        return code, json.loads(buf.getvalue())

    def whole_verify(self, final):
        """A finished whole-plan verify (package ALL) with this answer."""
        self.agent("DUMMY-ALL-verify", package="ALL", phase="verify", started=20, ended=15)
        self.finish("DUMMY-ALL-verify", final)

    def test_work_agent_is_refused(self):
        """A work agent calling bzz ready is refused."""
        self.agent("DUMMY-P1-work", package="P1", phase="work", status="running")
        code, res = self.call_ready("DUMMY-P1-work")
        self.assertEqual(code, 1)
        self.assertFalse(res["ok"])
        self.assertIn("release phase", res["error"])

    def test_fix_of_blocked_row_may_register_again(self):
        """The work agent of a package FIX-<tip> (fix of a blocked queue row) may register its branch again."""
        self.agent("DUMMY-FIX-abc-work", package="FIX-abc", phase="work", status="running")
        code, res = self.call_ready("DUMMY-FIX-abc-work")
        self.assertEqual((code, res["ok"]), (0, True))

    def test_release_needs_whole_plan_verify_with_zero(self):
        """The release agent is refused without a whole-plan verify, and with one that reports open items; accepted after 'Offene Anforderungen: 0'."""
        self.agent("DUMMY-REL-release", package="REL", phase="release", status="running")
        self.assertEqual(self.call_ready("DUMMY-REL-release")[0], 1)
        self.whole_verify("Offene Anforderungen: 1")
        self.assertEqual(self.call_ready("DUMMY-REL-release")[0], 1)
        self.finish("DUMMY-ALL-verify", "Offene Anforderungen: 0")
        code, res = self.call_ready("DUMMY-REL-release")
        self.assertEqual((code, res["ok"]), (0, True))

    def test_caller_without_agent_name_is_not_judged(self):
        """Without BZZ_AGENT (a person, a test) bzz ready works as before."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.cmd_ready(argparse.Namespace(project="testproj", branch="b2", tip="abc", plan=REL, note="n"), self.conn)
        self.assertEqual(code, 0)


class AgentEnvironmentTest(RunnerBase):
    """The agent runner tells the agent's processes which agent they belong to."""

    def test_bzz_agent_is_set(self):
        """The CLI of the agent sees BZZ_AGENT with the agent name."""
        self.prepare("A-1")
        r = self.run_runner("A-1", "claude", claude_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.record()["bzz_agent"], "A-1")


if __name__ == "__main__":
    unittest.main()
