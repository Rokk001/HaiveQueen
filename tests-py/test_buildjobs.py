"""Port of test-buildjobs.ps1 and test-events.ps1: build-job calculation and measurement, the dashboard line, the onEvent hook."""
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bzztest import REL, RoundCase, runtime
from haivequeen import buildjobs as bj
from haivequeen import events


def settings(**extra):
    """Build-job settings with the standard values; extra overrides single keys."""
    b = {"envVar": "BUILD_JOBS", "coreShare": "2/3", "reserveMB": 1024, "perJobMB": "auto", "firstJobs": 2}
    b.update(extra)
    return bj.settings_from(b)


class BuildJobsTest(RoundCase):
    """All numbers are invented."""

    def test_defaults(self):
        """Defaults when the block only names the variable; no block = no build jobs."""
        d = bj.settings_from({"envVar": "X"})
        self.assertAlmostEqual(d["CoreShare"], 2 / 3)
        self.assertEqual((d["ReserveMB"], d["FirstJobs"], d["PerJobMB"], d["Fixed"]), (1024, 2, None, None))
        self.assertIsNone(bj.settings_from(None))
        self.assertEqual(bj.settings_from(4)["Fixed"], 4)
        self.assertEqual(bj.settings_from(0)["EnvVar"], "BUILD_JOBS")

    def test_job_count(self):
        """First build, RAM and core limits, fixed, configured per-job value, reserve."""
        r = bj.job_count(settings(), 12, 32000, None)
        self.assertEqual((r["Jobs"], r["Why"]), (2, "first"))
        self.assertEqual(bj.job_count(settings(firstJobs=10), 6, 32000, None)["Jobs"], 4)
        r = bj.job_count(settings(), 12, 4096, 1000)
        self.assertEqual((r["Jobs"], r["Why"]), (3, "ram"))
        self.assertEqual(bj.job_count(settings(), 12, 1500, 1000)["Jobs"], 1)
        r = bj.job_count(settings(), 10, 64000, 500)
        self.assertEqual((r["Jobs"], r["Why"]), (7, "cores"))
        r = bj.job_count(settings(fixed=5), 4, 1200, 2000)
        self.assertEqual((r["Jobs"], r["Why"]), (5, "fixed"))
        self.assertEqual(bj.job_count(settings(perJobMB=2000), 12, 6144, 100)["Jobs"], 2)
        self.assertEqual((bj.job_count(settings(), 12, 3072, 1000)["Jobs"], bj.job_count(settings(reserveMB=2048), 12, 3072, 1000)["Jobs"]), (2, 1))

    def test_stored_maximum(self):
        """A higher measured value replaces the old one, a lower one does not; the stored value feeds the next count."""
        c = self.conn
        self.assertIsNone(bj.stored_per_job(c, "testproj"))
        self.assertEqual(bj.save_per_job(c, "testproj", 600), 600)
        self.assertEqual(bj.save_per_job(c, "testproj", 900), 900)
        self.assertEqual(bj.save_per_job(c, "testproj", 400), 900)
        self.assertEqual(bj.job_count(settings(), 12, 4096, bj.stored_per_job(c, "testproj"))["Jobs"], 3)
        self.assertEqual((bj.use_mb(8000, 5000, 3), bj.use_mb(3000, 4000, 2)), (1000, 0))

    def test_measure_and_line(self):
        """End of a build: lowest sample, use per job, one ROUNDS.log line; dashboard line while a build runs and without."""
        samples = self.base / "samples.txt"
        samples.write_text("2026-01-01T00:00:00;8000;0\n2026-01-01T00:00:10;6500;4\n2026-01-01T00:00:20;5000;4\n2026-01-01T00:00:30;7000;1\n", encoding="utf-8")
        env = self.env()
        m = bj.complete_measure(env, "TEST-build", 3, 8000, samples, datetime.now() - timedelta(seconds=42))
        self.assertEqual((m["MinFreeMB"], m["UseMB"], m["StoredMB"]), (5000, 1000, 1000))
        self.assertRegex(self.log(), r"build TEST-build: jobs=3, duration=00:00:4\d, lowest free RAM=5000 MB, use per job=1000 MB")
        env.settings["buildJobs"] = {"envVar": "BUILD_JOBS", "fixed": 4}
        self.assertEqual(bj.jobs_line(env, [{"status": "running", "buildJobs": 4}], 5, cores=8),
                         "Build jobs 4 of 8 cores (running) | per job 1000 MB | active: 5 cores")
        self.assertEqual(bj.jobs_line(env, [], 0, cores=8), "Build jobs 4 of 8 cores (next build) | per job 1000 MB")
        self.assertTrue(bj.jobs_line(env, [], 3, cores=8).endswith("| active: 3 cores"))
        self.assertIsNone(bj.jobs_line(type("E", (), {"settings": {}, "conn": self.conn, "name": "testproj"})(), []))

    def test_sampler(self):
        """The sampler writes `time;free MB;count` lines and ends with the parent."""
        out = self.base / "s.txt"
        n = bj.sample_loop(out, 0, 0.01, [], max_samples=2)
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual((n, len(lines)), (2, 2))
        self.assertEqual(len(lines[0].split(";")), 3)


class EventHookTest(RoundCase):
    """The onEvent hook: one call per round with all new events; a failed call is repeated."""

    def setUp(self):
        """A recorder script is the hook command; it fails while fail.flag exists."""
        super().setUp()
        self.rec = self.base / "recorder.py"
        self.rec.write_text("import sys, pathlib\nb = pathlib.Path(__file__).parent\nwith open(b / 'calls.log', 'a', encoding='utf-8') as f:\n    f.write('|'.join(sys.argv[1:]) + '\\n')\n"
                            "sys.exit(1 if (b / 'fail.flag').exists() else 0)\n", encoding="utf-8")
        self.machine["onEvent"] = {"command": [sys.executable, str(self.rec)], "workDir": str(self.base), "timeoutSeconds": 60}
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "first"}])

    def calls(self):
        """Lines of the recorder log."""
        f = self.base / "calls.log"
        return f.read_text(encoding="utf-8").splitlines() if f.is_file() else []

    def test_hook(self):
        """Baseline, quiet round, one event, two events in one call, failed call repeated, success clears."""
        note = self.brain_answer({"action": "note", "text": "test"})
        self.round(note)
        self.assertEqual(self.calls(), [], "first round only records the baseline")
        self.round(note)
        self.assertEqual(self.calls(), [])
        runtime.set_plan_status(self.conn, "testproj", REL, "PAUSED", str(self.repo))
        self.round(note)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("plan DUMMY-PLAN PAUSED", self.calls()[0])
        self.round(note)
        self.assertEqual(len(self.calls()), 1, "no new event: no second call")
        runtime.queue_upsert(self.conn, "testproj", "feature/dummy", "abc123def", REL, "ready", "x")
        from haivequeen import db
        qid = db.add_question(self.conn, "testproj", REL, "X1", "Which one?", "A")
        self.round(note)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("ready feature/dummy", self.calls()[1])
        self.assertIn("question Q-%d asked" % qid, self.calls()[1])
        (self.base / "fail.flag").write_text("x")
        db.answer_question(self.conn, qid, "A", "owner")
        self.round(note)
        ev = (Path(self.base / "logs") / "ORCH-EVENTS.log").read_text(encoding="utf-8")
        self.assertEqual(len(self.calls()), 3)
        self.assertRegex(ev, r"\|E\|Event command failed")
        (self.base / "fail.flag").unlink()
        self.round(note)
        self.assertEqual(len(self.calls()), 4)
        self.assertEqual(self.calls()[3], self.calls()[2])
        self.assertIn("question Q-%d answered" % qid, self.calls()[3])
        self.round(note)
        self.assertEqual(len(self.calls()), 4, "after success nothing is repeated")

    def test_missing_command_never_raises(self):
        """A command that does not exist raises nothing and the event stays pending."""
        env = self.env()
        env.settings["onEvent"] = {"command": ["does-not-exist-xyz", "-x"], "timeoutSeconds": 5}
        plans = [{"path": REL, "status": "READY"}]
        events.invoke_hook(env, [], [], lambda n: False, [])   # baseline
        events.invoke_hook(env, plans, [], lambda n: False, [])
        state = json.loads(runtime.kv_get(self.conn, "testproj", "event-sync"))
        self.assertEqual(len(state["pending"]), 1)


if __name__ == "__main__":
    unittest.main()
