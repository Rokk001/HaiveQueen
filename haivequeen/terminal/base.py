"""One terminal interface for the Windows Terminal, tmux and headless backends."""
import abc
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TITLE_PREFIX = "HaiveQueen"


class TerminalError(Exception):
    """Raised when a terminal backend cannot do what was asked."""


@dataclass
class PaneInfo:
    """Description of one pane: id, title, process id of the pane and whether it is alive."""
    id: str
    title: str
    pid: int
    alive: bool


def pane_title(project, who, job, model, test=False):
    """Return the fixed pane title: "HaiveQueen | <project> | <who> | job: <job> | model: <model>".

    `who` is e.g. "Agent NAME" or "QUEEN"; test runtimes say "HaiveQueen TEST".
    """
    prefix = TITLE_PREFIX + (" TEST" if test else "")
    return "%s | %s | %s | job: %s | model: %s" % (prefix, project, who, job, model)


def safe_name(text):
    """Return text reduced to characters that are safe in a folder name."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("._") or "pane"


class PaneRegistry:
    """Pane records on disk: <runs_dir>/<name>/pane.json, so any process can list and close panes."""

    def __init__(self, runs_dir):
        """Remember the runs folder."""
        self.runs_dir = Path(runs_dir)

    def folder(self, name):
        """Return the folder of one pane record."""
        return self.runs_dir / name

    def save(self, name, record):
        """Write the record of a pane (atomic replace)."""
        folder = self.folder(name)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "pane.json"
        tmp = folder / "pane.json.tmp"
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(str(tmp), str(target))

    def records(self):
        """Return all pane records as dicts (each with its `name`)."""
        out = []
        if not self.runs_dir.is_dir():
            return out
        for path in sorted(self.runs_dir.glob("*/pane.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rec["name"] = path.parent.name
            out.append(rec)
        return out

    def find(self, pane_id):
        """Return the record with this pane id or None."""
        for rec in self.records():
            if rec.get("id") == pane_id:
                return rec
        return None

    def next_index(self, window):
        """Return the next pane number of a window (creation order, never reused)."""
        used = [r.get("index", -1) for r in self.records() if r.get("window") == window]
        return max(used) + 1 if used else 0


class Terminal(abc.ABC):
    """Interface of a terminal backend.

    Directions: "horizontal" puts the new pane beside the old one (left/right),
    "vertical" puts it below. `size` is the share of the space the new pane takes (0..1).
    `command` is always an argument list; each backend quotes it for its own terminal.
    """

    name = ""

    @abc.abstractmethod
    def open_window(self, window, title, command, colour=None, name=None):
        """Open the orchestrator window with its first pane running `command`; return the pane id."""

    @abc.abstractmethod
    def split_pane(self, window, title, colour, command, direction="horizontal", size=0.5,
                   target=None, name=None):
        """Split a pane of `window` (the `target` pane, else the newest) and run `command`; return the new pane id."""

    @abc.abstractmethod
    def close_pane(self, pane_id):
        """Close a pane by ending its process (the pane then closes itself)."""

    @abc.abstractmethod
    def list_panes(self, window):
        """Return a list of PaneInfo for the panes of `window`."""

    @abc.abstractmethod
    def set_title(self, pane_id, title):
        """Set the title of a pane."""


def check_direction(direction):
    """Validate a split direction and return it."""
    if direction not in ("horizontal", "vertical"):
        raise TerminalError("direction must be 'horizontal' or 'vertical': %r" % (direction,))
    return direction


def check_size(size):
    """Validate a split size (share between 0 and 1, exclusive) and return it as float."""
    value = float(size)
    if not 0 < value < 1:
        raise TerminalError("size must be between 0 and 1: %r" % (size,))
    return value


def get_terminal(name="auto", runs_dir=None, **options):
    """Return the backend "wt", "tmux" or "headless"; "auto" picks wt on Windows and tmux elsewhere.

    `runs_dir` (or the environment variable BZZ_RUNS_DIR) is where pane records and outputs live.
    """
    if name == "auto":
        name = "wt" if sys.platform == "win32" else "tmux"
    if runs_dir is None:
        runs_dir = os.environ.get("BZZ_RUNS_DIR")
    if name == "wt":
        from .wt import WindowsTerminal
        return WindowsTerminal(runs_dir, **options)
    if name == "tmux":
        from .tmux import Tmux
        return Tmux(**options)
    if name == "headless":
        from .headless import Headless
        return Headless(runs_dir, **options)
    raise TerminalError("unknown terminal backend: %r" % (name,))
