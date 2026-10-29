"""Tests of the pane layout and the pane command lock (the v2 form of the v1 pane tests B and E).

B: only the chief and dashboard panes are open and the first agent starts: its pane splits the dashboard pane, the next agent splits the first one.
E: pane commands come from the loop round (launch_agent) and from `bzz start`; both take pane.lock, so two processes never send them at the same
   time, and of several loops started at the same moment exactly one takes loop.lock.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from bzztest import RoundCase
from haivequeen import loop
from haivequeen.terminal import pane_title
from haivequeen.terminal.headless import Headless

ONE = [{"id": "P1", "title": "first"}]
SLEEP = [sys.executable, "-c", "import time;time.sleep(60)"]
ROOT = Path(__file__).resolve().parent.parent


class RecordingHeadless(Headless):
    """Headless backend that remembers the target and direction of every split."""

    def __init__(self, runs_dir):
        """Start with an empty record."""
        super().__init__(runs_dir)
        self.splits = []

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5, target=None, name=None):
        """Record the split, then do it."""
        self.splits.append((target, direction))
        return super().split_pane(window, title, colour, command, direction, size, target, name)


class PaneLayoutTest(RoundCase):
    """Test B: chief and dashboard panes are open, no agent runs, an agent starts."""

    def setUp(self):
        """A dummy runner that sets the pid of its agent row, a recording headless terminal with the chief and the dashboard pane."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        self.plan("IN_PROGRESS", ONE)
        self.seed()
        dummy = self.base / "dummy_runner.py"
        dummy.write_text(
            "import os, sys, time\nsys.path.insert(0, %r)\nfrom haivequeen import config, db, runtime\n"
            "conn = db.connect(config.db_path())\nruntime.upsert_agent(conn, sys.argv[1], sys.argv[2], {'pid': os.getpid(), 'status': 'running'})\ntime.sleep(30)\n"
            % str(loop.TOOLS_DIR), encoding="utf-8")
        self.orig = loop.runner_argv
        loop.runner_argv = lambda env, spec: [sys.executable, str(dummy), env.name, spec["name"]]
        self.term = RecordingHeadless(self.base / "panes")
        self.chief = self.term.open_window("HaiveQueen", pane_title("all projects", "QUEEN", "Queen", "m", True), SLEEP, name="chief")
        self.dash = self.term.split_pane("HaiveQueen", pane_title("all projects", "Overview", "Dashboard", "-", True), "white", SLEEP,
                                         "vertical", 0.8, self.chief, "dashboard")
        self.term.splits.clear()

    def tearDown(self):
        """End every process of the panes."""
        loop.runner_argv = self.orig
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive:
                self.term.close_pane(p.id)
        super().tearDown()

    def test_first_agent_splits_dashboard_next_splits_first(self):
        """The first agent pane is a split of the dashboard pane (beside it); the second agent splits the first agent pane; all panes stay open."""
        self.round(terminal=self.term, poll=0.2, tries=100)
        first = self.ag("DUMMY-P1-work")
        self.assertEqual(self.term.splits, [(self.dash, "horizontal")])
        self.assertEqual(first["status"], "running")
        spec = {"name": "DUMMY-P2-work", "person": "Zed", "model_long": "Claude Sonnet 5.5 medium"}
        loop.launch_agent(self.envs()[0], self.term, "HaiveQueen", spec, settle=0, poll=0.2, tries=100)
        second = self.ag("DUMMY-P2-work")
        self.assertEqual(self.term.splits[-1], (first["pane_id"], "horizontal"))
        ids = [p.id for p in self.term.list_panes("HaiveQueen") if p.alive]
        self.assertEqual(sorted(ids), sorted([self.chief, self.dash, first["pane_id"], second["pane_id"]]))
        self.assertEqual(len(set(ids)), 4)

    def test_without_dashboard_the_chief_pane_is_split(self):
        """With the dashboard pane gone the first agent pane splits the chief pane."""
        self.term.close_pane(self.dash)
        loop.launch_agent(self.envs()[0], self.term, "HaiveQueen",
                          {"name": "DUMMY-P1-work", "person": "Max", "model_long": "Claude Sonnet 5.5 medium"}, settle=0, poll=0.2, tries=100)
        self.assertEqual(self.term.splits, [(self.chief, "horizontal")])


HOLD = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom haivequeen import loop\n"
        "with loop.pane_lock():\n    open(sys.argv[2], 'a').write('in %s\\n' % sys.argv[3])\n    time.sleep(0.6)\n    open(sys.argv[2], 'a').write('out %s\\n' % sys.argv[3])\n")
TAKE = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom haivequeen import loop\n"
        "res = loop._take_pid_lock(loop.lock_file(), own_ok=True)\nprint(res, flush=True)\ntime.sleep(2.5)\n")


class LeftoverPanesTest(RoundCase):
    """A new start closes the Overview pane a previous start left behind, so the window keeps one tab."""

    def test_leftover_overview_is_closed_agent_pane_stays(self):
        """The old Overview pane ends, a running agent pane of the same window and the (ended) old Queen pane are not touched or counted."""
        term = Headless(self.base / "panes")
        dash = term.open_window("HaiveQueen", pane_title("all projects", "Overview", "Dashboard", "-", True), SLEEP, name="dashboard")
        agent = term.split_pane("HaiveQueen", pane_title("ODP", "Agent PIA", "INGAME-QS-1", "m", True), "white", SLEEP,
                                "vertical", 0.5, dash, "ODP__INGAME-QS-1")
        try:
            self.assertEqual(loop.close_leftover_panes(term, "HaiveQueen"), [dash])
            alive = dict((p.id, p.alive) for p in term.list_panes("HaiveQueen"))
            self.assertFalse(alive[dash])
            self.assertTrue(alive[agent])
        finally:
            for p in term.list_panes("HaiveQueen"):
                if p.alive:
                    term.close_pane(p.id)


class StopAndStartTest(RoundCase):
    """`bzz stop` closes the Queen and Overview panes and the panes of ended agents; `bzz start` resets pause and drain."""

    def test_stop_closes_session_panes_but_not_running_agents(self):
        """After the loop ended: Overview, Queen and the pane of an ended agent are closed, the pane of a running agent stays."""
        term = Headless(self.base / "panes")
        queen = term.open_window("HaiveQueen", pane_title("all projects", "QUEEN", "Queen", "m", True), SLEEP, name="chief")
        over = term.split_pane("HaiveQueen", pane_title("all projects", "Overview", "Dashboard", "-", True), "white", SLEEP, "vertical", 0.8, queen, "dashboard")
        done = term.split_pane("HaiveQueen", pane_title("testproj", "Agent PIA", "A-done", "m", True), "white", SLEEP, "vertical", 0.5, queen, "testproj__A-done")
        live = term.split_pane("HaiveQueen", pane_title("testproj", "Agent PIA", "A-live", "m", True), "white", SLEEP, "vertical", 0.5, queen, "testproj__A-live")
        self.agent("A-done", status="done", started=20, ended=10)
        self.agent("A-live", status="running", started=20)
        try:
            closed = loop.close_session_panes(self.conn, term, "HaiveQueen")
            alive = dict((p.id, p.alive) for p in term.list_panes("HaiveQueen"))
            self.assertEqual(sorted(closed), sorted([queen, over, done]))
            self.assertEqual(closed[-1], queen, "the Queen pane goes last")
            self.assertTrue(alive[live])
            self.assertFalse(alive[queen] or alive[over] or alive[done])
        finally:
            for p in term.list_panes("HaiveQueen"):
                if p.alive:
                    term.close_pane(p.id)

    def test_start_resets_a_drain(self):
        """A drain from the run before is lifted by `bzz start` (only the reset before the loop starts is under test)."""
        from haivequeen import runstate
        runstate.drain(self.conn, "test")
        self.assertNotEqual(runstate.get_state(self.conn)["state"], "RUNNING")
        args = argparse.Namespace(foreground=True, terminal="headless", rounds=0, round_minutes=0)
        orig = loop.run_loop
        loop.run_loop = lambda *a, **k: 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                loop.cmd_start(args, self.conn)
        finally:
            loop.run_loop = orig
        self.assertEqual(runstate.get_state(self.conn)["state"], "RUNNING")


class PaneLockTest(RoundCase):
    """Test E: pane commands and the loop lock across processes."""

    def run_procs(self, code, count, extra=()):
        """Start `count` Python processes at once with the given code; returns their outputs."""
        procs = [subprocess.Popen([sys.executable, "-c", code, str(loop.TOOLS_DIR)] + [str(a) for a in extra] + [str(i)],
                                  stdout=subprocess.PIPE, text=True, env=os.environ.copy()) for i in range(count)]
        return [p.communicate(timeout=60)[0].strip() for p in procs]

    def test_two_processes_send_pane_commands_one_after_the_other(self):
        """Two processes that both hold pane.lock never overlap: in, out, in, out."""
        log = self.base / "pane.log"
        self.run_procs(HOLD, 2, [log])
        words = [line.split()[0] for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(words, ["in", "out", "in", "out"])
        self.assertFalse(loop.pane_lock_file().exists(), "the lock is released")

    def test_only_one_of_several_loops_takes_the_loop_lock(self):
        """Six processes try to take loop.lock at the same moment: exactly one gets it, the others see its pid."""
        results = self.run_procs(TAKE, 6)
        self.assertEqual(results.count("0"), 1, results)
        holders = {r for r in results if r != "0"}
        self.assertEqual(len(holders), 1)
        self.assertTrue(int(holders.pop()) > 0)

    def test_lock_of_a_dead_process_is_taken_over(self):
        """A lock file that names a process that is gone does not block."""
        loop.pane_lock_file().parent.mkdir(parents=True, exist_ok=True)
        loop.pane_lock_file().write_text("2000000000", encoding="ascii")
        with loop.pane_lock(timeout=5):
            self.assertEqual(loop.pane_lock_file().read_text(encoding="ascii"), str(os.getpid()))
        self.assertFalse(loop.pane_lock_file().exists())

    def test_pane_lock_times_out_while_another_process_holds_it(self):
        """A living holder keeps the lock: the second caller raises TimeoutError instead of sending its pane command."""
        holder = subprocess.Popen(SLEEP)
        try:
            loop.pane_lock_file().parent.mkdir(parents=True, exist_ok=True)
            loop.pane_lock_file().write_text(str(holder.pid), encoding="ascii")
            with self.assertRaises(TimeoutError):
                with loop.pane_lock(timeout=0.5, poll=0.05):
                    self.fail("must not get the lock")
        finally:
            holder.kill()
            holder.wait()

    def start(self):
        """Call `bzz start` (window mode, headless backend) in this process and return its JSON output."""
        args = argparse.Namespace(foreground=False, terminal="headless", rounds=0, round_minutes=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.cmd_start(args, self.conn)
        return json.loads(buf.getvalue())

    def test_window_start_passes_rounds_to_the_loop(self):
        """`bzz start --rounds 1 --round-minutes 2.5` hands both to the loop that runs in the Queen pane (before, the loop ran until stopped)."""
        args = argparse.Namespace(foreground=False, terminal="headless", rounds=1, round_minutes=2.5)
        cmd = loop.start_boot_command(args)
        self.assertEqual(cmd[-6:], ["--terminal", "headless", "--rounds", "1", "--round-minutes", "2.5"])
        plain = loop.start_boot_command(argparse.Namespace(foreground=False, terminal=None, rounds=0, round_minutes=0))
        self.assertNotIn("--rounds", plain)
        self.assertNotIn("--round-minutes", plain)

    def test_second_start_does_not_open_a_second_window(self):
        """With a loop running, or a window start pending, `bzz start` opens no pane."""
        holder = subprocess.Popen(SLEEP)
        try:
            loop.lock_file().parent.mkdir(parents=True, exist_ok=True)
            loop.lock_file().write_text(str(holder.pid), encoding="ascii")
            self.assertTrue(self.start()["running"])
        finally:
            holder.kill()
            holder.wait()
        loop.lock_file().unlink()
        loop.start_pending_file().write_text("1", encoding="ascii")
        self.assertTrue(self.start()["starting"])
        self.assertFalse((loop.osplat.runs_dir() / "_panes").exists(), "no pane was opened")


class RealWindowsTerminalCloseTest(RoundCase):
    """Real Windows Terminal in the test runtime (opt in with BZZ_REAL_WT=1: the test opens, resizes, moves and closes a real window of the Windows
    Terminal process it runs in, and a crash of that process ends every window and shell in it).
    Covers: restart, new agents in the same tab, resize and move with live headers and dashboard, several panes closed one after another."""

    def require_real_wt(self):
        """Skip unless Windows, wt.exe and the opt-in variable are there."""
        import shutil
        if sys.platform != "win32" or not shutil.which("wt.exe") or not os.environ.get("BZZ_REAL_WT"):
            self.skipTest("opt in with BZZ_REAL_WT=1 (opens, resizes and closes a real Windows Terminal window)")

    def window_handles(self):
        """Native window handles of all Windows Terminal windows, comma separated."""
        return self.powershell(
            "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes; $r=[System.Windows.Automation.AutomationElement]::RootElement; "
            "($r.FindAll('Children',(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty,'CASCADIA_HOSTING_WINDOW_CLASS'))) "
            "| ForEach-Object { $_.Current.NativeWindowHandle }) -join ','")

    def drag_new_window(self, before, rounds=12):
        """Resize and move the window that is not in `before` the way a drag does (every 0.4 s a new size and place, also across the screen); return the number of steps."""
        out = self.powershell(
            "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes; $before=@('%s'.Split(',')); $r=[System.Windows.Automation.AutomationElement]::RootElement; "
            "$ws=$r.FindAll('Children',(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty,'CASCADIA_HOSTING_WINDOW_CLASS'))); "
            "$w=$null; foreach($x in $ws){ if($before -notcontains [string]$x.Current.NativeWindowHandle){ $w=$x } }; if(-not $w){ 0; exit }; "
            "$p=$w.GetCurrentPattern([System.Windows.Automation.TransformPattern]::Pattern); $n=0; "
            "for($i=0;$i -lt %d;$i++){ $p.Resize(700+($i*53)%%500, 420+($i*37)%%300); $p.Move(40+($i*97)%%600, 30+($i*61)%%300); Start-Sleep -Milliseconds 400; $n++ }; $n" % (before, rounds))
        return int(out or "0")

    def head_command(self, name):
        """Command of a pane that runs the real header redraw loop of one agent against the test database."""
        code = ("import os,sys;os.environ['BZZ_DATA_DIR']=sys.argv[1];sys.path.insert(0,sys.argv[2]);"
                "from haivequeen import header;header.run('testproj',sys.argv[3],0,sys.argv[4])")
        return [sys.executable, "-c", code, os.environ["BZZ_DATA_DIR"], str(ROOT), name, str(self.base / "runs")]

    def dash_command(self):
        """Command of a pane that runs the real dashboard against the test database."""
        code = "import os,sys;os.environ['BZZ_DATA_DIR']=sys.argv[1];sys.path.insert(0,sys.argv[2]);from haivequeen.dashboard import main;sys.exit(main([]))"
        return [sys.executable, "-c", code, os.environ["BZZ_DATA_DIR"], str(ROOT)]

    def powershell(self, script):
        """Run a PowerShell snippet and return its trimmed output."""
        return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=90).stdout.strip()

    def crashes_since(self, since):
        """Application Error / Hang events of WindowsTerminal.exe after `since` (Get-WinEvent); return the count."""
        return int(self.powershell("@(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1002; StartTime=[datetime]'%s'} -ErrorAction SilentlyContinue "
                                   "| Where-Object { $_.Message -match 'WindowsTerminal' }).Count" % since.strftime("%Y-%m-%d %H:%M:%S")) or "0")

    def tab_count(self):
        """Number of tabs in all Windows Terminal windows (UI Automation)."""
        return int(self.powershell(
            "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes; $n=0; $r=[System.Windows.Automation.AutomationElement]::RootElement; "
            "$ws=$r.FindAll('Children',(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty,'CASCADIA_HOSTING_WINDOW_CLASS'))); "
            "foreach($w in $ws){$n+=$w.FindAll('Descendants',(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::TabItem))).Count}; $n") or "0")

    def test_resize_and_move_with_live_headers_and_dashboard_do_not_crash_the_terminal(self):
        """Queen, dashboard and three agent panes with the real header loop stay alive while the window is resized and moved twelve times, then all panes close one after another."""
        import datetime
        from haivequeen.terminal import wt
        self.require_real_wt()
        window = "HQTEST%d" % os.getpid()
        term = wt.WindowsTerminal(str(self.base / "panes"), start_timeout=30, settle=2.0)
        before = self.window_handles()
        started = datetime.datetime.now() - datetime.timedelta(seconds=2)
        qtitle = pane_title("all projects", "QUEEN", "Queen", "m", True)
        names = ["A-head-%d" % i for i in range(3)]
        for name in names:
            self.agent(name, status="running", started=20)
        try:
            with loop.pane_lock():
                panes = loop.open_session_panes(term, window, qtitle, SLEEP, self.dash_command())
            spots = [(panes[1], "horizontal"), (None, "vertical"), (panes[1], "vertical")]   # beside the dashboard, below the first agent, below the dashboard: no pane gets too small to split
            for name, (target, direction) in zip(names, spots):
                target = target or panes[-1]
                panes.append(term.split_pane(window, pane_title("testproj", "Agent PIA", name, "m", True), "white", self.head_command(name), direction, 0.5,
                                             target, "testproj__" + name))
            steps = self.drag_new_window(before)
            self.assertEqual(steps, 12, "the test window was not found or not resized")
            for p in term.list_panes(window):
                self.assertTrue(p.alive, "pane %s ended during the drag" % p.id)
            for name in names:
                self.finish(name)
            with loop.pane_lock():
                closed = loop.close_session_panes(self.conn, term, window)
            self.assertEqual(sorted(closed), sorted(panes))
            self.assertEqual(self.crashes_since(started), 0, "Windows Terminal crashed or hung during resize, move or close")
        finally:
            for p in term.list_panes(window):
                if p.alive:
                    term.close_pane(p.id)

    def test_restart_keeps_one_tab_and_closing_panes_does_not_crash_the_terminal(self):
        """A restart while an agent pane runs adds no tab, the next agent splits in the same tab, and `bzz stop` closes all panes without a crash or hang."""
        import datetime
        from haivequeen.terminal import wt
        self.require_real_wt()
        window = "HQTEST%d" % os.getpid()
        term = wt.WindowsTerminal(str(self.base / "panes"), start_timeout=30, settle=2.0)
        tabs_before = self.tab_count()
        started = datetime.datetime.now() - datetime.timedelta(seconds=2)
        qtitle = pane_title("all projects", "QUEEN", "Queen", "m", True)
        try:
            with loop.pane_lock():
                first = loop.open_session_panes(term, window, qtitle, SLEEP, SLEEP)
            agents = []
            self.agent("A-run", status="running", started=20)
            agents.append(term.split_pane(window, pane_title("testproj", "Agent PIA", "A-run", "m", True), "white", SLEEP, "horizontal", 0.5, first[1], "testproj__A-run"))
            self.assertEqual(self.tab_count(), tabs_before + 1)
            with loop.pane_lock():   # restart: the agent pane is still open
                second = loop.open_session_panes(term, window, qtitle, SLEEP, SLEEP)
            self.assertEqual(self.tab_count(), tabs_before + 1, "the restart opened a second tab")
            for i in range(2):
                name = "A-new-%d" % i
                self.agent(name, status="done", started=20, ended=10)
                live = [p for p in term.list_panes(window) if p.alive]
                agents.append(term.split_pane(window, pane_title("testproj", "Agent PIA", name, "m", True), "white", SLEEP, "vertical", 0.5,
                                              loop.first_split_target(live), "testproj__" + name))
            self.assertEqual(self.tab_count(), tabs_before + 1, "an agent pane opened a second tab")
            self.finish("A-run")
            with loop.pane_lock():
                closed = loop.close_session_panes(self.conn, term, window)
            self.assertEqual(sorted(closed), sorted(second + agents))
            for p in term.list_panes(window):
                self.assertTrue(wait_dead(p.pid), "pane %s still runs" % p.id)
            self.assertEqual(self.crashes_since(started), 0, "Windows Terminal crashed or hung while the panes closed")
        finally:
            for p in term.list_panes(window):
                if p.alive:
                    term.close_pane(p.id)


def wait_dead(pid, seconds=30):
    """Poll until the process is gone or the time is up; return True when it is gone."""
    import time
    from haivequeen.terminal import _proc
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _proc.pid_alive(pid):
            return True
        time.sleep(0.2)
    return not _proc.pid_alive(pid)


if __name__ == "__main__":
    unittest.main()
