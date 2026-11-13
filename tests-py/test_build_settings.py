"""Build settings: core selection, parallel value, minimum free RAM, measurement of the build processes, median, icons."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import agent_runner, buildjobs as bj, cli, config, db, settings, hives, loop, roles, routine, runtime
from haivequeen import platform as osplat
from test_agent_runner import RunnerBase, claude_stream

ROOT = Path(__file__).resolve().parent.parent


def new_row(conn, project, per_job, ts, jobs=2, host="testhost", parallel=1):
    """Store one valid (process RAM) measurement row."""
    runtime.build_add(conn, project, host, jobs, 0, 0, per_job, 1, peak_mb=per_job * jobs * parallel, parallel=parallel)
    conn.execute("UPDATE build_measurements SET ts=? WHERE rowid=(SELECT MAX(rowid) FROM build_measurements)", (ts,))


class CoreSelectionTest(unittest.TestCase):
    """B-1, B-6a: the core selection, its default and the old forms."""

    def test_choices_and_default(self):
        """The selection offers 1 to the logical core count; the default is two thirds, rounded up."""
        self.assertEqual(bj.core_choices(8), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual([bj.default_cores(n) for n in (1, 2, 3, 5, 8, 12, 16)], [1, 2, 2, 4, 6, 8, 11])
        self.assertEqual(bj.core_limit(bj.settings_from({}), 8), 6)

    def test_old_core_share_gives_the_same_count(self):
        """An existing coreShare is used as the core count it gives today (two thirds of 8 = 6)."""
        self.assertEqual(bj.core_limit(bj.settings_from({"coreShare": "2/3"}), 8), 6)
        self.assertEqual(bj.core_limit(bj.settings_from({"coreShare": "1/2"}), 12), 6)
        self.assertEqual(settings.build_values({"buildJobs": {"coreShare": "2/3"}}, 8)["cores"], 6)

    def test_stored_count_above_the_machine_is_capped(self):
        """A stored core count above the cores of the machine is used as the machine's core count."""
        self.assertEqual(bj.core_limit(bj.settings_from({"cores": 20}), 8), 8)
        self.assertEqual(settings.build_values({"buildJobs": {"cores": 20}}, 8)["cores"], 8)
        self.assertEqual(bj.job_count(bj.settings_from({"cores": 20, "perJobMB": 1}), 8, 64000, None)["Jobs"], 8)

    def test_old_forms_map_to_the_core_selection(self):
        """buildJobs = N and fixed = N are core counts; buildJobs = 0 is the default; the new core count wins when both exist."""
        self.assertEqual(bj.core_limit(bj.settings_from(4), 12), 4)
        self.assertEqual(bj.core_limit(bj.settings_from({"fixed": 3}), 12), 3)
        self.assertEqual(bj.core_limit(bj.settings_from(0), 12), 8)
        self.assertEqual(bj.core_limit(bj.settings_from({"cores": 5, "fixed": 3, "coreShare": "1/2"}), 12), 5)

    def test_the_limit_is_an_upper_limit(self):
        """The RAM limit (free RAM - reserve) / use per job can still lower the chosen count."""
        b = bj.settings_from({"cores": 6})
        self.assertEqual(bj.job_count(b, 8, 4096, 1000)["Jobs"], 3)
        self.assertEqual(bj.job_count(b, 8, 64000, 1000)["Jobs"], 6)


class ParallelJobsTest(unittest.TestCase):
    """B-7, B-10a: the parallel value and the shared cores."""

    def test_defaults(self):
        """The parallel value defaults to 1, the minimum free RAM to 2 GB, the variable names to BUILD_JOBS / BUILD_PARALLEL; a configured envVar wins."""
        d = bj.settings_from({})
        self.assertEqual((d["Parallel"], d["MinFreeGB"], d["EnvVar"], d["ParallelVar"], d["Measurements"]), (1, 2.0, "BUILD_JOBS", "BUILD_PARALLEL", 10))
        self.assertEqual(bj.settings_from({"envVar": "BUILD_MP_JOBS"})["EnvVar"], "BUILD_MP_JOBS")

    def test_jobs_per_target_are_cores_divided_by_parallel(self):
        """With parallel N each target gets floor(cores / N) jobs, at least 1; the RAM per job counts for jobs x N."""
        b = bj.settings_from({"cores": 6, "parallel": 2, "perJobMB": 1})
        self.assertEqual(bj.job_count(b, 8, 64000, None)["Jobs"], 3)
        self.assertEqual(bj.job_count(bj.settings_from({"cores": 6, "parallel": 4, "perJobMB": 1}), 8, 64000, None)["Jobs"], 1)
        self.assertEqual(bj.job_count(bj.settings_from({"cores": 2, "parallel": 5, "perJobMB": 1}), 8, 64000, None)["Jobs"], 1)
        ram = bj.settings_from({"cores": 8, "parallel": 2, "perJobMB": 1000, "reserveMB": 1000})
        self.assertEqual(bj.job_count(ram, 8, 5000, None)["Jobs"], 2)   # (5000 - 1000) / (1000 x 2)

    def test_builds_at_the_same_time_share_the_cores(self):
        """Two builds at the same time share the chosen cores, each gets at least 1."""
        b = bj.settings_from({"cores": 6, "perJobMB": 1})
        self.assertEqual(bj.job_count(b, 8, 64000, None)["Jobs"], 6)
        self.assertEqual(bj.job_count(b, 8, 64000, None, concurrent=1)["Jobs"], 3)
        self.assertEqual(bj.job_count(b, 8, 64000, None, concurrent=9)["Jobs"], 1)

    def test_running_builds_are_counted_from_the_agent_rows(self):
        """plan_build lets the builds that run now share the cores; the asking agent itself is not counted."""
        with contextlib.ExitStack() as stack:
            import tempfile
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            conn = db.connect(Path(tmp) / "db")
            stack.callback(conn.close)
            runtime.upsert_agent(conn, "p", "A-integrate", {"status": "running", "buildJobs": 3})
            runtime.upsert_agent(conn, "q", "B-queen", {"status": "running", "buildJobs": 2})
            env = type("E", (), {"name": "p", "conn": conn, "settings": {"buildJobs": {"cores": 6, "perJobMB": 1}}})()
            self.assertEqual(bj.plan_build(env, free_mb=64000, cores=8)["Jobs"], 1)  # five cores already allocated
            self.assertEqual(bj.plan_build(env, free_mb=64000, cores=8, exclude="A-integrate")["Jobs"], 3)

    def test_the_parallel_value_is_a_whole_number_from_1(self):
        """The settings window accepts whole numbers from 1 up and nothing else."""
        data = {}
        settings.apply_build(data, "4", "3", "2", cores=8)
        self.assertEqual(data["buildJobs"]["parallel"], 3)
        for bad in ("0", "-1", "1.5", "x", ""):
            with self.assertRaises(ValueError, msg=bad):
                settings.apply_build({}, "4", bad, "2", cores=8)
        for bad in ("0", "9", "x"):
            with self.assertRaises(ValueError, msg=bad):
                settings.apply_build({}, bad, "1", "2", cores=8)


class MeasurementTest(RoundCase):
    """B-2, B-3, B-4, B-6b: what is measured, the median and the old values."""

    def sample_file(self, lines):
        """Write a sample file (time;free MB;build process count;their RAM MB)."""
        f = self.base / "samples.txt"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_only_the_ram_of_the_build_processes_counts(self):
        """The free RAM of the whole machine does not matter: the peak of the process RAM sum divided by the jobs is stored."""
        f = self.sample_file(["2026-01-01T00:00:00;90000;0;0", "2026-01-01T00:00:10;100;3;2100", "2026-01-01T00:00:20;50;3;3000", "2026-01-01T00:00:30;90000;1;400"])
        m = bj.complete_measure(self.env(), "T-build", 3, 90000, f, datetime.now() - timedelta(seconds=5))
        self.assertEqual((m["PeakMB"], m["UseMB"], m["StoredMB"]), (3000, 1000, 1000))

    def test_a_build_without_a_build_process_stores_nothing(self):
        """No build process seen (also in the old three column format): no measurement row and a log line that says so."""
        for lines in (["2026-01-01T00:00:00;8000;0;0", "2026-01-01T00:00:10;1000;0;0"], ["2026-01-01T00:00:00;8000;4", "2026-01-01T00:00:10;1000;4"], []):
            f = self.sample_file(lines)
            m = bj.complete_measure(self.env(), "T-build", 2, 8000, f, datetime.now())
            self.assertEqual((m["UseMB"], m["StoredMB"]), (None, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM build_measurements").fetchone()[0], 0)
        self.assertIn("no build process seen, nothing stored", self.log())

    def test_the_peak_is_divided_by_the_jobs_handed_out_and_the_parallel_value(self):
        """The use per job is the peak RAM sum / (jobs x parallel)."""
        self.assertEqual(bj.use_mb(6000, 3, 2), 1000)
        f = self.sample_file(["2026-01-01T00:00:10;100;5;6000"])
        m = bj.complete_measure(self.env(), "T-build", 3, 8000, f, datetime.now(), parallel=2)
        self.assertEqual(m["UseMB"], 1000)
        row = self.conn.execute("SELECT jobs, parallel, per_job_mb, peak_mb FROM build_measurements").fetchone()
        self.assertEqual(tuple(row), (3, 2, 1000, 6000))

    def test_median_of_the_last_measurements(self):
        """The job count uses the median of the last 10 measurements, not the highest value ever stored."""
        for i, per in enumerate([5000, 5000, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]):
            new_row(self.conn, "testproj", per, "2026-01-%02dT00:00:00" % (i + 1))
        self.assertEqual(bj.stored_per_job(self.conn, "testproj"), 550.0)   # the last 10: 100 .. 1000
        self.assertEqual(bj.stored_per_job(self.conn, "testproj", count=3), 900.0)
        self.assertEqual(bj.job_count(bj.settings_from({"cores": 12}), 12, 7000, bj.stored_per_job(self.conn, "testproj"))["Jobs"], 10)

    def test_median_with_fewer_measurements(self):
        """With fewer measurements than the setting the median of those that exist is used."""
        new_row(self.conn, "testproj", 400, "2026-01-01T00:00:00")
        self.assertEqual(bj.stored_per_job(self.conn, "testproj"), 400.0)
        new_row(self.conn, "testproj", 800, "2026-01-02T00:00:00")
        self.assertEqual(bj.stored_per_job(self.conn, "testproj"), 600.0)
        env = type("E", (), {"name": "testproj", "conn": self.conn, "settings": {"buildJobs": {"measurements": 1, "cores": 12}}})()
        self.assertEqual(bj.plan_build(env, free_mb=7000, cores=12, host="testhost")["PerJobMB"], 800.0)

    def test_old_measurements_are_not_used(self):
        """Rows stored before this change (no process RAM) are ignored: firstJobs applies, nothing is shown, and the maximum helper skips them."""
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO build_measurements (host, project, ts, jobs, per_job_mb) VALUES ('testhost','testproj','2026-01-01T00:00:00',1,5226)")
        self.assertIsNone(bj.stored_per_job(self.conn, "testproj"))
        self.assertIsNone(runtime.build_max_per_job(self.conn, "testproj", "testhost"))
        env = type("E", (), {"name": "testproj", "conn": self.conn, "settings": {"buildJobs": {"cores": 8}}})()
        plan = bj.plan_build(env, free_mb=9000, cores=8, host="testhost")
        self.assertEqual((plan["Jobs"], plan["Why"], plan["PerJobMB"]), (2, "first", None))
        self.assertIn("per job not measured yet", bj.jobs_line(env, [], 0, cores=8, free_mb=9000, host="testhost"))
        new_row(self.conn, "testproj", 700, "2026-02-01T00:00:00")
        self.assertEqual(bj.stored_per_job(self.conn, "testproj"), 700.0)

    def test_the_old_database_gets_the_columns(self):
        """A database of the previous version gets the two new columns and keeps its rows as old values."""
        c = db.connect(self.base / "old.db")
        c.execute("DROP TABLE build_measurements")
        c.execute("CREATE TABLE build_measurements (host TEXT, project TEXT, ts TEXT, jobs INTEGER, free_start_mb REAL, free_min_mb REAL, per_job_mb REAL, duration_s REAL)")
        c.execute("INSERT INTO build_measurements VALUES ('h','p','t',1,1,1,5226,1)")
        c.execute("PRAGMA user_version = 11")
        c.commit()
        c.close()
        c = db.connect(self.base / "old.db")
        self.assertEqual(c.execute("SELECT peak_mb, parallel FROM build_measurements").fetchone()[0], None)
        self.assertIsNone(bj.stored_per_job(c, "p", "h"))
        c.close()


class ProcessTreeTest(unittest.TestCase):
    """B-2, B-10c: only the build processes started by the building agent are measured."""

    CHILD = ("import subprocess, sys, time\n"
             "b = bytearray(%d * 1024 * 1024)\nb[::4096] = b'\\x01' * (len(b) // 4096)\n"
             "kid = subprocess.Popen([sys.executable, '-c', 'import time\\nb = bytearray(%d * 1024 * 1024)\\nb[::4096] = b\"\\\\x01\" * (len(b) // 4096)\\nprint(1, flush=True)\\ntime.sleep(60)'], stdout=subprocess.PIPE)\n"
             "kid.stdout.readline()\nprint(1, flush=True)\ntime.sleep(60)\n")

    def start(self, mine, theirs):
        """Start a process (with a child) that holds `mine` MB + `theirs` MB and wait until both are touched."""
        p = subprocess.Popen([sys.executable, "-c", self.CHILD % (mine, theirs)], stdout=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        p.stdout.readline()
        return p

    def test_only_the_tree_of_the_agent_counts(self):
        """The RAM of the python processes below the agent is summed; a process of the same name that someone else started is not counted."""
        name = Path(sys.executable).name
        mine = self.start(40, 60)
        other = self.start(300, 300)
        time.sleep(0.3)
        ram, count = osplat.process_tree_ram(mine.pid, [name])
        self.assertEqual(count, 2)
        self.assertGreater(ram, 100)
        self.assertLess(ram, 400)   # the 600 MB of the other tree are not part of it
        ram_other, count_other = osplat.process_tree_ram(other.pid, [name])
        self.assertGreater(ram_other, 600)
        self.assertEqual(osplat.process_tree_ram(mine.pid, ["no-such-build-tool.exe"]), (0.0, 0))
        self.assertEqual(osplat.process_tree_ram(None, [name]), (0.0, 0))
        self.assertEqual(osplat.process_tree_ram(mine.pid, []), (0.0, 0))

    def test_the_sample_line_has_the_process_ram(self):
        """A sample line is `time;free MB;count;RAM MB` and reads back through read_samples."""
        name = Path(sys.executable).name
        p = self.start(50, 50)
        line = bj.sample_line([name], p.pid)
        parts = line.split(";")
        self.assertEqual(len(parts), 4)
        self.assertEqual(int(parts[2]), 2)
        self.assertGreater(int(parts[3]), 80)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.txt"
            f.write_text(line + "\n", encoding="utf-8")
            got = bj.read_samples(f)
        self.assertEqual((got["Seen"], got["PeakMB"] > 80), (2, True))


class AcquireTest(RoundCase):
    """B-0, B-6c, B-9, B-10d: what bzz integration acquire hands out and when it waits."""

    def setUp(self):
        """The Project has a Build command, so its integration builds and needs a job count and free RAM."""
        super().setUp()
        toml = self.repo / ".haivequeen" / "project.toml"
        toml.write_text(toml.read_text(encoding="utf-8") + '\nbuildCommand = "make"\n', encoding="utf-8")

    def config_file(self, text):
        """Write config.toml of the machine for the cli commands."""
        d = self.base / "cfg"
        d.mkdir(exist_ok=True)
        (d / "config.toml").write_text(text, encoding="utf-8")

    def acquire(self, plan, free_mb, agent=None):
        """Run `bzz integration acquire` with a given free RAM; returns (exit code, printed JSON)."""
        buf = io.StringIO()
        saved = os.environ.get("BZZ_AGENT")
        try:
            if agent:
                os.environ["BZZ_AGENT"] = agent
            else:
                os.environ.pop("BZZ_AGENT", None)
            with contextlib.redirect_stdout(buf), mock.patch.object(osplat, "free_ram_mb", return_value=free_mb), mock.patch.object(osplat, "logical_cores", return_value=8):
                code = cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan=plan, owner_pid=os.getpid()), self.conn)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved
        return code, json.loads(buf.getvalue())

    def test_acquire_hands_out_the_computed_count_not_one(self):
        """The job count of acquire follows cores, RAM and measurements; the configured variable name is used; parallel and minimum RAM come with it."""
        self.config_file('[buildJobs]\nenvVar = "BUILD_MP_JOBS"\ncores = 6\nparallel = 2\nminFreeRamGB = 2\nperJobMB = 500\n')
        code, got = self.acquire("outside", 64000)
        self.assertEqual(code, 0)
        job = got["buildJobs"]
        self.assertEqual((job["Jobs"], job["EnvVar"], job["Parallel"]), (3, "BUILD_MP_JOBS", 2))
        self.assertEqual(job["Environment"], {"BUILD_MP_JOBS": "3", "BUILD_PARALLEL": "2", "BUILD_MIN_FREE_RAM_GB": "2"})
        self.assertEqual(self.conn.execute("SELECT build_jobs FROM hive_integration WHERE plan_path='outside'").fetchone()[0], 3)

    def test_the_count_of_the_moment_replaces_the_earlier_one(self):
        """A second acquire with less free RAM replaces the stored count (the one of the Queen start); the measurement divides by it."""
        self.config_file('[buildJobs]\ncores = 8\nperJobMB = 1000\nreserveMB = 0\n')
        self.assertEqual(self.acquire("outside", 8000)[1]["buildJobs"]["Jobs"], 8)
        self.assertEqual(self.acquire("outside", 3000)[1]["buildJobs"]["Jobs"], 3)
        self.assertEqual(self.conn.execute("SELECT build_jobs FROM hive_integration WHERE plan_path='outside'").fetchone()[0], 3)

    def test_acquire_waits_below_the_minimum_free_ram_without_the_place(self):
        """Below the minimum free RAM acquire answers waiting, does not take the place and starts the build when the RAM is there."""
        self.config_file('[buildJobs]\nminFreeRamGB = 4\n')
        code, got = self.acquire("outside", 3000)
        self.assertEqual(code, 1)
        self.assertIn("waiting for integration", got["error"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)
        code, got = self.acquire("outside", 4096)
        self.assertEqual(code, 0)
        self.assertEqual(self.conn.execute("SELECT acquired FROM hive_integration").fetchone()[0], 1)

    def test_a_queen_waiting_for_ram_is_marked_and_does_not_hold_the_place(self):
        """A Queen that must wait for RAM gets the state integration_wait like for an occupied place and holds nothing."""
        self.config_file('[buildJobs]\nminFreeRamGB = 4\n')
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        code, _ = self.acquire(REL, 1000, agent=queen["name"])
        self.assertEqual(code, 1)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "integration_wait")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)

    def test_the_restart_of_a_waiting_queen_waits_for_the_ram(self):
        """A Queen in integration_wait is not restarted (and takes no place) while the RAM is below the minimum, and is restarted when it is reached."""
        self.machine["buildJobs"] = {"minFreeRamGB": 8}
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        self.finish(queen["name"], status="failed")
        self.conn.execute("UPDATE hives SET state='integration_wait' WHERE project='testproj'")
        self.round(free_ram=lambda: 4.0, alive=lambda pid: False)
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "integration_wait")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)
        self.round(free_ram=lambda: 9.0, alive=lambda pid: False)
        self.assertNotEqual(hives.get(self.conn, "testproj", REL)["state"], "integration_wait")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 1)


class StartWaitTest(RoundCase):
    """B-10d: the start of an integrate or release agent waits for the minimum free RAM before a build."""

    def test_integrate_and_release_wait_work_does_not(self):
        """Below the minimum an integrate or release agent waits (a start reason that is tried again next round); other phases are not held."""
        env = self.env()
        env.settings["buildJobs"] = {"minFreeRamGB": 8}
        for phase in ("integrate", "release"):
            self.assertIn("free RAM 4.00 GB is below", loop.build_ram_wait(env, phase, 4.0))
            self.assertTrue(loop.TEMPORARY.match(loop.build_ram_wait(env, phase, 4.0)))
            self.assertIsNone(loop.build_ram_wait(env, phase, 9.0))
        self.assertIsNone(loop.build_ram_wait(env, "work", 1.0))
        env.settings["buildJobs"] = 0
        self.assertIsNotNone(loop.build_ram_wait(env, "release", 1.5))   # the default minimum is 2 GB
        self.assertIsNone(loop.build_ram_wait(env, "release", 2.5))


class QueenMonitorTest(RoundCase):
    """B-6c: the count handed out at acquire replaces the one from the Queen start in the measurement."""

    def test_the_measurement_divides_by_the_count_of_acquire(self):
        """Acquire replaces 2 jobs with 5; one observed process and three parallel targets divide the measured peak by 3."""
        name = Path(sys.executable).name
        runtime.upsert_agent(self.conn, "testproj", "Q-queen", {"status": "running", "phase": "queen", "plan_path": REL})
        self.conn.execute("INSERT INTO hive_integration (project, plan_path, acquired, build_jobs) VALUES ('testproj', ?, 1, 5)", (REL,))
        self.conn.commit()
        stop = threading.Event()
        samples = self.base / "queen-samples.txt"
        t = threading.Thread(target=agent_runner.queen_build_monitor, args=("testproj", REL, "Q-queen", 2, samples, [name], stop, 0.05, 3, os.getpid(), 10))
        t.start()
        time.sleep(0.6)
        stop.set()
        t.join(10)
        rows = self.conn.execute("SELECT jobs, per_job_mb, peak_mb FROM build_measurements WHERE project='testproj'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["jobs"], 5)
        self.assertEqual(runtime.get_agent(self.conn, "testproj", "Q-queen")["buildParallel"], 3)
        self.assertAlmostEqual(rows[0]["per_job_mb"], round(rows[0]["peak_mb"] / 3), delta=1)


class RunnerVariablesTest(RunnerBase):
    """B-0, B-7, B-9, B-10b: every agent that builds gets the three variables, the plan and integration builds the computed count."""

    def setUp(self):
        """The machine that runs the tests may have the build variables set: the runner must not inherit them."""
        super().setUp()
        for key in ("BUILD_MP_JOBS", "BUILD_JOBS", "BUILD_PARALLEL", "BUILD_MIN_FREE_RAM_GB"):
            self.env.pop(key, None)

    def test_every_building_agent_gets_the_variables(self):
        """Integrate, release, Queen and Bee agents get the job count in the configured variable, BUILD_PARALLEL and the minimum free RAM; a work agent none."""
        self.write_config('[buildJobs]\nenvVar = "BUILD_MP_JOBS"\ncores = 4\nparallel = 2\nminFreeRamGB = 0.5\nperJobMB = 1\n')
        expected = str(max(1, min(4, os.cpu_count() or 1) // 2))
        for i, phase in enumerate(("integrate", "release", "queen", "bee")):
            name = "V-%d" % i
            self.prepare(name)
            self.run_runner(name, "claude", claude_stream(), phase=phase, env={"BZZ_SAMPLE_SEC": "0.2"})
            rec = self.record()
            self.assertEqual((rec["mp_jobs"], rec["parallel"], rec["min_ram"]), (expected, "2", "0.5"), phase)
        self.prepare("V-work")
        self.run_runner("V-work", "claude", claude_stream(), phase="work")
        self.assertEqual((self.record()["mp_jobs"], self.record()["parallel"]), (None, None))

    def test_a_bee_is_not_a_build_agent_of_its_own(self):
        """A Bee gets the variables but no job count in its row and no measurement."""
        self.write_config("[buildJobs]\ncores = 2\n")
        self.prepare("B-bee")
        self.run_runner("B-bee", "claude", claude_stream(), phase="bee", env={"BZZ_SAMPLE_SEC": "0.2"})
        self.assertNotIn("buildJobs", self.row("B-bee"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM build_measurements").fetchone()[0], 0)

    def test_agent_start_counts_compile_checks_and_waits_for_cores(self):
        """Startup uses the current shared core budget before exporting build variables."""
        self.write_config('[buildJobs]\nenvVar = "BUILD_MP_JOBS"\ncores = 8\nparallel = 2\nfirstJobs = 8\n')
        (self.base / 'stream.txt').write_text(claude_stream(), encoding='utf-8')
        for name, counts in (('checks-running', [2]), ('checks-full', [8, 2])):
            with self.subTest(name=name):
                (self.base / 'record.json').unlink(missing_ok=True)
                self.prepare(name)
                script = ('import sys\nfrom haivequeen import agent_runner as ar, buildjobs\n'
                          'ar.osplat.logical_cores = lambda: 8\n'
                          'ar.osplat.free_ram_mb = lambda: 64000\n'
                          'counts = iter(%r)\n' % counts +
                          'buildjobs._running_checks = lambda: next(counts, 2)\n'
                          'sys.exit(ar.main(sys.argv[1:]))\n')
                result = subprocess.run([sys.executable, '-c', script,
                    '--project', 'demo', '--name', name, '--provider', 'claude',
                    '--plan', 'plans/Demo-PLAN.md', '--package', 'P1', '--phase', 'bee',
                    '--cwd', str(self.ws), '--prompt-file', str(self.prompt), '--runs-dir', str(self.runs)],
                    cwd=str(ROOT), env=self.env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.record()['mp_jobs'], self.record()['parallel']), ('1', '2'))
                self.assertEqual('Waiting for build cores' in result.stdout, counts[0] == 8)


class InstructionsTest(RoundCase):
    """B-7, B-9: the build instructions of the agents name the variables and say that the values win over a project rule."""

    def test_the_rule_text(self):
        """The text names the job count variable, the parallel variable and the minimum RAM, says they win and that no question is asked."""
        text = bj.rule_text({"envVar": "BUILD_MP_JOBS", "minFreeRamGB": 3})
        for part in ("BUILD_MP_JOBS", "BUILD_PARALLEL", "--parallel", "3 GB", "win over", "do not ask a question"):
            self.assertIn(part, text)
        self.assertEqual(bj.rule_text(None), "")

    def test_bee_queen_release_and_integrator_prompts(self):
        """The Bee task, the Queen order and the release and integrator prompts carry the build instructions."""
        env = self.env()
        env.settings["buildJobs"] = {"envVar": "BUILD_MP_JOBS", "parallel": 2}
        from haivequeen import bees
        self.assertIn("BUILD_PARALLEL", bees.bee_prompt("Anna", "P1", "task", ["s1"], ["a"], ["r"], bj.rule_text(env.settings["buildJobs"])))
        self.assertNotIn("{{", bees.bee_prompt("Anna", "P1", "task", ["s1"], ["a"], ["r"]))
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen")
        text = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8")
        for part in ("Parallel as the --parallel value", "bzz integration acquire"):
            self.assertNotIn(part, text)   # the Queen builds nothing (O-1); the Orchestrator applies the job values before Build and Deploy


if __name__ == "__main__":
    unittest.main()
