"""K1 of HQ-COMPILE-CHECK-FIX (K-1, K-2, K-4): the General setting "Max concurrent compile checks" replaces the single machine-wide compile lock;
a compile check starts only while a slot, enough free RAM and a free core are available, counting running builds and compile checks together."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bzztest  # noqa: F401  (puts this checkout first on the path)
from haivequeen import buildjobs, gui_config, integration, scheduler
from test_p2_project_page import WindowCase, descendants

KEY = "maxConcurrentCompileChecks"
LABEL = "Max concurrent compile checks"
BIG = 1 << 20   # MB of free RAM that never limits


class SettingTests(WindowCase):
    """K-1: shown on the General page with its explanation, whole numbers from 1, default 2, still set after a restart."""

    def test_general_page_shows_the_setting_with_default_2_and_an_explanation(self):
        win = self.window()
        shown = {path: getter() for path, getter, _like in win.getters}
        self.assertEqual(shown[(KEY,)], "2")
        texts = {w.cget("text") for w in descendants(win.form) if w.winfo_class() == "Label"}
        self.assertIn(LABEL, texts)
        explanation = gui_config.GENERAL_HELP[KEY]
        self.assertIn(explanation, texts)
        for word in ("same time", "computer"):
            self.assertIn(word, explanation)
        self.assertEqual(gui_config.GENERAL_LABELS[KEY], LABEL)

    def test_only_whole_numbers_from_1_are_accepted(self):
        for bad in ("0", "-1", "1.5", "two", ""):
            win = self.window()
            self.set_entry(win, (KEY,), bad)
            self.assertFalse(win.collect() and not gui_config.check(win.data), bad)
            win.win.destroy()
        for problem in (0, -3, 1.5, "2", True):
            self.assertTrue(gui_config.check({KEY: problem}), problem)
        for fine in (1, 2, 9):
            self.assertEqual(gui_config.check({KEY: fine}), [])

    def test_the_value_is_saved_and_still_set_after_a_restart(self):
        win = self.window()
        self.set_entry(win, (KEY,), "4")
        self.assertTrue(win.collect())
        self.assertEqual(win.data[KEY], 4)
        win.save()
        win.win.destroy()
        self.assertEqual(gui_config.load_raw(self.base / "config.toml")[KEY], 4)
        again = self.window()
        self.assertEqual({path: getter() for path, getter, _like in again.getters}[(KEY,)], "4")
        self.assertEqual(scheduler.build_settings(gui_config.load_raw(self.base / "config.toml"), {})[KEY], 4)

    def test_default_machine_wide_and_no_second_new_setting(self):
        self.assertEqual(scheduler.build_settings({}, {})[KEY], 2)
        self.assertEqual(scheduler.build_settings({KEY: 3}, {KEY: 7})[KEY], 3)   # a project cannot change a machine-wide value
        self.assertEqual(integration.compile_limit({KEY: 0}), 2)   # a wrong value never stops all checks
        self.assertEqual(integration.compile_limit({KEY: "x"}), 2)
        self.assertEqual(integration.compile_limit({KEY: 5}), 5)


class SlotCase(unittest.TestCase):
    """Slots in a throw-away machine data folder; RAM and cores are mocked."""

    def setUp(self):
        data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(data.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"BZZ_DATA_DIR": data.name}),
                        mock.patch.object(integration.osplat, "free_ram_mb", side_effect=lambda: self.free),
                        mock.patch.object(integration.osplat, "logical_cores", side_effect=lambda: self.cores),
                        mock.patch.object(integration, "_running_builds", side_effect=lambda: self.builds)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.free, self.cores, self.builds = BIG, 8, 0
        self.slots = []
        self.threads = []
        self.addCleanup(self.release_all)

    def release_all(self):
        for slot in self.slots:
            integration._release_compile_slot(slot, 0.01)
        for thread in self.threads:
            thread.join(5)

    def take(self, **settings):
        """Take a slot now (it must be free)."""
        slot = integration._acquire_compile_slot(settings, 0.01)
        self.slots.append(slot)
        return slot

    def try_take(self, wait=0.4, **settings):
        """Ask for a slot in a thread; returns (event set when it was granted, list that receives the slot)."""
        granted, box = threading.Event(), []

        def run():
            slot = integration._acquire_compile_slot(settings, 0.01)
            self.slots.append(slot)
            box.append(slot)
            granted.set()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)
        granted.wait(wait)
        return granted, box


class LimitTests(SlotCase):
    """K-2, K-4: the configured limit replaces the one-at-a-time lock."""

    def test_with_the_default_two_checks_run_together_and_a_third_waits(self):
        self.take()
        self.take()
        third, _ = self.try_take()
        self.assertFalse(third.is_set(), "a third check started beyond the limit of 2")
        integration._release_compile_slot(self.slots.pop(0), 0.01)
        self.assertTrue(third.wait(5), "the third check did not start after one ended")

    def test_the_configured_limit_is_kept(self):
        one, _ = self.try_take(**{KEY: 1})
        self.assertTrue(one.is_set())
        second, _ = self.try_take(**{KEY: 1})
        self.assertFalse(second.is_set(), "limit 1 still allowed two checks")
        integration._release_compile_slot(self.slots.pop(0), 0.01)
        self.assertTrue(second.wait(5))
        for _ in range(2):
            self.take(**{KEY: 4})
        self.assertEqual(len(integration._compile_holders()), 3)
        fourth, _ = self.try_take(**{KEY: 4})
        self.assertTrue(fourth.is_set())
        fifth, _ = self.try_take(**{KEY: 4})
        self.assertFalse(fifth.is_set())

    def test_the_slot_of_a_dead_process_is_free_again(self):
        path = integration._compile_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        dead = __import__("subprocess").Popen([sys.executable, "-c", "pass"])
        dead.wait()
        path.write_text(json.dumps({"holders": [{"token": "a", "pid": dead.pid}, {"token": "b", "pid": dead.pid}]}), encoding="utf-8")
        granted, _ = self.try_take()
        self.assertTrue(granted.is_set())
        self.assertEqual(len(integration._compile_holders()), 1)

    def test_a_slot_of_a_living_process_counts(self):
        path = integration._compile_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"holders": [{"token": "a", "pid": os.getpid()}, {"token": "b", "pid": os.getpid()}]}), encoding="utf-8")
        granted, _ = self.try_take()
        self.assertFalse(granted.is_set())

    def test_checks_of_different_projects_run_at_the_same_time_and_the_third_waits(self):
        """Three real run_compile_check calls of three projects: two overlap, the third starts after one ended."""
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, str(folder), True)
        log = folder / "log.txt"
        script = folder / "check.py"
        script.write_text("import sys, time\nlog = sys.argv[1]\nopen(log, 'a').write('start %s\\n' % time.time())\ntime.sleep(1.5)\nopen(log, 'a').write('end %s\\n' % time.time())\n", encoding="utf-8")
        results = {}
        from haivequeen import config, db

        def run(name):
            conn = db.connect(config.db_path())
            env = SimpleNamespace(name=name, conn=conn, settings={"compileCheck": '"%s" "%s" "%s"' % (sys.executable, script, log), "compileStallMinutes": 5, "compileMaxMinutes": 5})
            try:
                results[name] = integration.run_compile_check(env, {"name": "Q-" + name}, cwd=str(folder), poll=0.05, milestones=False)
            finally:
                conn.close()
        threads = [threading.Thread(target=run, args=(name,)) for name in ("alpha", "beta", "gamma")]
        for thread in threads:
            thread.start()
            time.sleep(0.15)
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(results), 3, results)
        self.assertTrue(all(r["ok"] for r in results.values()), results)
        events = [(float(line.split()[1]), line.split()[0]) for line in log.read_text(encoding="utf-8").splitlines()]
        events.sort()
        kinds = [kind for _t, kind in events]
        self.assertEqual(kinds[:2], ["start", "start"], kinds)   # two started before any ended
        self.assertEqual(kinds[2], "end", kinds)   # the third started only after one ended
        self.assertTrue(all(r["seconds"] < 3.2 for r in results.values()), results)   # waiting is not part of the run time


class ResourceTests(SlotCase):
    """K-2, K-4: RAM and cores of the three existing settings."""

    BUILD = {"buildJobs": {"cores": 2, "parallel": 1, "minFreeRamGB": 2}, KEY: 5}

    def test_a_check_waits_below_the_minimum_free_ram_and_starts_above_it(self):
        self.free = 1500   # below 2 GB
        granted, _ = self.try_take(**self.BUILD)
        self.assertFalse(granted.is_set(), "the check started with too little free RAM")
        self.free = 2048
        self.assertTrue(granted.wait(5), "the check did not start once the RAM was free")

    def test_the_minimum_free_ram_setting_is_used(self):
        self.free = 3000
        granted, _ = self.try_take(buildJobs={"cores": 2, "minFreeRamGB": 4}, **{KEY: 5})
        self.assertFalse(granted.is_set())
        self.free = 4096
        self.assertTrue(granted.wait(5))

    def test_a_check_waits_while_the_cores_of_a_build_are_used_up(self):
        self.cores = 8
        self.take(**self.BUILD)   # one check uses one of the 2 cores
        second, _ = self.try_take(**self.BUILD)
        self.assertTrue(second.is_set())   # a running check and a new one fit into 2 cores
        third, _ = self.try_take(**self.BUILD)
        self.assertFalse(third.is_set(), "a check started with all CPU cores of CPU cores per build used")
        integration._release_compile_slot(self.slots.pop(0), 0.01)
        self.assertTrue(third.wait(5), "the check did not start once a core was free")

    def test_running_builds_and_checks_share_the_cores_together(self):
        self.builds = 2   # two builds use both cores
        granted, _ = self.try_take(**self.BUILD)
        self.assertFalse(granted.is_set(), "a check started while builds used all cores")
        self.builds = 1
        self.assertTrue(granted.wait(5))
        more, _ = self.try_take(**self.BUILD)   # one build and one check use both cores
        self.assertFalse(more.is_set())
        self.builds = 0
        self.assertTrue(more.wait(5))

    def test_the_cores_never_exceed_the_cores_of_the_machine(self):
        self.cores = 1   # "CPU cores per build" 4 counts as 1
        self.take(buildJobs={"cores": 4}, **{KEY: 5})
        second, _ = self.try_take(buildJobs={"cores": 4}, **{KEY: 5})
        self.assertFalse(second.is_set())

    def test_without_a_build_block_the_defaults_of_the_three_settings_apply(self):
        self.free = 1000
        granted, _ = self.try_take()
        self.assertFalse(granted.is_set())   # 2 GB default
        self.free = BIG
        self.assertTrue(granted.wait(5))

    def test_running_checks_take_cores_from_the_builds_that_start(self):
        """A build started while a check runs gets the cores divided by the number of builds and checks together; its meaning stays otherwise unchanged."""
        from haivequeen import db
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        conn = db.connect(Path(folder.name) / "t.db")
        self.addCleanup(conn.close)
        env = SimpleNamespace(conn=conn, name="proj", settings={"buildJobs": {"cores": 4, "firstJobs": 8}})
        alone = buildjobs.plan_build(env, free_mb=BIG, cores=8)["Jobs"]
        self.assertEqual(alone, 4)
        self.take(**self.BUILD)
        self.assertEqual(buildjobs.plan_build(env, free_mb=BIG, cores=8)["Jobs"], 2)   # 4 cores shared with the running check
        self.take(**self.BUILD)
        self.assertEqual(buildjobs.plan_build(env, free_mb=BIG, cores=8)["Jobs"], 1)   # 4 // (2 checks + this build)


class CheckStepTests(unittest.TestCase):
    """K-2: the Check step of the Orchestrator takes a slot like every other compile check."""

    def test_the_check_step_waits_for_a_slot_and_uses_the_limit(self):
        from haivequeen import orchestrator
        data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(data.cleanup)
        with mock.patch.dict(os.environ, {"BZZ_DATA_DIR": data.name}), mock.patch.object(integration.osplat, "free_ram_mb", return_value=BIG), \
                mock.patch.object(integration, "_running_builds", return_value=0), mock.patch.object(orchestrator, "POLL", 0.05):
            settings = {KEY: 2, "compileStallMinutes": 5, "compileMaxMinutes": 5}
            first = integration._acquire_compile_slot(settings, 0.01)   # one other check runs: the Check step still starts (limit 2)
            try:
                with tempfile.TemporaryDirectory() as folder:
                    ran = []
                    for index, hold in enumerate((0, 1)):
                        extra = [integration._acquire_compile_slot(settings, 0.01) for _ in range(hold)]
                        result = Path(folder) / ("r%d.json" % index)
                        job = orchestrator.Job("t%d" % index)
                        thread = threading.Thread(target=orchestrator._work, args=(settings, job, "Check", '"%s" -c "pass"' % sys.executable, folder, None, str(result)), daemon=True)
                        thread.start()
                        thread.join(1.0 if hold == 0 else 0.5)
                        ran.append(not thread.is_alive())
                        for slot in extra:
                            integration._release_compile_slot(slot, 0.01)
                        thread.join(10)
                        self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["code"], 0)
                    self.assertEqual(ran, [True, False], "the Check step must run with one other check and wait with two")
            finally:
                integration._release_compile_slot(first, 0.01)


if __name__ == "__main__":
    unittest.main()
