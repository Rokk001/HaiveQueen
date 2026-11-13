"""The terminal interface is gone: no terminal modules, no terminal options, `bzz start` always starts the loop hidden and opens the browser,
and every agent starts through the one hidden-process path."""
import argparse
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import cli, config, loop, runtime
from haivequeen import scheduler as sch

PACKAGE = Path(__file__).resolve().parent.parent / "haivequeen"


# No leftovers of the terminal interface.
class NoLeftoversTest(unittest.TestCase):
    """The removed modules cannot be imported and the code no longer names the terminal machinery."""

    def test_removed_modules_are_gone(self):
        """terminal (wt, tmux, headless backend), dashboard (text), header, hivepane, hivelayout and ansi_svg do not exist any more."""
        for name in ("terminal", "dashboard", "header", "hivepane", "hivelayout", "ansi_svg"):
            # The files are checked, not find_spec: an editable install of another checkout can still resolve the name.
            self.assertFalse((PACKAGE / name).exists() or (PACKAGE / (name + ".py")).exists(), name)

    def test_source_has_no_terminal_machinery(self):
        """No source file of the package names the terminal backends, pane functions or the removed options."""
        banned = re.compile(r"get_terminal|TerminalError|pane_lock|ensure_hive_pane|ensure_dashboard|open_session_panes|BZZ_TERMINAL|"
                            r"haivequeen\.header|haivequeen\.hivepane|haivequeen\.dashboard|--wrap|BZZ_NO_HEADER")
        for path in PACKAGE.glob("*.py"):
            hits = banned.findall(path.read_text(encoding="utf-8"))
            self.assertEqual(hits, [], path.name)

    def test_no_terminal_settings(self):
        """The settings ui, terminal and window no longer exist in the defaults."""
        for key in ("ui", "terminal", "window"):
            self.assertNotIn(key, config.DEFAULT_CONFIG)
            self.assertNotIn(key, sch.DEFAULT_SETTINGS)


# The command line.
class StartOptionsTest(unittest.TestCase):
    """bzz start has no --ui and no --terminal."""

    def test_options(self):
        """--ui and --terminal are rejected; the remaining options parse."""
        parser = cli.build_parser()
        for bad in (["start", "--ui", "gui"], ["start", "--terminal", "headless"]):
            with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                parser.parse_args(bad)
        a = parser.parse_args(["start", "--foreground", "--rounds", "2", "--round-minutes", "1", "--keep-pause", "--desktop"])
        self.assertEqual((a.foreground, a.rounds, a.round_minutes, a.keep_pause, a.desktop), (True, 2, 1.0, True, True))
        self.assertTrue(parser.parse_args(["gui", "--detach"]).detach)

    def test_boot_command_has_no_terminal_option(self):
        """The command of the hidden loop process is `start --foreground` plus the limits only."""
        args = argparse.Namespace(rounds=3, round_minutes=0, keep_pause=True)
        cmd = loop.start_boot_command(args)
        self.assertEqual(cmd[-5:], ["start", "--foreground", "--rounds", "3", "--keep-pause"][-5:])
        self.assertNotIn("--terminal", cmd)


# bzz start with a temp runtime.
class StartTest(RoundCase):
    """cmd_start spawns the loop detached and opens the browser once; nothing else is opened."""

    def args(self, **kw):
        """Parsed arguments of `bzz start`."""
        base = dict(foreground=False, rounds=0, round_minutes=0, keep_pause=False, desktop=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_start_is_always_headless_and_opens_the_browser(self):
        """One hidden loop process and one browser; the call uses no terminal program."""
        out = {}
        with mock.patch.object(loop, "spawn_detached") as spawn, \
                mock.patch("haivequeen.web_service.open_browser", return_value={"ok": True, "web": "http://127.0.0.1:8484", "already": False}) as gui, \
                mock.patch("subprocess.Popen") as popen, mock.patch("builtins.print"):
            args = self.args()
            self.assertEqual(loop.cmd_start(args, self.conn), 0)
            out = args.desktop_result
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(gui.call_count, 1)
        popen.assert_not_called()
        argv = spawn.call_args[0][0]
        self.assertIn("--foreground", argv)
        self.assertNotIn("--terminal", argv)
        self.assertEqual((out["ok"], out["web"]), (True, "http://127.0.0.1:8484"))
        self.assertNotIn("panes", out)
        self.assertNotIn("window", out)

    def test_running_loop_only_opens_the_browser(self):
        """With a loop already running the call starts no second loop but still opens the browser once."""
        with mock.patch.object(loop, "loop_running", return_value=4711), mock.patch.object(loop, "spawn_detached") as spawn, \
                mock.patch("haivequeen.web_service.open_browser", return_value={"ok": True, "web": "http://127.0.0.1:8484", "already": True}) as gui, mock.patch("builtins.print"):
            args = self.args()
            loop.cmd_start(args, self.conn)
        spawn.assert_not_called()
        self.assertEqual(gui.call_count, 1)
        self.assertEqual(args.desktop_result["pid"], 4711)

    def test_stop_reports_without_panes(self):
        """bzz stop only sets the STOP flag."""
        with mock.patch("builtins.print"):
            args = argparse.Namespace(desktop=False)
            loop.cmd_stop(args, self.conn)
        self.assertTrue(loop.stop_flag().exists())
        self.assertEqual(set(args.desktop_result), {"ok", "running_agents"})


# Starting agents.
class LaunchTest(RoundCase):
    """Every agent starts through launch_headless: no wrapper argument, a hidden process, output in runner.log."""

    def spec(self, phase):
        """The start description of an agent of the dummy project."""
        name = "DUMMY-%s" % phase
        return {"name": name, "provider": "claude", "protocol": "claude", "plan": REL, "package": "" if phase == "queen" else "P1", "phase": phase,
                "cwd": str(self.wt), "prompt_file": str(self.wt / "prompt.md")}

    def test_runner_argv_has_no_wrapper(self):
        """The runner command line runs the runner itself (no --wrap) and leaves out empty arguments."""
        argv = loop.runner_argv(self.env(), self.spec("queen"))
        self.assertNotIn("--wrap", argv)
        self.assertNotIn("--package", argv)
        self.assertIn("--phase", argv)

    def test_launch_headless_hides_the_window_and_writes_runner_log(self):
        """The process starts hidden with its output in runner.log of the run folder."""
        env = self.env()
        spec = self.spec("bee")
        env.run_dir(spec["name"]).mkdir(parents=True)
        with mock.patch("subprocess.Popen") as popen:
            loop.launch_headless(env, spec)
        kwargs = popen.call_args[1]
        if sys.platform == "win32":
            self.assertEqual(kwargs["creationflags"] & 0x08000000, 0x08000000)   # CREATE_NO_WINDOW
        self.assertEqual(kwargs["stdin"], -3)   # subprocess.DEVNULL
        self.assertTrue((env.run_dir(spec["name"]) / "runner.log").is_file())

    def test_every_phase_uses_launch_headless(self):
        """_do_start hands a Queen, a Bee and a work agent to launch_headless, and to nothing else."""
        os.environ.pop("BZZ_TEST_NOSTART", None)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        env = self.env()
        for phase in ("queen", "bee", "work"):
            with mock.patch.object(loop, "launch_headless") as launch, mock.patch("time.sleep"):
                action = {"name": "DUMMY-%s-x" % phase, "type": "claude", "phase": phase, "plan": REL, "package": "P1", "cwd": str(self.wt), "prompt": "do it"}
                ctx = {"env": env, "state": {"names": [], "busy": [], "plan_status": {sch.norm_plan(REL): "READY"},
                                             "by_path": {sch.norm_plan(REL): {"path": REL}}, "blocked": [], "startable": [], "all_plans": []},
                       "pool": loop.Pool({"claude": 2, "codex": 2}, 1), "report": [], "dry": False, "settle": 0}
                pick = next(p for p in env.providers if p["name"] == "claude")
                self.assertEqual(loop._do_start(ctx, action, forced_pick=pick), 1, phase)
            self.assertEqual(launch.call_count, 1, phase)
            spec = launch.call_args[0][1]
            self.assertEqual(spec["phase"], phase)
            self.assertEqual(set(spec), {"name", "provider", "protocol", "plan", "package", "phase", "cwd", "prompt_file"})
        self.assertTrue(runtime.list_agents(self.conn, "testproj", "all"))


if __name__ == "__main__":
    unittest.main()
