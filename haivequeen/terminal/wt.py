"""Windows Terminal backend (`wt.exe`), same commands and titles as the first version of the tool.

A named window (`-w <name>`) is addressed by every call. Panes are numbered in creation order, as
Windows Terminal does. Each command runs behind a small launcher (_panerun.py) that writes a pid
file into runs/<pane>/; closing a pane ends the command's process tree, the launcher then exits 0
and the terminal closes the pane itself. The visible title of every pane is the constant "HaiveQueen"
(`--suppressApplicationTitle`); the descriptive title is kept in the pane record, where the loop and the self-update find the pane.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .. import platform as osplat
from . import _proc
from .base import (PaneInfo, PaneRegistry, Terminal, TerminalError, check_direction, check_size,
                   safe_name)

LAUNCHER = str(Path(__file__).with_name("_panerun.py"))
SHARE_FALLBACKS = (0.9, 0.75, 0.5)   # smaller shares tried when wt makes no pane: it refuses a split that leaves the old pane too few lines (0.95 of a tall pane)
VISIBLE_TITLE = "HaiveQueen"   # what Windows Terminal shows for every pane and tab, so the window title never names a project, agent or Queen


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


# Windows Terminal through wt.exe (path override
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
        """Return the wt sub-commands that open the named window with its first pane (visible title: VISIBLE_TITLE; `title` only goes to the register)."""
        sub = ["-w", window, "new-tab"]
        if self.profile:
            sub += ["-p", self.profile]
        sub += ["--title", VISIBLE_TITLE, "--suppressApplicationTitle"]
        if colour:
            sub += ["--tabColor", colour]
        return [sub + list(command)]

    def split_pane_args(self, window, title, command, direction="horizontal", size=0.5, target=None):
        """Return the wt sub-commands that split a pane (the target pane, else the focused one); the visible title is VISIBLE_TITLE, `title` only goes to the register."""
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
        first += ["--title", VISIBLE_TITLE, "--suppressApplicationTitle"]
        subs.append(first + list(command))
        return subs

    # Start wt.exe with the built sub-commands, record the pane and wait for its pid file.
    def _launch(self, window, subs_builder, title, command, name, pane_id_index, what="call", after=None):
        """Start wt.exe with the built sub-commands, record the pane and wait for its pid file.

        wt's own output goes to wt.out and wt.err in the pane folder; with a trace sink one line says what was called and what came of it.
        `what` names the call for that line (open-window, or split with target, direction and size).
        `after` is the creation number of the pane this one was split off; it is kept in the record to rebuild wt's pane order.
        """
        pane_id = "%s.%d" % (window, pane_id_index)
        folder_name = safe_name(name or "%s-%d" % (window, pane_id_index))
        folder = self.registry.folder(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        pidfile = folder / "pane.pid"
        if pidfile.exists():
            pidfile.unlink()
        self.registry.save(folder_name, {"id": pane_id, "window": window, "index": pane_id_index,
                                         "title": title, "pid": 0, "pidfile": str(pidfile), "after": after})
        line = wt_quote(self.exe()) + " " + wt_commandline(subs_builder(self.wrapped(pidfile, command)))
        time.sleep(self.settle)
        with osplat.keep_foreground():
            with open(folder / "wt.out", "wb") as out, open(folder / "wt.err", "wb") as err:
                # the Beekeeper itself runs in a wt pane: wt.exe gets neither that pane's WT_* variables nor its console
                env = {k: v for k, v in os.environ.items() if not k.startswith("WT_")}
                flags = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {}   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                proc = subprocess.Popen(line, stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True, env=env, **flags)
            pids = self._wait_pids(pidfile)
            self._trace_call(pane_id, what, line, proc, pids, folder)
        if pids is None and self.start_timeout > 0:
            self.registry.discard(folder_name)   # wt made no pane: its number must not count for the next focus-pane target
            raise TerminalError("pane %s did not start within %s s" % (pane_id, self.start_timeout))
        return pane_id

    def _trace_call(self, pane_id, what, line, proc, pids, folder):
        """Give the trace sink one line about a wt call: pane, what, the whole command line, exit code (running while wt.exe still lives), stdout, stderr, pane.pid."""
        if self.trace is None:
            return
        try:
            code = proc.poll()
            text = {}
            for key in ("out", "err"):
                text[key] = (folder / ("wt." + key)).read_bytes().decode("utf-8", "replace").strip()
            self.trace("wt %s %s | %s | exit=%s | stdout=%r | stderr=%r | pane.pid=%s" % (
                pane_id, what, line, "running" if code is None else code, text["out"], text["err"],
                "no after %s s" % self.start_timeout if pids is None else "yes %s/%s" % pids))
        except Exception:   # the trace is a help for diagnosis: it never breaks a pane start
            pass

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
                            title, command, name, index, "open-window")

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5,
                   target=None, name=None):
        """Split a pane of the named window; `colour` has no effect (wt colours tabs, not panes)."""
        check_direction(direction)
        size = check_size(size)
        if target is not None and "." in str(target):
            target = pane_number(str(target))
        shares = [size] + [s for s in SHARE_FALLBACKS if s < size]
        for share in shares:
            try:
                return self._split(window, title, command, direction, share, target, name)
            except TerminalError:
                if target is not None and share == size:
                    # no pane came of the split with focus-pane: once more on the pane wt has focused (the one it made last); the trace shows both calls
                    try:
                        return self._split(window, title, command, direction, share, None, name)
                    except TerminalError:
                        pass
                if share == shares[-1]:
                    raise

    def wt_order(self, window):
        """Creation numbers of the panes of `window` in the order wt counts them for focus-pane (depth first through the split tree).

        A split pane sits right behind the pane it was split off (or, split off the focused pane, behind the pane made last); panes whose launcher has ended are left out.
        """
        order, last = [], None
        for rec in sorted((r for r in self.registry.records() if r.get("window") == window), key=lambda r: r.get("index", -1)):
            after = rec.get("after") if rec.get("after") is not None else last
            if after in order:
                order.insert(order.index(after) + 1, rec["index"])
            else:
                order.append(rec["index"])
            last = rec["index"]
        gone = {r["index"] for r in self.registry.records() if r.get("window") == window and not self._launcher_alive(r)}
        return [n for n in order if n not in gone]

    def _launcher_alive(self, rec):
        """False only when the record has a launcher pid that no longer runs (records without a pid count as alive)."""
        launcher, _command = self._pids(rec)
        return not launcher or _proc.pid_alive(launcher)

    def _split(self, window, title, command, direction, size, target, name):
        """One split call of wt with the next free pane number; `target` None splits the focused pane.

        `target` is a creation number; wt's focus-pane counts in tree order, so it is translated first (a target that is gone splits the focused pane).
        """
        index = self.registry.next_index(window)
        after, wt_target = None, None
        if target is not None:
            order = self.wt_order(window)
            after = int(target)
            wt_target = order.index(after) if after in order else None
        return self._launch(window,
                            lambda wrapped: self.split_pane_args(window, title, wrapped, direction, size, wt_target),
                            title, command, name, index,
                            "split target=%s direction=%s size=%g wt-index=%s" % (target, direction, size, wt_target), after)

    def _pids(self, rec):
        """Return (launcher pid, command pid) of a record, reading the pid file when it is there."""
        pids = self._read_pids(rec.get("pidfile", ""))
        return pids if pids is not None else (rec.get("pid", 0), 0)

    def close_pane(self, pane_id):
        """End the command's process tree; the launcher then exits 0 and wt closes the pane.

        Pauses `settle` seconds first (as before every split): the terminal finishes the previous split or pane close before the next pane goes.
        """
        rec = self.registry.find(pane_id)
        if rec is None:
            raise TerminalError("unknown pane: %s" % (pane_id,))
        time.sleep(self.settle)
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
