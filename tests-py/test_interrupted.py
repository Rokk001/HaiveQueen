"""Tests of R1 (interrupted agent ends wait and retry, never count as failure): the kinds of interruption are recognised from error events and
stderr only, an interrupted end blocks and locks nothing and does not raise the class, and the task is tried again in the same phase after
its wait (a rate limit goes to a provider with room, otherwise it waits for the reset)."""
import json
import os
import unittest
from datetime import datetime, timedelta

from bzztest import REL, RoundCase
from haivequeen import interrupt, routine, runtime
from test_agent_runner import RunnerBase


def later(minutes):
    """ISO time `minutes` from now (negative: in the past)."""
    return (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


class ClassifyEndTest(unittest.TestCase):
    """Which ends count as interrupted."""

    def test_api_errors(self):
        """Rate limit, overloaded and 429 are rate limits, a 5xx status is an API error."""
        for text in ("Claude AI usage limit: rate limit reached", "API Error: overloaded_error", "HTTP 429 Too Many Requests"):
            self.assertEqual(interrupt.classify_end(True, [text]), interrupt.RATE_LIMIT, text)
        for text in ("API Error: 500 Internal server error", "502 Bad Gateway", "503 Service Unavailable"):
            self.assertEqual(interrupt.classify_end(True, [text]), interrupt.API, text)

    def test_connection_texts_in_error_event_or_stderr(self):
        """Each connection text of the plan counts, in an error event or in stderr."""
        for text in ("ECONNRESET", "ECONNABORTED", "ETIMEDOUT", "ENOTFOUND", "EAI_AGAIN", "connection reset by peer", "Connection refused",
                     "connection closed", "socket hang up", "fetch failed", "stream disconnected before completion", "error sending request for url"):
            self.assertEqual(interrupt.classify_end(True, [text]), interrupt.CONNECTION, text)
            self.assertEqual(interrupt.classify_end(True, [], stderr="x " + text), interrupt.CONNECTION, text)

    def test_clock_jump_with_failed_end(self):
        """A run during which the clock jumped and that then failed is interrupted; one that ended well stays done."""
        self.assertEqual(interrupt.classify_end(True, [], clock_jumped=True), interrupt.SLEEP)
        self.assertEqual(interrupt.classify_end(False, [], clock_jumped=True), "")

    def test_vanished_kinds(self):
        """A vanished runner process, a pane that never came up and an agent lost by a Queen restart are interrupted."""
        for last in ("runner process vanished (ended by executor or crashed)", "pane never started (no runner process)",
                     "process gone after orchestrator restart"):
            self.assertEqual(interrupt.classify_end(True, [], last_line=last), interrupt.VANISHED, last)

    def test_normal_failures_stay_normal(self):
        """Answer text that mentions network or 500, a 401 and a plain failure are no interruption (the answer text is never searched)."""
        self.assertEqual(interrupt.classify_end(True, ["401 Unauthorized"], last_line="boom"), "")
        self.assertEqual(interrupt.classify_end(True, [], stderr="I changed the network code, 500 lines", last_line="task failed"), "")
        self.assertEqual(interrupt.classify_end(True, ["error: tests failed"], last_line="no result event, exit 1"), "")

    def test_backoff(self):
        """1, 2, 4, 8 minutes, then every 15 minutes."""
        self.assertEqual([interrupt.backoff_minutes(n) for n in (1, 2, 3, 4, 5, 9)], [1, 2, 4, 8, 15, 15])


class InterruptedRetryTest(RoundCase):
    """An interrupted end waits, blocks nothing and is retried in the same phase."""

    def setUp(self):
        """Package R1 without dependencies."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "R1", "title": "first task", "class": "medium"}])

    def interrupted(self, name, phase="work", kind=interrupt.CONNECTION, retry="", provider="claude", started=30, ended=25, package="R1"):
        """A failed agent row that ended interrupted."""
        self.agent(name, package=package, phase=phase, status="failed", last_line="error: " + kind, type=provider, provider=provider,
                   started=started, ended=ended, interrupted=kind, retryAt=retry)

    def limits(self, **used):
        """Provider limits for the test runtime: name=(used percent, hours to reset)."""
        f = self.base / "limits.json"
        f.write_text(json.dumps({n: {"usedPercent": u, "resetHours": h} for n, (u, h) in used.items()}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(f)

    def test_waiting_retry_starts_nothing(self):
        """Until its time no agent of any phase starts for the package, and the brain is not asked about the failure."""
        self.interrupted("DUMMY-R1-work", retry=later(10))
        self.round()
        self.round()
        self.assertEqual(self.names(), ["DUMMY-R1-work"])

    def test_due_retry_starts_the_same_phase(self):
        """After its time the next agent is a work agent, not a verify."""
        self.interrupted("DUMMY-R1-work", retry=later(-1))
        self.round()
        self.assertEqual(self.ag("DUMMY-R1-work-2")["phase"], "work")
        self.assertNotIn("DUMMY-R1-verify", self.names())

    def test_waiting_retry_survives_a_restart(self):
        """The wait is stored in the database: new environments in a later round still hold it back, and release it when the time has come."""
        self.interrupted("DUMMY-R1-work", retry=later(10))
        self.round()
        self.assertEqual(self.names(), ["DUMMY-R1-work"])
        runtime.upsert_agent(self.conn, "testproj", "DUMMY-R1-work", {"retryAt": later(-1)})
        self.round()
        self.assertIn("DUMMY-R1-work-2", self.names())

    def test_interrupted_ends_are_no_failures(self):
        """Three interrupted work ends: no failure counted, no block, no lock, the class stays."""
        for n, name in enumerate(("DUMMY-R1-work", "DUMMY-R1-work-2", "DUMMY-R1-work-3")):
            self.interrupted(name, kind=interrupt.API, retry=later(-1), started=90 - 20 * n, ended=85 - 20 * n)
        env = self.env()
        agents = routine.all_agents(env)
        self.assertEqual(routine.package_failures(env, agents, REL, "R1"), 0)
        self.assertEqual(routine.blocked_tasks(agents), [])
        self.assertFalse(routine.package_locked(env, REL, "R1", agents))
        self.assertEqual(routine.start_class(env, "work", REL, "R1", "medium"), "medium")
        self.round()
        self.assertIn("DUMMY-R1-work-4", self.names())

    def test_verify_is_retried_after_its_wait(self):
        """A verify that ended interrupted is run again in the verify phase only after its wait."""
        self.agent("DUMMY-R1-work", package="R1", started=60, ended=50)
        self.interrupted("DUMMY-R1-verify", phase="verify", retry=later(10), started=45, ended=40)
        self.round()
        self.assertEqual(self.names(), ["DUMMY-R1-work", "DUMMY-R1-verify"])
        runtime.upsert_agent(self.conn, "testproj", "DUMMY-R1-verify", {"retryAt": later(-1)})
        self.round()
        self.assertEqual(self.ag("DUMMY-R1-verify-2")["phase"], "verify")

    def test_rate_limit_goes_to_the_provider_with_room(self):
        """Claude is at its weekly limit: the retry after the rate limit is started at once, at Codex."""
        self.limits(claude=(100, 48), codex=(10, 48))
        self.interrupted("DUMMY-R1-work", kind=interrupt.RATE_LIMIT)
        self.round()
        self.assertEqual(self.ag("DUMMY-R1-work-2")["provider"], "codex")

    def test_rate_limit_with_both_providers_at_limit_waits(self):
        """Both providers are at their limit: the retry waits for the reset (the later one), nothing starts."""
        self.limits(claude=(100, 2), codex=(100, 5))
        self.interrupted("DUMMY-R1-work", kind=interrupt.RATE_LIMIT, ended=1, started=10)
        self.round()
        self.assertEqual(self.names(), ["DUMMY-R1-work"])


class InterruptedIntegratorTest(RoundCase):
    """An integrator that ended interrupted gives its queue rows back, and the next integrator first aborts an unfinished merge."""

    def setUp(self):
        """One queue row taken by an integrator (in_run) and the plan of the project."""
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "R1", "title": "first task"}])
        runtime.queue_upsert(self.conn, "testproj", "feature/b1", "abc123456", REL, "in_run", "taken by the integrator")

    def integrator(self, status="failed", interrupted="vanished", last="runner process vanished (ended by executor or crashed)"):
        """The integrator agent row."""
        self.agent("INTEGRATOR-1029-1000", package="INT-10291000", plan_path="integration-queue", phase="integrate", status=status, last_line=last,
                   interrupted=interrupted, started=20, ended=15)

    def row_status(self):
        """Status word of the queue row."""
        return routine.queue_rows(self.env())["feature/b1"]["status"]

    def test_rows_go_back_to_waiting(self):
        """After an interrupted integrator the row is ready again."""
        self.integrator()
        routine.return_interrupted_rows(self.env())
        self.assertEqual(self.row_status(), "ready")

    def test_running_or_normally_failed_integrator_keeps_the_rows(self):
        """A running integrator and one that failed for a real reason keep their rows."""
        self.integrator(status="running", interrupted="", last="working")
        routine.return_interrupted_rows(self.env())
        self.assertEqual(self.row_status(), "in Lauf")
        self.integrator(status="failed", interrupted="", last="build failed")
        routine.return_interrupted_rows(self.env())
        self.assertEqual(self.row_status(), "in Lauf")

    def test_next_integrator_aborts_an_unfinished_merge(self):
        """The round gives the row back and starts the next integrator, whose prompt starts with aborting an unfinished merge."""
        self.integrator()
        self.round()
        new = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "integrate" and a["name"] != "INTEGRATOR-1029-1000"]
        self.assertEqual(len(new), 1)
        self.assertIn("merge --abort", (self.env().run_dir(new[0]["name"]) / "prompt.md").read_text(encoding="utf-8"))


class RunnerEndTest(RunnerBase):
    """The agent runner marks an interrupted end in the agent row and leaves normal ends alone."""

    def run_stream(self, name, provider, events, exit_code=1):
        """Run the runner with these events (a list of dicts) and return the agent row."""
        self.prepare(name)
        self.run_runner(name, provider, "\n".join(json.dumps(e) for e in events) + "\n", exit_code=exit_code)
        return self.row(name)

    def test_claude_api_error_is_interrupted(self):
        """A Claude result with is_error and an overloaded text ends failed and interrupted as a rate limit."""
        row = self.run_stream("I-1", "claude", [{"type": "result", "is_error": True, "result": "API Error: 529 overloaded_error"}])
        self.assertEqual((row["status"], row.get("interrupted")), ("failed", interrupt.RATE_LIMIT))

    def test_codex_connection_error_is_interrupted(self):
        """A Codex turn.failed with a lost stream ends failed and interrupted as a connection error."""
        row = self.run_stream("I-2", "codex", [{"type": "turn.failed", "error": {"message": "stream disconnected before completion"}}])
        self.assertEqual((row["status"], row.get("interrupted")), ("failed", interrupt.CONNECTION))

    def test_normal_failure_and_success_are_not_interrupted(self):
        """A failed task with an ordinary error text and a successful end carry no interruption mark."""
        row = self.run_stream("I-3", "claude", [{"type": "result", "is_error": True, "result": "Tests failed: the network code has 500 lines"}])
        self.assertEqual((row["status"], row.get("interrupted") or ""), ("failed", ""))
        row = self.run_stream("I-4", "claude", [{"type": "result", "is_error": False, "result": "Done. The API Error 500 handling is finished."}], exit_code=0)
        self.assertEqual((row["status"], row.get("interrupted") or ""), ("done", ""))


if __name__ == "__main__":
    unittest.main()
