"""Tests of the terminal backends: factory, quoting, titles, wt and tmux command lines, headless for real."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from haivequeen.terminal import TerminalError, get_terminal, pane_title  # noqa: E402
from haivequeen.terminal import headless, tmux, wt  # noqa: E402
from haivequeen.terminal import _proc  # noqa: E402


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
            os.environ.pop("BZZ_RUNS_DIR", None)
            with self.assertRaises(TerminalError):
                get_terminal("headless")


class TitleTest(unittest.TestCase):
    """Pane titles."""

    def test_format(self):
        """The title has the v1 format with the project name."""
        self.assertEqual(pane_title("demo", "Agent ANNA", "work-1", "claude opus 5.5 medium"),
                         "HaiveQueen | demo | Agent ANNA | job: work-1 | model: claude opus 5.5 medium")

    def test_test_prefix(self):
        """A test runtime says TEST."""
        self.assertTrue(pane_title("p", "QUEEN", "Queen", "m", test=True)
                        .startswith("HaiveQueen TEST | p | QUEEN | job: Queen"))


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
        self.term = wt.WindowsTerminal(self.tmp.name, start_timeout=0, profile="PowerShell", settle=0)

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def test_open_window(self):
        """The window is opened as a named new-tab with the fixed visible title HaiveQueen (never the pane's own name) and tab colour."""
        subs = self.term.open_window_args("W1", "T 1", ["py", "-c", "x"], colour="#112233")
        self.assertEqual(subs, [["-w", "W1", "new-tab", "-p", "PowerShell", "--title", "HaiveQueen",
                                 "--suppressApplicationTitle", "--tabColor", "#112233", "py", "-c", "x"]])

    def test_split_with_target(self):
        """A target pane is focused first, then split; horizontal means -V (side by side)."""
        subs = self.term.split_pane_args("W1", "T", ["py"], "horizontal", 0.7, target="2")
        self.assertEqual(subs[0], ["-w", "W1", "focus-pane", "-t", "2"])
        self.assertEqual(subs[1][:6], ["split-pane", "-p", "PowerShell", "-V", "-s", "0.7"])
        self.assertIn("--suppressApplicationTitle", subs[1])

    def test_visible_title_is_always_haivequeen(self):
        """Every wt pane and tab shows the constant title HaiveQueen, so the window title never names a project, agent or Queen."""
        for subs in (self.term.open_window_args("W1", "HaiveQueen | p | Hive | job: X | model: -", ["py"]),
                     self.term.split_pane_args("W1", "HaiveQueen | p | Agent ANNA | job: x | model: m", ["py"], target="1")):
            sub = subs[-1]
            self.assertEqual(sub[sub.index("--title") + 1], "HaiveQueen")
            self.assertNotIn("Hive", " ".join(sub[:sub.index("py")]).replace("--suppressApplicationTitle", ""))

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
        self.assertNotIn("; one", line.replace("\;", ""))   # the visible title is the constant one, the title with the semicolon is not on the line
        self.assertIn("--title HaiveQueen ", line)
        self.assertIn("print(1)\\;print(2)", line)
        self.assertIn("_panerun.py", line)
        self.assertEqual([r["title"] for r in self.term.registry.records()], ["Title; one"])   # the lookup title stays in the register
        with mock.patch.object(wt.subprocess, "Popen"):
            second = self.term.split_pane("W9", "T2", None, ["py"], "vertical", 0.4, target="W9.0", name="p1")
        self.assertEqual(second, "W9.1")
        infos = self.term.list_panes("W9")
        self.assertEqual([i.id for i in infos], ["W9.0", "W9.1"])
        self.assertFalse(any(i.alive for i in infos))

    def test_every_wt_call_is_traced(self):
        """With a trace sink every wt call writes one line: the whole command line, target number, direction, size, exit code, stdout, stderr and whether pane.pid appeared."""
        seen = []
        self.term.trace = seen.append

        def popen(line, **kw):
            """Stand in for wt.exe: write to the redirected output files and report exit code 1."""
            kw["stdout"].write(b"some out")
            kw["stderr"].write(b"no such pane")
            return mock.Mock(poll=mock.Mock(return_value=1))
        with mock.patch.object(wt.subprocess, "Popen", side_effect=popen):
            self.term.open_window("W7", "T", ["py"], name="t0")
            self.term.split_pane("W7", "T", None, ["py"], "vertical", 0.4, target="W7.0", name="t1")
        self.assertEqual(len(seen), 2)
        self.assertIn("open-window", seen[0])
        split = seen[1]
        for part in ("split target=0 direction=vertical size=0.4", "focus-pane -t 0", "split-pane", "exit=1", "some out", "no such pane", "pane.pid=no"):
            self.assertIn(part, split)

    def test_a_pane_that_never_starts_gives_its_number_back(self):
        """wt numbers only the panes it really made: a split whose pane never started must not use up a number, else every later focus-pane target is wrong."""
        term = wt.WindowsTerminal(self.tmp.name, start_timeout=0.3, settle=0)
        term.registry.save("first", {"id": "W8.0", "window": "W8", "index": 0, "title": "T", "pid": 0, "pidfile": ""})
        for _ in range(3):   # three rounds, each one split fails
            with mock.patch.object(wt.subprocess, "Popen"), self.assertRaises(TerminalError):
                term.split_pane("W8", "T", None, ["py"], "vertical", 0.5, target="W8.0", name="hive")
        self.assertEqual(term.registry.next_index("W8"), 1)
        self.assertEqual([r["id"] for r in term.registry.records()], ["W8.0"])

    def test_split_that_gets_no_pane_is_tried_once_more_on_the_focused_pane(self):
        """When wt makes no pane for a split with focus-pane, the same split is tried once without focus-pane (on the pane wt has focused); both calls are traced."""
        import re
        term = wt.WindowsTerminal(self.tmp.name, start_timeout=0.3, settle=0)
        seen = []
        term.trace = seen.append
        term.registry.save("first", {"id": "W9.0", "window": "W9", "index": 0, "title": "T", "pid": 0, "pidfile": ""})

        def popen(line, **kw):
            """Stand in for wt.exe: a split with focus-pane makes no pane, one without it does (the launcher writes its pid file)."""
            if "focus-pane" not in line:
                Path(re.search(r"_panerun\.py (\S+)", line).group(1)).write_text("111 222", encoding="ascii")
            return mock.Mock(poll=mock.Mock(return_value=0))
        with mock.patch.object(wt.subprocess, "Popen", side_effect=popen):
            pane = term.split_pane("W9", "T", None, ["py"], "vertical", 0.5, target="W9.0", name="hive")
        self.assertEqual(pane, "W9.1")
        self.assertEqual(len(seen), 2)
        self.assertIn("focus-pane -t 0", seen[0])
        self.assertIn("pane.pid=no", seen[0])
        self.assertIn("target=None", seen[1])
        self.assertNotIn("focus-pane", seen[1])
        self.assertIn("pane.pid=yes", seen[1])

    def test_focus_pane_target_is_the_number_wt_counts_in_tree_order(self):
        """wt counts panes through the split tree, not in creation order: the pane split off pane 0 last sits at 1, so the pane made third is wt's number 1."""
        import re
        term = wt.WindowsTerminal(self.tmp.name, start_timeout=0.3, settle=0)
        lines = []

        def popen(line, **kw):
            """Stand in for wt.exe: record the line and let the launcher write a pid file of a living process."""
            lines.append(line)
            Path(re.search(r"_panerun\.py (\S+)", line).group(1)).write_text("%d %d" % (os.getpid(), os.getpid()), encoding="ascii")
            return mock.Mock(poll=mock.Mock(return_value=0))
        with mock.patch.object(wt.subprocess, "Popen", side_effect=popen):
            chief = term.open_window("W10", "T", ["py"], None, "chief")
            term.split_pane("W10", "T", None, ["py"], "vertical", 0.8, target=chief, name="dash")
            head = term.split_pane("W10", "T", None, ["py"], "horizontal", 0.5, target=chief, name="head")
            term.split_pane("W10", "T", None, ["py"], "vertical", 0.5, target=head, name="hive")
        self.assertEqual(head, "W10.2")
        self.assertIn("focus-pane -t 0 ; split-pane", lines[2])
        self.assertIn("focus-pane -t 1 ; split-pane", lines[3])

    def test_split_that_leaves_too_little_room_is_tried_with_a_smaller_share(self):
        """wt silently makes no pane when the old pane would keep too few lines (0.95 of a tall head pane); the split is tried again with a smaller share until a pane comes."""
        import re
        term = wt.WindowsTerminal(self.tmp.name, start_timeout=0.2, settle=0)
        sizes = []

        def popen(line, **kw):
            """Stand in for wt.exe: shares above 0.9 make no pane, smaller ones do (the launcher writes its pid file)."""
            share = float(re.search(r"-s (\S+)", line).group(1))
            sizes.append(share)
            if share <= 0.9:
                Path(re.search(r"_panerun\.py (\S+)", line).group(1)).write_text("111 222", encoding="ascii")
            return mock.Mock(poll=mock.Mock(return_value=0))
        term.registry.save("first", {"id": "W11.0", "window": "W11", "index": 0, "title": "T", "pid": 0, "pidfile": ""})
        with mock.patch.object(wt.subprocess, "Popen", side_effect=popen):
            pane = term.split_pane("W11", "T", None, ["py"], "vertical", 0.95, target="W11.0", name="hive")
        self.assertEqual(pane, "W11.1")
        self.assertEqual(sizes[-1], 0.9)

    def test_wt_is_started_without_the_session_of_the_calling_pane(self):
        """The Beekeeper runs inside a wt pane: wt.exe must not inherit that pane's WT_* variables or its console, else it may treat the call as coming from the pane."""
        seen = {}

        def popen(line, **kw):
            """Record how wt.exe is started."""
            seen.update(kw)
            return mock.Mock(poll=mock.Mock(return_value=0))
        with mock.patch.dict("os.environ", {"WT_SESSION": "abc", "WT_PROFILE_ID": "{x}", "KEEP_ME": "1"}), \
                mock.patch.object(wt.subprocess, "Popen", side_effect=popen):
            self.term.open_window("W3", "T", ["py"], name="e0")
        self.assertEqual([k for k in seen["env"] if k.startswith("WT_")], [])
        self.assertEqual(seen["env"].get("KEEP_ME"), "1")
        if sys.platform == "win32":
            self.assertEqual(seen["creationflags"] & 0x208, 0x208)   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    def test_reopened_window_numbers_from_zero(self):
        """A window that is opened again starts at pane 0; the records of the old window are retired, not reused."""
        with mock.patch.object(wt.subprocess, "Popen"):
            self.term.open_window("W5", "T", ["py"], name="a0")
            self.term.split_pane("W5", "T", None, ["py"], "horizontal", 0.5, target="W5.0", name="a1")
            reopened = self.term.open_window("W5", "T", ["py"], name="b0")
            after = self.term.split_pane("W5", "T", None, ["py"], "horizontal", 0.5, target=reopened, name="b1")
        self.assertEqual((reopened, after), ("W5.0", "W5.1"))
        self.assertEqual([i.id for i in self.term.list_panes("W5")], ["W5.0", "W5.1"])
        self.assertEqual(self.term.registry.find("W5.0")["name"], "b0")

    def test_settle_before_every_command(self):
        """The pause before a wt command (the first version's 2 s) runs before the process starts."""
        term = wt.WindowsTerminal(self.tmp.name, start_timeout=0, settle=2.0)
        order = []
        with mock.patch.object(wt.time, "sleep", side_effect=lambda s: order.append(("sleep", s))), \
                mock.patch.object(wt.subprocess, "Popen", side_effect=lambda *a, **k: order.append(("popen",))):
            term.open_window("W6", "T", ["py"], name="c0")
        self.assertLess(order.index(("sleep", 2.0)), order.index(("popen",)))

    def test_exe_override(self):
        """BZZ_WT_EXE replaces wt.exe (tests and stubs)."""
        with mock.patch.dict("os.environ", {"BZZ_WT_EXE": "stub.exe"}):
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

    def test_resize_pane_in_percent(self):
        """resize_pane sets the width (x) or height (y) of a pane as a percentage of the window; a wrong axis is refused."""
        self.term.resize_pane("%3", "x", 0.75)
        self.term.resize_pane("%3", "y", 0.2)
        self.assertEqual(self.calls, [["tmux", "resize-pane", "-t", "%3", "-x", "75%"], ["tmux", "resize-pane", "-t", "%3", "-y", "20%"]])
        with self.assertRaises(TerminalError):
            self.term.resize_pane("%3", "z", 0.5)

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
        self.outputs = ["%0\tHaiveQueen | p | QUEEN\t100\t0\n%1\tt\t101\t1\n"]
        panes = self.term.list_panes("sess")
        self.assertEqual(self.calls[0][:6], ["tmux", "list-panes", "-s", "-t", "sess", "-F"])
        self.assertEqual([(p.id, p.title, p.pid, p.alive) for p in panes],
                         [("%0", "HaiveQueen | p | QUEEN", 100, True), ("%1", "t", 101, False)])

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
