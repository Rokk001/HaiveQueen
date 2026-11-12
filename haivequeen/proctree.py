"""Leftover processes of agents: end every process an agent started once the agent ends.

A Queen or Bee runner watches the process tree below itself and remembers every process it sees (pid and birth identity), so a grep or find that
kept running after `| head` is still known when its parent is long gone. When the agent ends (normally, with an error or stopped) the runner ends
all of them that still run. The list is also kept in the run folder (processes.json), so an agent that vanished with its runner (a restart, a
crash) has its leftovers ended at the next start of HaiveQueen.

Processes that must outlive their starter register themselves as exempt (the runner of a Bee started through `bzz bee start`, a compile check
started by HaiveQueen); an exempt process and everything below it is never ended by the cleanup of another agent.
"""
import json
import os
import signal
import threading
import time
from pathlib import Path

from . import config
from . import platform as osplat

STATE_FILE = "processes.json"
POLL_SECONDS = 1.0


def exempt_dir() -> Path:
    """Folder with one file per exempt process (named by its pid, holding its identity)."""
    return config.data_dir() / "exempt-processes"


def exempt_register(pid=None) -> None:
    """Mark a process (default: this one) and everything it starts as exempt from the cleanup of an agent's leftovers."""
    pid = int(pid or os.getpid())
    try:
        folder = exempt_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / str(pid)).write_text(osplat.process_identity(pid) or "", encoding="utf-8")
    except OSError:
        pass


def exempt_clear(pid=None) -> None:
    """Remove the exempt mark of a process."""
    try:
        (exempt_dir() / str(int(pid or os.getpid()))).unlink()
    except (OSError, ValueError):
        pass


def is_exempt(pid, parents) -> bool:
    """True when the process or one of its recorded ancestors (`parents`: pid -> parent pid) carries a valid exempt mark."""
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        try:
            recorded = (exempt_dir() / str(pid)).read_text(encoding="utf-8")
        except OSError:
            recorded = None
        if recorded is not None and (not recorded or recorded == (osplat.process_identity(pid) or recorded)):
            return True
        pid = parents.get(pid)
    return False


def snapshot() -> dict:
    """All running processes as {pid: parent pid}."""
    if osplat.is_windows():
        try:
            return {pid: parent for pid, parent, _name in osplat._win_snapshot()}
        except OSError:
            return {}
    out = {}
    try:
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                try:
                    with open("/proc/%s/stat" % entry, encoding="ascii", errors="replace") as stat:
                        out[int(entry)] = int(stat.read().rsplit(")", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    continue
    except OSError:
        pass
    return out


def descendants(root, table=None) -> dict:
    """{pid: parent pid} of every process below `root` (not `root` itself). On Windows a process counts as a child only when it was created after its
    parent: process ids are reused, a stranger whose recorded parent id equals a long-gone parent's must not be pulled in."""
    table = snapshot() if table is None else table
    children = {}
    for pid, parent in table.items():
        children.setdefault(parent, []).append(pid)
    found, stack = {}, [int(root)]
    while stack:
        current = stack.pop()
        born = osplat._win_process_creation(current) if osplat.is_windows() else None
        for child in children.get(current, []):
            if child in found or child == int(root):
                continue
            if born is not None:
                created = osplat._win_process_creation(child)
                if created is not None and created < born:
                    continue
            found[child] = current
            stack.append(child)
    return found


def tree_cpu_seconds(root) -> float:
    """The CPU time used so far by `root` and all processes below it, in seconds."""
    pids = [int(root)] + list(descendants(root))
    return sum(osplat.process_cpu_seconds(pid) for pid in pids)


def _terminate(pid) -> None:
    """End one process at once."""
    try:
        if osplat.is_windows():
            osplat._win_terminate(pid)
        else:
            os.kill(pid, getattr(signal, "SIGKILL", 9))
    except (OSError, AttributeError):
        pass


def kill_recorded(procs, protect=()) -> list:
    """End the recorded processes ({pid: {"identity", "ppid"}}) that still run with their recorded identity and are not exempt; returns the pids ended.
    Children go before parents."""
    parents = {int(pid): info.get("ppid") for pid, info in procs.items()}
    exempt = {pid for pid in parents if is_exempt(pid, parents)}
    ancestors = set()
    for pid in exempt:
        parent = parents.get(pid)
        while parent in parents and parent not in ancestors:
            ancestors.add(parent)
            parent = parents.get(parent)
    def depth(pid):
        seen = set()
        while pid in parents and pid not in seen:
            seen.add(pid)
            pid = parents[pid]
        return len(seen)

    ended = []
    for pid in sorted(parents, key=depth, reverse=True):
        info = procs[pid] if pid in procs else procs[str(pid)]
        if pid in protect or not osplat.process_alive(pid):
            continue
        identity = info.get("identity")
        if identity and osplat.process_identity(pid) != identity:
            continue   # the process id belongs to another process now
        # Ending its debugger would also end an exempt compile command or Bee.
        # Other ancestors keep the existing cleanup behavior, and ordinary
        # descendants of a supervisor remain eligible for cleanup.
        if pid in exempt or (pid in ancestors and osplat.is_console_supervisor(pid)):
            continue
        _terminate(pid)
        ended.append(pid)
    return ended


class Tracker:
    """Remembers every process below `root` while the agent runs and ends the leftovers when it is over."""

    def __init__(self, root, folder=None, interval=POLL_SECONDS):
        self.root = int(root)
        self.folder = Path(folder) if folder else None
        self.interval = interval
        self.procs = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def poll(self) -> None:
        """Record the processes below the root that are not known yet."""
        table = snapshot()
        found = descendants(self.root, table)
        changed = False
        with self.lock:
            for pid, parent in found.items():
                identity = osplat.process_identity(pid)
                known = self.procs.get(pid)
                if known is None or (identity and known.get("identity") != identity):
                    self.procs[pid] = {"identity": identity, "ppid": parent}
                    changed = True
            if changed:
                self.save()

    def save(self) -> None:
        """Keep the list in the run folder, so a later start of HaiveQueen can end the leftovers of this agent."""
        if not self.folder:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            data = {"root": self.root, "rootIdentity": osplat.process_identity(self.root), "procs": {str(p): i for p, i in self.procs.items()}}
            (self.folder / STATE_FILE).write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _loop(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.poll()
            except Exception:
                pass

    def start(self) -> "Tracker":
        """Record the first processes and keep watching in a thread."""
        self.poll()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def finish(self) -> list:
        """Stop watching and end every process of the agent that still runs; returns the pids ended."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        ended = []
        for _ in range(3):   # a process may start another one while it is ended
            try:
                self.poll()
            except Exception:
                pass
            with self.lock:
                procs = dict(self.procs)
            now = kill_recorded(procs, protect=(self.root, os.getpid()))
            ended += now
            if not now:
                break
            time.sleep(0.1)
        if self.folder:
            try:
                (self.folder / STATE_FILE).unlink()
            except OSError:
                pass
        return ended


def sweep(runs_root) -> list:
    """At the start of HaiveQueen: end the leftovers of agents that ended while it was not running. Every run folder below `runs_root` (project/agent)
    with a processes.json whose runner no longer runs gets its recorded processes ended (only when their process identity still matches) and the file removed."""
    ended = []
    root = Path(runs_root)
    if not root.is_dir():
        return ended
    for state in list(root.glob("*/*/" + STATE_FILE)):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            pid = int(data.get("root") or 0)
        except (OSError, ValueError):
            continue
        if pid and osplat.process_alive(pid) and (not data.get("rootIdentity") or osplat.process_identity(pid) == data["rootIdentity"]):
            continue   # the runner still watches its agent
        procs = {int(p): i for p, i in (data.get("procs") or {}).items()}
        ended += kill_recorded(procs)
        try:
            state.unlink()
        except OSError:
            pass
    return ended
