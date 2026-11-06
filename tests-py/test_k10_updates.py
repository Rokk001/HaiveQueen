"""K10, H-42 and H-46(i): daily and manual update check, confirmed install of a release, drain of agents and restart."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from bzztest import RoundCase
from haivequeen import bees, config, desktop, gui_config, i18n, loop, selfupdate, updates
from haivequeen import platform as osplat


class CheckTests(RoundCase):
    """The daily check asks GitHub once per interval; Check now always asks; the result survives a restart."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("BZZ_NO_UPDATE_CHECK", None)   # the tests switch the automatic check off; these tests are about it
        self.calls = []
        self.now = datetime(2026, 11, 6, 9, 0)

    def lookup(self, repository, current):
        self.calls.append((repository, current))
        return {"version": "2.0.2", "url": "https://github.com/o/r/releases/tag/v2.0.2"}

    def check(self, now, manual=False, machine=None):
        return updates.check(machine or {"update": {"checkHours": 24, "repository": "o/r"}}, now=now, manual=manual,
                             lookup=self.lookup, current="2.0.1")

    def test_default_interval_is_24_hours_in_the_machine_settings(self):
        self.assertEqual(config.DEFAULT_CONFIG["update"]["checkHours"], 24)
        self.assertEqual(updates.check_hours({}), 24)
        self.assertEqual(updates.check_hours({"update": {"checkHours": 6}}), 6)

    def test_daily_check_runs_once_per_interval_and_persists(self):
        state = self.check(self.now)
        self.assertEqual(state["available"]["version"], "2.0.2")
        self.assertIsNone(self.check(self.now + timedelta(hours=23)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(updates.load_state()["available"]["version"], "2.0.2")   # read back from the state file
        self.assertIsNotNone(self.check(self.now + timedelta(hours=24)))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], ("o/r", "2.0.1"))

    def test_check_now_always_asks(self):
        self.check(self.now)
        self.assertIsNotNone(self.check(self.now + timedelta(minutes=1), manual=True))
        self.assertEqual(len(self.calls), 2)

    def test_zero_interval_switches_the_daily_check_off_but_not_check_now(self):
        machine = {"update": {"checkHours": 0, "repository": "o/r"}}
        self.assertIsNone(self.check(self.now, machine=machine))
        self.assertIsNotNone(self.check(self.now, manual=True, machine=machine))

    def test_failed_check_is_recorded_and_throttled(self):
        def offline(repository, current):
            self.calls.append(repository)
            raise OSError("offline")
        state = updates.check({"update": {"repository": "o/r"}}, now=self.now, lookup=offline, current="2.0.1")
        self.assertIn("offline", state["error"])
        self.assertIsNone(state["available"])
        self.assertIsNone(updates.check({"update": {"repository": "o/r"}}, now=self.now + timedelta(hours=1), lookup=offline, current="2.0.1"))
        self.assertEqual(len(self.calls), 1)

    def test_no_newer_release_clears_the_offer(self):
        self.check(self.now)
        state = updates.check({"update": {"repository": "o/r"}}, now=self.now, manual=True, lookup=lambda r, c: None, current="2.0.2")
        self.assertIsNone(state["available"])
        self.assertIsNone(updates.offered())

    def test_repository_from_the_origin_of_the_install_folder(self):
        run = lambda argv, cwd: (0, "git@github.com:someone/HaiveQueen.git\n")
        self.assertEqual(updates.repository({}, live="x", run=run), "someone/HaiveQueen")
        self.assertEqual(updates.repository({"update": {"repository": "a/b"}}, live="x", run=run), "a/b")
        self.assertEqual(updates.repository({}, live="x", run=lambda argv, cwd: (1, "")), updates.DEFAULT_REPOSITORY)


class VersionTests(unittest.TestCase):
    """The installed version is the release tag of the install folder; without one, the version of pyproject.toml."""

    def git(self, *args):
        subprocess.run(["git", "-C", self.dir, *args], check=True, capture_output=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        Path(self.dir, "pyproject.toml").write_text('[project]\nname = "haivequeen"\nversion = "2.0.0"\n', encoding="utf-8")
        self.git("init", "-q")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "pyproject.toml")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "first")

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_tag_the_pyproject_version(self):
        self.assertEqual(updates.installed_version(self.dir), "2.0.0")

    def test_release_tag(self):
        self.git("tag", "v2.0.3")
        self.assertEqual(updates.installed_version(self.dir), "2.0.3")

    def test_header_shows_the_installed_version(self):
        from haivequeen import gui_data
        gui_data._VERSION_CACHE.clear()
        with mock.patch("haivequeen.updates.installed_version", return_value="2.0.7"):
            self.assertEqual(gui_data._version(), "2.0.7")


class InstallTests(RoundCase):
    """The update button installs only the offered release, through the detached worker that waits for all agents."""

    def offer(self, version="2.0.2"):
        updates.save_state({"last_check": "2026-11-06T09:00:00", "available": {"version": version, "url": ""}, "error": ""})

    def test_install_refuses_a_version_that_is_not_offered(self):
        spawned = []
        with self.assertRaises(ValueError):
            updates.request_install("2.0.2", spawn=lambda version: spawned.append(version))
        self.offer()
        with self.assertRaises(ValueError):
            updates.request_install("2.0.3", spawn=lambda version: spawned.append(version))
        self.assertEqual(spawned, [])

    def test_install_spawns_the_worker_with_the_version_and_remembers_the_last_run(self):
        self.offer()
        spawned = []
        updates.request_install("2.0.2", spawn=lambda version: spawned.append(version) or {"how": "detached"})
        self.assertEqual(spawned, ["2.0.2"])
        self.assertEqual(updates.load_state()["installing"]["version"], "2.0.2")

    def test_worker_command_line_names_the_release(self):
        argv = selfupdate.worker_argv("2.0.2")
        self.assertEqual(argv[-3:], ["--worker", "--version", "2.0.2"])
        self.assertEqual(selfupdate.worker_argv()[-1], "--worker")   # the legacy self-update stays (H-46 j)

    def fake_run(self, heads):
        calls = []

        def run(argv, cwd):
            calls.append(argv)
            if argv[-2:] == ["rev-parse", "HEAD"]:
                return 0, heads.pop(0) if len(heads) > 1 else heads[0]
            if "start" in argv:
                return 0, ""
            return 0, ""
        return calls, run

    def test_release_update_fetches_the_tag_merges_it_and_never_pushes(self):
        calls, run = self.fake_run(["a" * 40, "b" * 40])
        with mock.patch.object(selfupdate, "wait_first_round", return_value=True), mock.patch.object(selfupdate, "lock_path", return_value=self.base / "lock"):
            res = selfupdate.run_update(self.conn, live=self.base, run=run, sleep=lambda s: None, alive=lambda p: False,
                                        loop_pid=lambda: None, version="2.0.2")
        self.assertEqual(res["result"], "ok")
        joined = [" ".join(a) for a in calls]
        self.assertTrue(any("fetch origin tag v2.0.2" in j for j in joined), joined)
        self.assertTrue(any("merge --ff-only v2.0.2" in j for j in joined), joined)
        self.assertFalse(any(" push " in j for j in joined), joined)
        self.assertTrue(any(a[-2:] == ["start", "--keep-pause"] for a in calls), joined)

    def test_restart_of_the_worker_is_marked_by_a_variable_not_an_argument(self):
        """The restart keeps the command line `start --keep-pause` (also code from before a rollback understands it) and carries BZZ_UPDATE_RESTART."""
        seen = []

        def run(argv, cwd):
            if "start" in argv:
                seen.append((argv[-2:], os.environ.get(selfupdate.RESTART_VAR)))
            if argv[-2:] == ["rev-parse", "HEAD"]:
                return 0, "a" * 40
            return 0, ""
        with mock.patch.object(selfupdate, "wait_first_round", return_value=True), mock.patch.object(selfupdate, "lock_path", return_value=self.base / "lock"):
            res = selfupdate.run_update(self.conn, live=self.base, run=run, sleep=lambda s: None, alive=lambda p: False, loop_pid=lambda: None, version="2.0.2")
        self.assertEqual(res["result"], "ok")
        self.assertEqual(seen, [(["start", "--keep-pause"], "1")])
        self.assertIsNone(os.environ.get(selfupdate.RESTART_VAR))

    def test_release_update_rejects_a_bad_version(self):
        with self.assertRaises(ValueError):
            selfupdate.run_update(self.conn, live=self.base, run=lambda a, c: (0, ""), version="2.0; rm")

    def test_legacy_self_update_still_merges_the_integration_branch_and_pushes(self):
        calls, run = self.fake_run(["a" * 40, "b" * 40])
        with mock.patch.object(selfupdate, "wait_first_round", return_value=True), mock.patch.object(selfupdate, "lock_path", return_value=self.base / "lock"):
            selfupdate.run_update(self.conn, live=self.base, run=run, sleep=lambda s: None, alive=lambda p: False, loop_pid=lambda: None)
        joined = [" ".join(a) for a in calls]
        self.assertTrue(any("merge --ff-only origin/main" in j for j in joined), joined)
        self.assertTrue(any("push origin main" in j for j in joined), joined)

    def test_finished_install_is_recognised(self):
        state = {"installing": {"version": "2.0.2", "after_row": 4}}
        self.assertIsNone(updates.install_finished(state, None))
        self.assertIsNone(updates.install_finished(state, {"id": 4, "result": "ok", "ended": "x"}))
        self.assertIsNone(updates.install_finished(state, {"id": 5, "result": None, "ended": None}))
        self.assertEqual(updates.install_finished(state, {"id": 5, "result": "ok", "ended": "x"}), "ok")
        self.assertEqual(updates.install_finished(state, {"id": 5, "result": "rollback", "ended": "x"}), "failed")
        self.assertIsNone(updates.install_finished({}, {"id": 5, "result": "ok", "ended": "x"}))


class HeaderTests(RoundCase):
    """The GUI header shows Check now, "New version available" with the update button, the waiting install and a failure."""

    def test_header_line_states(self):
        self.assertEqual(updates.header_line({})[2], "check")
        self.assertEqual(updates.header_line({}, checking=True)[2], None)
        text, level, action = updates.header_line({"available": {"version": "2.0.2"}})
        self.assertEqual((action, level), ("install", "warn"))
        self.assertIn("2.0.2", text)
        self.assertIn("New version available", text)
        self.assertIsNone(updates.header_line({"installing": {"version": "2.0.2"}, "available": {"version": "2.0.2"}})[2])
        self.assertEqual(updates.header_line({"failed": "2.0.2"})[1:], ("bad", "check"))
        self.assertEqual(updates.header_line({"error": "offline", "last_check": "x"})[1:], ("bad", "check"))

    def test_settle_install(self):
        updates.save_state({"available": {"version": "2.0.2"}, "installing": {"version": "2.0.2", "after_row": 3}})
        self.assertIsNone(updates.settle_install({"id": 4, "ended": None, "result": None}))
        self.assertEqual(updates.settle_install({"id": 4, "ended": "x", "result": "rollback"}), "failed")
        self.assertEqual(updates.load_state()["failed"], "2.0.2")
        self.assertNotIn("installing", updates.load_state())
        updates.save_state({"available": {"version": "2.0.2"}, "installing": {"version": "2.0.2", "after_row": 3}})
        self.assertEqual(updates.settle_install({"id": 4, "ended": "x", "result": "ok"}), "ok")
        self.assertIsNone(updates.offered())

    def test_due(self):
        now = datetime(2026, 11, 6, 9, 0)
        with mock.patch.dict(os.environ, {"BZZ_NO_UPDATE_CHECK": ""}):
            self.assertTrue(updates.due({}, {}, now))
            self.assertFalse(updates.due({}, {"last_check": "2026-11-06T08:00:00"}, now))
            self.assertTrue(updates.due({}, {"last_check": "2026-11-05T09:00:00"}, now))
            self.assertFalse(updates.due({"update": {"checkHours": 0}}, {}, now))
        with mock.patch.dict(os.environ, {"BZZ_NO_UPDATE_CHECK": "1"}):
            self.assertFalse(updates.due({}, {}, now))


class DrainTests(RoundCase):
    """H-46(i): while the update waits for running agents no Queen or Bee starts."""

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)

    def hold_lock(self, pid):
        path = selfupdate.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding="ascii")

    def test_draining_while_a_living_worker_holds_the_lock(self):
        self.assertFalse(updates.draining())
        self.hold_lock(os.getpid())
        self.assertTrue(updates.draining(alive=lambda pid: True))
        self.assertFalse(updates.draining(alive=lambda pid: False))

    def test_start_is_refused_while_draining_except_for_the_restart_of_the_update(self):
        self.hold_lock(4242)
        args = Namespace(foreground=True, rounds=1, round_minutes=None, keep_pause=True, json=True, desktop=False)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch.object(loop, "run_loop", return_value=0) as run_loop,                 mock.patch("builtins.print"):
            loop.cmd_start(args, self.conn)
            run_loop.assert_not_called()
            with mock.patch.dict(os.environ, {selfupdate.RESTART_VAR: "1"}):
                loop.cmd_start(args, self.conn)
            run_loop.assert_called_once()

    def test_start_called_by_the_update_worker_is_allowed_without_the_variable(self):
        """A worker of an older version restarts without BZZ_UPDATE_RESTART: its child `start` is recognized by the parent process, else the loop never starts (rollback after 1800 s)."""
        self.hold_lock(4242)
        args = Namespace(foreground=True, rounds=1, round_minutes=None, keep_pause=True, json=True, desktop=False)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch.object(loop, "run_loop", return_value=0) as run_loop,                 mock.patch("os.getppid", return_value=4242), mock.patch("builtins.print"):
            loop.cmd_start(args, self.conn)
        run_loop.assert_called_once()

    def test_background_start_of_the_worker_passes_the_allowance_to_the_loop_process(self):
        """The detached `start --foreground` of the loop is no child of the worker: it gets BZZ_UPDATE_RESTART from the start the worker called."""
        self.hold_lock(4242)
        args = Namespace(foreground=False, rounds=0, round_minutes=0, keep_pause=True, json=True, desktop=False)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch.object(loop, "loop_running", return_value=None),                 mock.patch.object(loop, "spawn_detached") as spawn, mock.patch("haivequeen.gui.launch_detached", return_value={}),                 mock.patch("os.getppid", return_value=4242), mock.patch("builtins.print"):
            loop.cmd_start(args, self.conn)
        self.assertEqual(spawn.call_args.kwargs.get("env"), {selfupdate.RESTART_VAR: "1"})

    def test_start_of_another_process_is_still_refused_while_draining(self):
        """Only the child of the lock holder passes: a start from any other parent is refused."""
        self.hold_lock(4242)
        args = Namespace(foreground=True, rounds=1, round_minutes=None, keep_pause=True, json=True, desktop=False)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch.object(loop, "run_loop", return_value=0) as run_loop,                 mock.patch("os.getppid", return_value=999), mock.patch("builtins.print"):
            loop.cmd_start(args, self.conn)
        run_loop.assert_not_called()

    def refused(self, **kw):
        """Run `start` as a call that is refused; return (exit code, printed payload)."""
        args = Namespace(foreground=kw.pop("foreground", False), rounds=0, round_minutes=0, keep_pause=True, json=True, desktop=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = loop.cmd_start(args, self.conn)
        return code, json.loads(out.getvalue())

    def reasons(self):
        """The refusal lines in the events and in beekeeper.log."""
        events = [r["text"] for r in self.conn.execute("SELECT text FROM events WHERE kind='start-refused'")]
        log = osplat.runs_dir() / "beekeeper.log"
        return events, (log.read_text(encoding="utf-8") if log.exists() else "")

    def test_refused_start_while_updating_exits_with_1_and_names_the_reason(self):
        """A refusal is no success: exit 1, and the reason is an event and a line of beekeeper.log (the worker must not wait 1800 s for a loop that never starts)."""
        self.hold_lock(4242)
        with mock.patch("haivequeen.updates.draining", return_value=True), mock.patch("os.getppid", return_value=999):
            code, payload = self.refused()
        events, log = self.reasons()
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["updating"])
        self.assertEqual(len(events), 1)
        self.assertIn("update", events[0])
        self.assertIn(events[0], log)

    def test_refused_start_during_a_pending_stop_exits_with_1(self):
        loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
        loop.stop_flag().write_text("stop requested\n", encoding="utf-8")
        with mock.patch.object(loop, "loop_running", return_value=4711):
            code, payload = self.refused()
        events, log = self.reasons()
        self.assertEqual((code, payload["ok"], payload["stopping"]), (1, False, True))
        self.assertEqual(len(events), 1)
        self.assertIn(events[0], log)

    def test_refused_start_while_another_start_is_pending_exits_with_1(self):
        with mock.patch.object(loop, "loop_running", return_value=None), mock.patch.object(loop, "start_pending", return_value=True),                 mock.patch.object(loop, "spawn_detached") as spawn:
            code, payload = self.refused()
        events, log = self.reasons()
        spawn.assert_not_called()
        self.assertEqual((code, payload["ok"], payload["starting"]), (1, False, True))
        self.assertEqual(len(events), 1)
        self.assertIn(events[0], log)

    def test_start_with_a_running_loop_still_exits_with_0(self):
        """Not a refusal: the loop runs, the call only opens the window."""
        with mock.patch.object(loop, "loop_running", return_value=4711), mock.patch("haivequeen.gui.launch_detached", return_value={}):
            code, payload = self.refused()
        self.assertEqual((code, payload["ok"], payload["running"]), (0, True, True))
        self.assertEqual(self.reasons()[0], [])

    def test_desktop_text_of_a_refused_stop_or_start_is_the_message_not_an_error(self):
        self.assertIn("2", desktop.result_text("start", {"ok": False, "stopping": True, "running_agents": 2, "error": "x"}, "English"))
        self.assertEqual(desktop.result_text("start", {"ok": False, "starting": True, "error": "x"}, "English"), "HQ is already starting.")
        self.assertEqual(desktop.result_text("start", {"ok": False, "updating": True, "error": "An update is waiting"}, "English"), "Error: An update is waiting")

    def test_worker_sees_a_refused_restart_at_once_and_does_not_wait_for_a_round(self):
        """The restart answers with exit 1: the update fails now instead of after the 1800 s of the first round."""
        def run(argv, cwd):
            if "start" in argv:
                return 1, '{"ok": false, "updating": true}'
            return 0, "a" * 40
        with mock.patch.object(selfupdate, "wait_first_round") as wait, mock.patch.object(selfupdate, "lock_path", return_value=self.base / "lock"):
            res = selfupdate.run_update(self.conn, live=self.base, run=run, sleep=lambda s: None, alive=lambda p: False, loop_pid=lambda: None)
        wait.assert_not_called()
        self.assertIn(res["result"], ("failed", "rollback"))
        self.assertIn("start:", res["note"])

    def test_worker_accepts_a_restart_refused_because_a_start_is_pending(self):
        """Another start is already bringing the loop up: the worker waits for its first round as before."""
        def run(argv, cwd):
            if "start" in argv:
                return 1, '{"ok": false, "starting": true}'
            return 0, "a" * 40
        with mock.patch.object(selfupdate, "wait_first_round", return_value=True) as wait, mock.patch.object(selfupdate, "lock_path", return_value=self.base / "lock"):
            res = selfupdate.run_update(self.conn, live=self.base, run=run, sleep=lambda s: None, alive=lambda p: False, loop_pid=lambda: None)
        wait.assert_called_once()
        self.assertEqual(res["result"], "ok")

    def test_embedded_bee_start_is_refused_while_draining(self):
        self.assertIn("update", bees.drain_refusal(lambda: True))
        self.assertIsNone(bees.drain_refusal(lambda: False))


class TextTests(unittest.TestCase):
    """The update texts of the GUI exist in German."""

    def test_german_texts(self):
        for text in (updates.TEXT_CHECK_NOW, updates.TEXT_AVAILABLE, updates.TEXT_CONFIRM, updates.TEXT_CHECKING, updates.TEXT_INSTALLING, updates.TEXT_FAILED):
            with mock.patch.object(i18n, "is_german", return_value=True):
                self.assertNotEqual(i18n.tr(text, "2.0.2") if "%s" in text else i18n.tr(text), text % "2.0.2" if "%s" in text else text, text)

    def test_check_hours_are_validated_in_the_settings(self):
        self.assertTrue(gui_config.check({"update": {"checkHours": -1}}, True))
        self.assertTrue(gui_config.check({"update": {"checkHours": "x"}}, True))
        self.assertFalse(gui_config.check({"update": {"checkHours": 12}}, True))


class ReadmeTests(unittest.TestCase):
    """H-44: the README describes installation and the update function for users."""

    def test_readme(self):
        text = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
        for part in ("### Updates", "Check now", "New version available", "checkHours", "git checkout v", "publishReleases", "nextVersion", "bzz update"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
