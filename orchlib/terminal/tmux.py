"""tmux backend: one tmux session per orchestrator window, panes addressed by tmux's own pane ids."""
import os
import shlex
import subprocess

from .base import PaneInfo, Terminal, TerminalError, check_direction, check_size

SEP = "\t"
LIST_FORMAT = SEP.join(["#{pane_id}", "#{pane_title}", "#{pane_pid}", "#{pane_dead}"])


def tmux_command(command):
    """Quote an argument list as the single shell-command string tmux expects."""
    return " ".join(shlex.quote(str(a)) for a in command)


class Tmux(Terminal):
    """tmux through its command line (path override: environment variable ORCH_TMUX_EXE)."""

    name = "tmux"

    def __init__(self):
        """Nothing to prepare: tmux keeps all state itself."""

    def exe(self):
        """Return the tmux executable."""
        return os.environ.get("ORCH_TMUX_EXE") or "tmux"

    def _run(self, *args):
        """Run one tmux command; return its stdout or raise TerminalError."""
        result = subprocess.run([self.exe()] + [str(a) for a in args], capture_output=True, text=True)
        if result.returncode != 0:
            raise TerminalError("tmux %s failed: %s" % (args[0], (result.stderr or "").strip()))
        return result.stdout

    def _style(self, pane_id, title, colour):
        """Set the pane title and, when given, its background colour."""
        self._run("select-pane", "-t", pane_id, "-T", title)
        if colour:
            self._run("select-pane", "-t", pane_id, "-P", "bg=%s" % colour)

    def open_window(self, window, title, command, colour=None, name=None):
        """Create the detached session `window` with the first pane; return its pane id."""
        out = self._run("new-session", "-d", "-s", window, "-x", "200", "-y", "50",
                        "-P", "-F", "#{pane_id}", tmux_command(command))
        pane_id = out.strip()
        self._run("set-window-option", "-t", window, "pane-border-status", "top")
        self._run("set-window-option", "-t", window, "pane-border-format", " #{pane_title} ")
        self._style(pane_id, title, colour)
        return pane_id

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5,
                   target=None, name=None):
        """Split the target pane (else the session's active pane); return the new pane id."""
        check_direction(direction)
        size = check_size(size)
        out = self._run("split-window", "-t", target or window,
                        "-h" if direction == "horizontal" else "-v",
                        "-l", "%d%%" % round(size * 100), "-P", "-F", "#{pane_id}",
                        tmux_command(command))
        pane_id = out.strip()
        self._style(pane_id, title, colour)
        return pane_id

    def close_pane(self, pane_id):
        """Kill the pane (tmux ends its processes)."""
        self._run("kill-pane", "-t", pane_id)

    def list_panes(self, window):
        """Return the panes of the session as PaneInfo (alive = not dead)."""
        out = self._run("list-panes", "-s", "-t", window, "-F", LIST_FORMAT)
        panes = []
        for line in out.splitlines():
            if not line.strip():
                continue
            pane_id, rest = line.split(SEP, 1)
            title, pid, dead = rest.rsplit(SEP, 2)
            panes.append(PaneInfo(pane_id, title, int(pid or 0), dead.strip() != "1"))
        return panes

    def set_title(self, pane_id, title):
        """Set the pane title."""
        self._run("select-pane", "-t", pane_id, "-T", title)
