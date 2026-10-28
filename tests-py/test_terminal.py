"""Tests of the terminal backends: factory, quoting, titles, wt and tmux command lines, headless for real."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib.terminal import TerminalError, get_terminal, pane_title  # noqa: E402
from orchlib.terminal import headless, tmux, wt  # noqa: E402
from orchlib.terminal import _proc  # noqa: E402


def wait_for(condition, seconds=10):
    """Poll until the condition is true or the time is up; return the last value."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


class FactoryTest(unittest.TestCase):
    """get_terminal."""

    def test_backends(self):
        """Each name gives its backend; auto follows the platform."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(get_terminal("wt", tmp).name, "wt")
            self.assertEqual(get_terminal("tmux").name, "tmux")
            self.assertEqual(get_terminal("headless", tmp).name, "headless")
            expected = "wt" if sys.platform == "win32" else "tmux"
            self.assertEqual(get_terminal("auto", tmp).name, expected)

    def test_unknown(self):
        """An unknown name is an error."""
        with self.assertRaises(TerminalError):
            get_terminal("screen")

    def test_runs_dir_required(self):
        """Backends that need a runs folder refuse to work without one."""
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("ORCH_RUNS_DIR", None)
            with self.assertRaises(TerminalError):
                get_terminal("headless")


class TitleTest(unittest.TestCase):
    """Pane titles."""

    def test_format(self):
        """The title has the v1 format with the project name."""
        self.assertEqual(pane_title("demo", "Agent ANNA", "work-1", "claude opus 5.5 medium"),
                         "Agent-Orchestrator | demo | Agent ANNA | job: work-1 | model: claude opus 5.5 medium")

    def test_test_prefix(self):
        """A test runtime says TEST."""
        self.assertTrue(pane_title("p", "CHIEF", "Orchestrator", "m", test=True)
                        .startswith("Agent-Orchestrator TEST | p | CHIEF | job: Orchestrator"))


class QuotingTest(unittest.TestCase):
    """Quoting per backend."""

    def test_wt_semicolon(self):
        """A semicolon is escaped so wt does not read it as a sub-command separator."""
        self.assertEqual(wt.wt_quote("a;b"), "a\\;b")

    def test_wt_space_and_quotes(self):
        """Spaces get quotes, embedded quotes are escaped."""
        self.assertEqual(wt.wt_quote("a b"), '"a b"')
        self.assertEqual(wt.wt_quote('say "hi"'), '"say \\"hi\\""')

    def test_wt_all_together(self):
        """Semicolon inside a quoted argument is still escaped."""
        self.assertEqual(wt.wt_quote('x "y;z"'), '"x \\"y\\;z\\""')

    def test_wt_commandline_separator(self):
        """Sub-commands are joined by an unescaped ' ; '."""
        self.assertEqual(wt.wt_commandline([["focus-pane", "-t", "1"], ["split-pane", "a;b"]]),
                         "focus-pane -t 1 ; split-pane a\\;b")

    def test_tmux_command(self):
        """tmux gets one shell string with POSIX quoting."""
        self.assertEqual(tmux.tmux_command(["echo", "a b", "it's", "x;y"]),
                         "echo 'a b' 'it'\"'\"'s' 'x;y'")


class WtArgsTest(unittest.TestCase):
    """Command lines of the wt backend, without starting anything."""

    def setUp(self):
        """Create a backend on a temp folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.term = wt.WindowsTerminal(self.tmp.name, start_timeout=0, profile="PowerShell")

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def test_open_window(self):
        """The window is opened as a named new-tab with fixed title and tab colour."""
        subs = self.term.open_window_args("W1", "T 1", ["py", "-c", "x"], colour="#112233")
        self.assertEqual(subs, [["-w", "W1", "new-tab", "-p", "PowerShell", "--title", "T 1",
                                 "--suppressApplicationTitle", "--tabColor", "#112233", "py", "-c", "x"]])

    def test_split_with_target(self):
        """A target pane is focused first, then split; horizontal means -V (side by side)."""
        subs = self.term.split_pane_args("W1", "T", ["py"], "horizontal", 0.7, target="2")
        self.assertEqual(subs[0], ["-w", "W1", "focus-pane", "-t", "2"])
        self.assertEqual(subs[1][:6], ["split-pane", "-p", "PowerShell", "-V", "-s", "0.7"])
        self.assertIn("--suppressApplicationTitle", subs[1])

    def test_split_vertical(self):
        """Vertical means -H (below)."""
        subs = self.term.split_pane_args("W1", "T", ["py"], "vertical", 0.5)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0][:2], ["-w", "W1"])
        self.assertIn("-H", subs[0])

    def test_bad_direction_and_size(self):
        """Invalid direction or size is refused."""
        with self.assertRaises(TerminalError):
            self.term.split_pane_args("W", "T", ["x"], "diagonal", 0.5)
        with self.assertRaises(TerminalError):
            self.term.split_pane("W", "T", None, ["x"], "horizontal", 1.5)

    def test_launch_command_line(self):
        """The started wt command line wraps the command behind the launcher and escapes semicolons."""
        with mock.patch.object(wt.subprocess, "Popen") as popen:
            pane = self.term.open_window("W9", "Title; one", ["py", "-c", "print(1);print(2)"], name="p0")
        self.assertEqual(pane, "W9.0")
        line = popen.call_args[0][0]
        self.assertIn("-w W9 new-tab", line)
        self.assertIn("Title\\; one", line)
        self.assertIn("print(1)\\;print(2)", line)
        self.assertIn("_panerun.py", line)
        self.assertNotIn("; one", line.replace("\\;", ""))
        with mock.patch.object(wt.subprocess, "Popen"):
            second = self.term.split_pane("W9", "T2", None, ["py"], "vertical", 0.4, target="W9.0", name="p1")
        self.assertEqual(second, "W9.1")
        infos = self.term.list_panes("W9")
        self.assertEqual([i.id for i in infos], ["W9.0", "W9.1"])
        self.assertFalse(any(i.alive for i in infos))

    def test_exe_override(self):
        """ORCH_WT_EXE replaces wt.exe (tests and stubs)."""
        with mock.patch.dict("os.environ", {"ORCH_WT_EXE": "stub.exe"}):
            self.assertEqual(self.term.exe(), "stub.exe")


class TmuxStubTest(unittest.TestCase):
    """tmux backend against a stub for subprocess.run: the argument lists are checked."""

    def setUp(self):
        """Patch subprocess.run in the tmux module."""
        self.calls = []
        self.outputs = []

        def fake_run(args, capture_output=True, text=True):
            """Record the call and return the next prepared output."""
            self.calls.append(args)
            out = self.outputs.pop(0) if self.outputs else ""
            return subprocess.CompletedProcess(args, 0, out, "")

        patcher = mock.patch.object(tmux.subprocess, "run", fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.term = tmux.Tmux()

    def test_open_window(self):
        """new-session is detached, named, prints the pane id; the title is set afterwards."""
        self.outputs = ["%0\n"]
        pane = self.term.open_window("sess", "Title", ["py", "-c", "print('a b')"], colour="colour17")
        self.assertEqual(pane, "%0")
        self.assertEqual(self.calls[0], ["tmux", "new-session", "-d", "-s", "sess", "-x", "200", "-y", "50",
                                         "-P", "-F", "#{pane_id}", "py -c 'print('\"'\"'a b'\"'\"')'"])
        self.assertIn(["tmux", "select-pane", "-t", "%0", "-T", "Title"], self.calls)
        self.assertIn(["tmux", "select-pane", "-t", "%0", "-P", "bg=colour17"], self.calls)

    def test_split(self):
        """split-window uses -h/-v, a percent size and prints the pane id."""
        self.outputs = ["%5\n"]
        pane = self.term.split_pane("sess", "T", None, ["echo", "x;y"], "vertical", 0.25, target="%0")
        self.assertEqual(pane, "%5")
        self.assertEqual(self.calls[0], ["tmux", "split-window", "-t", "%0", "-v", "-l", "25%",
                                         "-P", "-F", "#{pane_id}", "echo 'x;y'"])
        self.assertEqual(self.calls[1], ["tmux", "select-pane", "-t", "%5", "-T", "T"])
        self.assertEqual(len(self.calls), 2)

    def test_split_default_target_and_horizontal(self):
        """Without a target the session is addressed; horizontal is -h."""
        self.outputs = ["%1\n"]
        self.term.split_pane("sess", "T", None, ["x"], "horizontal", 0.5)
        self.assertEqual(self.calls[0][:5], ["tmux", "split-window", "-t", "sess", "-h"])

    def test_close_and_title(self):
        """close_pane kills the pane, set_title selects with -T."""
        self.term.close_pane("%3")
        self.term.set_title("%3", "New")
        self.assertEqual(self.calls, [["tmux", "kill-pane", "-t", "%3"],
                                      ["tmux", "select-pane", "-t", "%3", "-T", "New"]])

    def test_list_panes(self):
        """list-panes output is parsed into PaneInfo."""
        self.outputs = ["%0\tAgent-Orchestrator | p | CHIEF\t100\t0\n%1\tt\t101\t1\n"]
        panes = self.term.list_panes("sess")
        self.assertEqual(self.calls[0][:6], ["tmux", "list-panes", "-s", "-t", "sess", "-F"])
        self.assertEqual([(p.id, p.title, p.pid, p.alive) for p in panes],
                         [("%0", "Agent-Orchestrator | p | CHIEF", 100, True), ("%1", "t", 101, False)])

    def test_failure(self):
        """A failing tmux command raises TerminalError."""
        with mock.patch.object(tmux.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 1, "", "no server")):
            with self.assertRaises(TerminalError):
                self.term.close_pane("%9")


class HeadlessTest(unittest.TestCase):
    """The headless backend runs real processes."""

    def setUp(self):
        """Create a backend on a temp folder."""
        self.tmp = tempfile.TemporaryDirectory()
        self.term = headless.Headless(self.tmp.name)
        self.ids = []

    def tearDown(self):
        """End every process this test started."""
        for pane_id in self.ids:
            try:
                self.term.close_pane(pane_id)
            except TerminalError:
                pass
        self.tmp.cleanup()

    def test_output_alive_close(self):
        """A pane writes to runs/<agent>/output.log, is alive, listed, and close_pane ends it."""
        quick = self.term.open_window("W", "quick", [sys.executable, "-c", "print('hello headless')"],
                                      name="quick")
        long = self.term.split_pane("W", "long", None,
                                    [sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"],
                                    "horizontal", 0.5, name="long")
        self.ids += [quick, long]
        log_long = Path(self.tmp.name) / "long" / "output.log"
        self.assertTrue(wait_for(lambda: log_long.exists() and "up" in log_long.read_text()))
        self.assertTrue(wait_for(lambda: "hello headless" in (Path(self.tmp.name) / "quick" / "output.log").read_text()))
        panes = {p.id: p for p in self.term.list_panes("W")}
        self.assertEqual(set(panes), {"W.0", "W.1"})
        self.assertEqual(panes[long].title, "long")
        self.assertTrue(panes[long].alive)
        self.assertTrue(wait_for(lambda: not {p.id: p for p in self.term.list_panes("W")}[quick].alive))
        pid = panes[long].pid
        self.assertTrue(_proc.pid_alive(pid))
        self.term.close_pane(long)
        self.assertTrue(wait_for(lambda: not _proc.pid_alive(pid)))
        self.assertFalse({p.id: p for p in self.term.list_panes("W")}[long].alive)

    def test_other_window_not_listed(self):
        """Panes of other windows are not listed."""
        self.ids.append(self.term.open_window("A", "t", [sys.executable, "-c", "pass"], name="a0"))
        self.assertEqual(self.term.list_panes("B"), [])

    def test_set_title_and_unknown(self):
        """Titles can change; an unknown pane id is an error."""
        pane = self.term.open_window("W", "old", [sys.executable, "-c", "pass"], name="x")
        self.ids.append(pane)
        self.term.set_title(pane, "new")
        self.assertEqual(self.term.list_panes("W")[0].title, "new")
        with self.assertRaises(TerminalError):
            self.term.close_pane("nope.9")


if __name__ == "__main__":
    unittest.main()
