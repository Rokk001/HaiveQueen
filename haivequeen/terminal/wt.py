"""Windows Terminal backend (`wt.exe`), same commands and titles as the first version of the tool.

A named window (`-w <name>`) is addressed by every call. Panes are numbered in creation order, as
Windows Terminal does. Each command runs behind a small launcher (_panerun.py) that writes a pid
file into runs/<pane>/; closing a pane ends the command's process tree, the launcher then exits 0
and the terminal closes the pane itself. The visible title of a pane is fixed at creation
(`--suppressApplicationTitle`); Windows Terminal has no command to change it later.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import _proc
from .base import (PaneInfo, PaneRegistry, Terminal, TerminalError, check_direction, check_size,
                   safe_name)

LAUNCHER = str(Path(__file__).with_name("_panerun.py"))


def wt_quote(arg):
    """Quote one argument for the wt.exe command line: Windows quoting, and ';' written as '\\;'.

    wt.exe separates its own sub-commands with ';', so a literal ';' in an argument must be escaped.
    """
    return subprocess.list2cmdline([str(arg)]).replace(";", "\\;")


def wt_commandline(subcommands):
    """Join sub-commands (lists of arguments) into one wt.exe argument string with ' ; ' separators."""
    return " ; ".join(" ".join(wt_quote(a) for a in sub) for sub in subcommands)


def pane_number(pane_id):
    """Return the pane number inside the window from an id like '<window>.<n>'."""
    return pane_id.rsplit(".", 1)[1]


class WindowsTerminal(Terminal):
    """Windows Terminal through wt.exe (path override: environment variable BZZ_WT_EXE)."""

    name = "wt"

    def __init__(self, runs_dir, start_timeout=20, profile=None, settle=2.0):
        """Use `runs_dir` for pane records; wait up to `start_timeout` s for a started pane's pid file.

        `settle` is the pause in seconds before every wt command (as in the first version): the terminal finishes the
        previous split or pane close first. Callers hold the pane lock, so no other command runs meanwhile.
        """
        if not runs_dir:
            raise TerminalError("wt backend needs a runs folder")
        self.registry = PaneRegistry(runs_dir)
        self.start_timeout = start_timeout
        self.profile = profile
        self.settle = settle

    def exe(self):
        """Return the wt.exe to call."""
        return os.environ.get("BZZ_WT_EXE") or "wt.exe"

    def wrapped(self, pidfile, command):
        """Return the argument list that runs `command` behind the pid-file launcher."""
        return [sys.executable, "-I", LAUNCHER, str(pidfile)] + [str(a) for a in command]

    def open_window_args(self, window, title, command, colour=None):
        """Return the wt sub-commands that open the named window with its first pane."""
        sub = ["-w", window, "new-tab"]
        if self.profile:
            sub += ["-p", self.profile]
        sub += ["--title", title, "--suppressApplicationTitle"]
        if colour:
            sub += ["--tabColor", colour]
        return [sub + list(command)]

    def split_pane_args(self, window, title, command, direction="horizontal", size=0.5, target=None):
        """Return the wt sub-commands that split a pane (the target pane, else the focused one)."""
        check_direction(direction)
        size = check_size(size)
        subs = []
        if target is not None:
            subs.append(["-w", window, "focus-pane", "-t", str(target)])
            first = ["split-pane"]
        else:
            first = ["-w", window, "split-pane"]
        if self.profile:
            first += ["-p", self.profile]
        # wt: -V puts the new pane beside the old one, -H below it
        first += ["-V" if direction == "horizontal" else "-H", "-s", "%g" % size]
        first += ["--title", title, "--suppressApplicationTitle"]
        subs.append(first + list(command))
        return subs

    def _launch(self, window, subs_builder, title, command, name, pane_id_index):
        """Start wt.exe with the built sub-commands, record the pane and wait for its pid file."""
        pane_id = "%s.%d" % (window, pane_id_index)
        folder_name = safe_name(name or "%s-%d" % (window, pane_id_index))
        folder = self.registry.folder(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        pidfile = folder / "pane.pid"
        if pidfile.exists():
            pidfile.unlink()
        self.registry.save(folder_name, {"id": pane_id, "window": window, "index": pane_id_index,
                                         "title": title, "pid": 0, "pidfile": str(pidfile)})
        line = wt_quote(self.exe()) + " " + wt_commandline(subs_builder(self.wrapped(pidfile, command)))
        time.sleep(self.settle)
        subprocess.Popen(line, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        pids = self._wait_pids(pidfile)
        if pids is None and self.start_timeout > 0:
            raise TerminalError("pane %s did not start within %s s" % (pane_id, self.start_timeout))
        return pane_id

    def _wait_pids(self, pidfile):
        """Wait for the launcher's pid file; return (launcher pid, command pid) or None."""
        deadline = time.time() + self.start_timeout
        while True:
            pids = self._read_pids(pidfile)
            if pids is not None or time.time() >= deadline:
                return pids
            time.sleep(0.2)

    def _read_pids(self, pidfile):
        """Return (launcher pid, command pid) from a pid file or None."""
        try:
            first, second = Path(pidfile).read_text(encoding="ascii").split()
            return int(first), int(second)
        except (OSError, ValueError):
            return None

    def open_window(self, window, title, command, colour=None, name=None):
        """Open the named window (new tab) with the first pane; return its pane id. The pane numbers start at 0 again."""
        self.registry.retire(window)
        index = self.registry.next_index(window)
        return self._launch(window, lambda wrapped: self.open_window_args(window, title, wrapped, colour),
                            title, command, name, index)

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5,
                   target=None, name=None):
        """Split a pane of the named window; `colour` has no effect (wt colours tabs, not panes)."""
        check_direction(direction)
        check_size(size)
        index = self.registry.next_index(window)
        if target is not None and "." in str(target):
            target = pane_number(str(target))
        return self._launch(window,
                            lambda wrapped: self.split_pane_args(window, title, wrapped, direction, size, target),
                            title, command, name, index)

    def _pids(self, rec):
        """Return (launcher pid, command pid) of a record, reading the pid file when it is there."""
        pids = self._read_pids(rec.get("pidfile", ""))
        return pids if pids is not None else (rec.get("pid", 0), 0)

    def close_pane(self, pane_id):
        """End the command's process tree; the launcher then exits 0 and wt closes the pane."""
        rec = self.registry.find(pane_id)
        if rec is None:
            raise TerminalError("unknown pane: %s" % (pane_id,))
        launcher, command = self._pids(rec)
        if command:
            _proc.kill_tree(command)
        deadline = time.time() + 5
        while _proc.pid_alive(launcher) and time.time() < deadline:
            time.sleep(0.1)
        if _proc.pid_alive(launcher):
            _proc.kill_tree(launcher)

    def list_panes(self, window):
        """Return the panes of the window; pid is the launcher's pid, alive = launcher still runs."""
        out = []
        for rec in self.registry.records():
            if rec.get("window") != window:
                continue
            launcher, _command = self._pids(rec)
            out.append(PaneInfo(rec["id"], rec.get("title", ""), launcher, _proc.pid_alive(launcher)))
        return out

    def set_title(self, pane_id, title):
        """Change the recorded title only: the visible title of a wt pane stays as created."""
        rec = self.registry.find(pane_id)
        if rec is None:
            raise TerminalError("unknown pane: %s" % (pane_id,))
        name = rec.pop("name")
        rec["title"] = title
        self.registry.save(name, rec)
