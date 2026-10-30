"""Tests of interrupted Queens, preserved retries and provider-limit waits."""
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


# Verify interrupted Queen retries and provider limits.
class InterruptedRetryTest(RoundCase):
    """A Hive retries interrupted Queens without switching provider."""

    # Prepare an existing Hive for each retry case.
    def setUp(self):
        """Create the plan and its running Hive."""
        from haivequeen import hives
        super().setUp()
        self.plan("IN_PROGRESS", [{"id": "R1", "title": "first task", "class": "medium"}])
        hives.create(self.conn, "testproj", REL, "claude", "4FA8FF")
        self.conn.execute("UPDATE hives SET state='running' WHERE project=? AND plan_path=?", ("testproj", REL))

    # Record an interrupted Queen without a living runner.
    def interrupted(self, name="QUEEN-1", kind=interrupt.CONNECTION, retry="", started=30, ended=25):
        """Create one interrupted Queen history entry."""
        self.agent(name, package="", phase="queen", status="failed", last_line="error: " + kind,
                   type="claude", provider="claude", started=started, ended=ended, interrupted=kind, retryAt=retry)

    # Configure isolated provider usage windows.
    def limits(self, **used):
        """Write test-only provider limits."""
        f = self.base / "limits.json"
        f.write_text(json.dumps({n: {"usedPercent": u, "resetHours": h} for n, (u, h) in used.items()}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(f)

    # Verify a future retry deadline blocks a new Queen.
    def test_waiting_retry_starts_nothing(self):
        """A Queen is not restarted before the stored retry time."""
        self.interrupted(retry=later(10))
        self.round()
        self.assertEqual(self.names(), ["QUEEN-1"])

    # Verify a due retry starts the same Hive and provider.
    def test_due_retry_starts_the_same_phase(self):
        """A due retry starts another Queen on the same provider."""
        self.interrupted(retry=later(-1))
        self.round()
        rows = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["phase"], rows[0]["provider"]), ("queen", "claude"))

    # Verify retry deadlines survive an environment reload.
    def test_waiting_retry_survives_a_restart(self):
        """The persisted deadline is still respected in later rounds."""
        self.interrupted(retry=later(10))
        self.round()
        self.assertEqual(self.names(), ["QUEEN-1"])
        runtime.upsert_agent(self.conn, "testproj", "QUEEN-1", {"retryAt": later(-1)})
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]), 1)

    # Verify interruptions never increase package-failure counts.
    def test_interrupted_ends_are_no_failures(self):
        """Repeated technical ends do not become package failures."""
        for n in range(3):
            self.interrupted("QUEEN-%d" % n, kind=interrupt.API, retry=later(-1), started=90 - 20*n, ended=85 - 20*n)
        env = self.env()
        self.assertEqual(routine.package_failures(env, routine.all_agents(env), REL, "R1"), 0)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["class"], "medium")

    # Verify a limit does not trigger an automatic provider change.
    def test_rate_limit_rests_on_same_provider(self):
        """A full provider rests even if another provider has room."""
        from haivequeen import hives
        self.limits(claude=(100, 48), codex=(10, 48))
        self.interrupted(kind=interrupt.RATE_LIMIT)
        self.round()
        hive = hives.get(self.conn, "testproj", REL)
        self.assertEqual((hive["state"], hive["provider"]), ("resting", "claude"))
        self.assertEqual(self.names(), ["QUEEN-1"])

    # Verify the later reset of two full windows is retained.
    def test_rate_limit_waits_for_later_full_window(self):
        """The reset deadline is the later full window of the Hive provider."""
        from haivequeen import hives
        f = self.base / "limits.json"
        f.write_text(json.dumps({"claude": {"usedPercent": 100, "resetHours": 48,
            "windows": [{"usedPercent": 100, "resetHours": 2}, {"usedPercent": 100, "resetHours": 48}]}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(f)
        self.interrupted(kind=interrupt.RATE_LIMIT)
        self.round()
        at = routine.db.parse_time(hives.get(self.conn, "testproj", REL)["reset_at"])
        self.assertAlmostEqual((at - datetime.now()).total_seconds() / 3600, 48, delta=0.1)

    # Verify the existing fallback when no reset time is known.
    def test_rate_limit_without_known_reset_waits_15_minutes(self):
        """An unknown reset uses the existing fifteen-minute fallback."""
        from haivequeen import hives
        self.limits(claude=(100, None), codex=(10, 48))
        self.interrupted(kind=interrupt.RATE_LIMIT)
        self.round()
        at = routine.db.parse_time(hives.get(self.conn, "testproj", REL)["reset_at"])
        self.assertAlmostEqual((at - datetime.now()).total_seconds() / 3600, 0.25, delta=0.02)


# Verify an old triage interruption does not start a new triage agent.
class InterruptedTriageTest(RoundCase):
    """Triage agents are not restarted in the Hive flow."""

    # Verify that no replacement triage agent is launched.
    def test_interrupted_triage_waits_and_is_retried(self):
        """An old interrupted triage row does not start another triage agent."""
        self.plan("IN_PROGRESS", [{"id": "K1", "title": "task"}])
        self.agent("old-triage", phase="triage", status="failed", interrupted=interrupt.CONNECTION, started=30, ended=20)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running"]
        self.assertTrue(all(a["phase"] != "triage" for a in active))


class ClockJumpTest(unittest.TestCase):
    """A run during which the wall clock jumped by more than 120 seconds counts as interrupted when it then fails, and stays done when it succeeds."""

    def watch(self, readings):
        """Run clock_watch over fake clock readings (the sleeps are skipped) and return the jumps it found."""
        from haivequeen import agent_runner as ar
        jumps, it = [], iter(readings)
        ar.clock_watch(jumps, step=5.0, now=lambda: next(it), sleep=lambda s: None, rounds=len(readings) - 1)
        return jumps

    def test_jump_is_detected(self):
        """Readings 0, 5, 10, then 400: the last step is a jump of about 390 seconds."""
        self.assertEqual(len(self.watch([0, 5, 10, 400])), 1)

    def test_normal_clock_has_no_jump(self):
        """Readings every 5 seconds (and one 60 s hiccup) give no jump."""
        self.assertEqual(self.watch([0, 5, 10, 70, 75]), [])

    def test_failed_run_after_a_jump_is_interrupted_and_a_good_run_stays_done(self):
        """The same jump: a failed end is interrupted (sleep), a successful end is not."""
        jumps = self.watch([0, 5, 400])
        self.assertEqual(interrupt.classify_end(True, [], clock_jumped=bool(jumps)), interrupt.SLEEP)
        self.assertEqual(interrupt.classify_end(False, [], clock_jumped=bool(jumps)), "")


class RunnerClockJumpTest(RunnerBase):
    """The runner marks an end after a simulated clock jump (BZZ_FAKE_CLOCK_JUMP, tests only)."""

    def test_failed_end_after_jump_is_interrupted(self):
        """A failing run with a simulated jump of 300 s ends interrupted (sleep); the same without a jump does not."""
        self.prepare("J-1")
        self.run_runner("J-1", "claude", json.dumps({"type": "result", "is_error": True, "result": "task failed"}) + "\n", exit_code=1,
                        env={"BZZ_FAKE_CLOCK_JUMP": "300"})
        self.assertEqual((self.row("J-1")["status"], self.row("J-1").get("interrupted")), ("failed", interrupt.SLEEP))
        self.prepare("J-2")
        self.run_runner("J-2", "claude", json.dumps({"type": "result", "is_error": True, "result": "task failed"}) + "\n", exit_code=1)
        self.assertEqual(self.row("J-2").get("interrupted") or "", "")

    def test_good_end_after_jump_stays_done(self):
        """A successful run with a simulated jump stays done and is not marked."""
        self.prepare("J-3")
        self.run_runner("J-3", "claude", json.dumps({"type": "result", "is_error": False, "result": "Open requirements: 0"}) + "\n", exit_code=0,
                        env={"BZZ_FAKE_CLOCK_JUMP": "300"})
        self.assertEqual((self.row("J-3")["status"], self.row("J-3").get("interrupted") or ""), ("done", ""))


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

    # Verify the Queen receives the interrupted-merge recovery instruction.
    def test_next_integrator_aborts_an_unfinished_merge(self):
        """The Queen is instructed to abort an unfinished merge before integrating."""
        self.integrator()
        self.round()
        new = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(new), 1)
        self.assertIn("abort any unfinished merge", (self.env().run_dir(new[0]["name"]) / "prompt.md").read_text(encoding="utf-8"))


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
