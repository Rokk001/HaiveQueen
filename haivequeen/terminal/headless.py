"""Headless backend (C7): agents are background processes, output goes to runs/<agent>/output.log."""
import subprocess
import sys

from . import _proc
from .base import (PaneInfo, PaneRegistry, Terminal, TerminalError, check_direction, check_size,
                   safe_name)


class Headless(Terminal):
    """Runs every pane as a background process of its own process group; for tests and CI only."""

    name = "headless"

    def __init__(self, runs_dir):
        """Use `runs_dir` for pane records and output files."""
        if not runs_dir:
            raise TerminalError("headless backend needs a runs folder")
        self.registry = PaneRegistry(runs_dir)
        self._procs = {}

    def _start(self, window, title, command, name):
        """Start the command, record the pane and return its id."""
        index = self.registry.next_index(window)
        pane_id = "%s.%d" % (window, index)
        folder_name = safe_name(name or "%s-%d" % (window, index))
        folder = self.registry.folder(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        flags = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            flags["start_new_session"] = True
        with open(str(folder / "output.log"), "ab") as out:
            proc = subprocess.Popen(list(command), stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, **flags)
        self._procs[pane_id] = proc
        self.registry.save(folder_name, {"id": pane_id, "window": window, "index": index,
                                         "title": title, "pid": proc.pid})
        return pane_id

    def open_window(self, window, title, command, colour=None, name=None):
        """Start the first pane of `window` (colour is ignored)."""
        return self._start(window, title, command, name)

    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5,
                   target=None, name=None):
        """Start another background process (direction, size and colour have no meaning here)."""
        check_direction(direction)
        check_size(size)
        return self._start(window, title, command, name)

    def _alive(self, pane_id, pid):
        """Return whether the pane process runs (reaps finished children first)."""
        proc = self._procs.get(pane_id)
        if proc is not None and proc.pid == pid:
            return proc.poll() is None
        return _proc.pid_alive(pid)

    def close_pane(self, pane_id):
        """End the process tree of the pane."""
        rec = self.registry.find(pane_id)
        if rec is None:
            raise TerminalError("unknown pane: %s" % (pane_id,))
        _proc.kill_tree(rec["pid"])
        proc = self._procs.get(pane_id)
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    def list_panes(self, window):
        """Return the panes of `window` with their process state."""
        return [PaneInfo(r["id"], r.get("title", ""), r["pid"], self._alive(r["id"], r["pid"]))
                for r in self.registry.records() if r.get("window") == window]

    def set_title(self, pane_id, title):
        """Change the recorded title of a pane."""
        rec = self.registry.find(pane_id)
        if rec is None:
            raise TerminalError("unknown pane: %s" % (pane_id,))
        name = rec.pop("name")
        rec["title"] = title
        self.registry.save(name, rec)
