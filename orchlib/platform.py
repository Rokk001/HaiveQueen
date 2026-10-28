"""Operating-system helpers (Windows and Linux): memory, processes, folders. Standard library only.

Inside the package use `from orchlib import platform as osplat`; the standard module `platform`
is not affected because imports in this package are absolute.
"""
import csv
import ctypes
import io
import ntpath
import os
import posixpath
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

APP = "agent-orchestrator"
STILL_ACTIVE = 259


def is_windows() -> bool:
    """Return True when running on Windows."""
    return sys.platform == "win32"


def is_linux() -> bool:
    """Return True when running on Linux."""
    return sys.platform.startswith("linux")


def host_name() -> str:
    """Return the short name of this machine."""
    return socket.gethostname() or "host"


def python_executable() -> str:
    """Return the path of the running Python interpreter (for starting child tools)."""
    return sys.executable or "python"


def logical_cores() -> int:
    """Return the number of logical CPU cores (at least 1)."""
    return os.cpu_count() or 1


# ---------------------------------------------------------------- memory

def _win_free_ram_mb() -> int:
    """Windows: available physical memory in MB from GlobalMemoryStatusEx."""
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0
    return int(status.ullAvailPhys // (1024 * 1024))


def _linux_free_ram_mb(meminfo: str = "/proc/meminfo") -> int:
    """Linux: MemAvailable (fallback MemFree) from a meminfo file, in MB."""
    values = {}
    with open(meminfo, encoding="ascii", errors="replace") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0])
    kb = values.get("MemAvailable", values.get("MemFree", 0))
    return kb // 1024


def free_ram_mb() -> int:
    """Return the available physical memory in MB (0 when it cannot be read)."""
    try:
        return _win_free_ram_mb() if is_windows() else _linux_free_ram_mb()
    except (OSError, AttributeError, ValueError):
        return 0


# ---------------------------------------------------------------- processes

def _win_process_alive(pid: int) -> bool:
    """Windows: True when the process exists and has not exited (OpenProcess + GetExitCodeProcess)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return kernel32.GetLastError() == 5  # access denied: it exists
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _posix_process_alive(pid: int) -> bool:
    """POSIX: signal 0 probe; a permission error still means the process exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _linux_zombie(pid)


def _linux_zombie(pid: int) -> bool:
    """Linux: True when the process has ended but its parent has not collected it yet (state Z); a zombie is no running process."""
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


def process_alive(pid) -> bool:
    """Return True when a process with this PID is running. Never raises."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        return _win_process_alive(pid) if is_windows() else _posix_process_alive(pid)
    except (OSError, AttributeError):
        return False


def _win_kill_tree(pid: int) -> bool:
    """Windows: taskkill /PID <pid> /T /F; True when the process is gone afterwards."""
    result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                            capture_output=True, text=True, timeout=30)
    return result.returncode == 0 or not process_alive(pid)


def _posix_kill_tree(pid: int, grace: float = 2.0) -> bool:
    """POSIX: SIGTERM to the process group (SIGKILL after the grace time); True when gone."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return True
    group = pgid != os.getpgrp()  # never signal our own group

    def send(sig: int) -> None:
        """Send a signal to the group, or only to the process when it shares our group."""
        if group:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)

    try:
        send(signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and _posix_process_alive(pid):
            time.sleep(0.05)
        if _posix_process_alive(pid):
            send(getattr(signal, "SIGKILL", 9))
            time.sleep(0.1)
    except ProcessLookupError:
        pass
    return not _posix_process_alive(pid)


def kill_process_tree(pid) -> bool:
    """End a process and its children; return True when it is gone. Never raises.

    Only use this on processes the caller started itself (on POSIX the child should have been
    started with start_new_session=True so that it leads its own process group).
    """
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        return _win_kill_tree(pid) if is_windows() else _posix_kill_tree(pid)
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return False


def _win_process_list() -> list:
    """Windows: all (pid, image name) pairs from `tasklist /FO CSV /NH`."""
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=30)
    rows = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) >= 2 and row[1].isdigit():
            rows.append((int(row[1]), row[0]))
    return rows


def _linux_process_list(proc: str = "/proc") -> list:
    """Linux: all (pid, comm) pairs from /proc/<pid>/comm."""
    rows = []
    for entry in os.listdir(proc):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc, entry, "comm"), encoding="utf-8", errors="replace") as handle:
                rows.append((int(entry), handle.read().strip()))
        except OSError:
            continue
    return rows


def list_processes(names) -> list:
    """Return [(pid, name)] of running processes whose name is in names (case-insensitive).

    An empty list gives an empty result. On Linux the kernel truncates names to 15 characters,
    so a wanted name also matches its 15-character prefix.
    """
    wanted = {str(n).lower() for n in (names or []) if n}
    if not wanted:
        return []
    try:
        rows = _win_process_list() if is_windows() else _linux_process_list()
    except (OSError, subprocess.SubprocessError):
        return []
    short = {w[:15] for w in wanted}
    out = []
    for pid, name in rows:
        low = name.lower()
        if low in wanted or (not is_windows() and low in short):
            out.append((pid, name))
    return out


def count_processes(names) -> int:
    """Return how many running processes match the names (see list_processes)."""
    return len(list_processes(names))


# ---------------------------------------------------------------- network drives (C3)

DRIVE_REMOTE = 4   # GetDriveTypeW result for a network drive
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "ncpfs", "ceph", "glusterfs", "sshfs", "fuse.sshfs"}


def _win_is_network(path: str, drive_type=None) -> bool:
    """Windows: a UNC path or a drive letter that maps to a network share; `drive_type` replaces the system call (tests)."""
    text = ntpath.abspath(path)
    if text.startswith("\\\\?\\UNC\\"):
        return True
    if text.startswith("\\\\?\\"):
        text = text[4:]
    drive = ntpath.splitdrive(text)[0]
    if drive.startswith("\\\\"):
        return True
    if not drive:
        return False
    kind = drive_type if drive_type is not None else ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
    return kind == DRIVE_REMOTE


def _linux_is_network(path: str, mounts: str = None) -> bool:
    """Linux: the longest mount point that contains the path has a network file system type; `mounts` replaces /proc/mounts (tests)."""
    if mounts is None:
        try:
            with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
                mounts = handle.read()
        except OSError:
            return False
    target = posixpath.abspath(path)
    best, best_type = "", ""
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if (target == point or target.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            best, best_type = point, parts[2]
    return best_type in NETWORK_FS


def is_network_path(path) -> bool:
    """Return whether the path lies on a network drive (SQLite WAL does not work reliably there, C3)."""
    try:
        return _win_is_network(str(path)) if is_windows() else _linux_is_network(str(path))
    except (OSError, AttributeError):
        return False


# ---------------------------------------------------------------- folders (D3)

def _is_win(system) -> bool:
    """Resolve the optional system argument ('windows' or 'linux') to a flag."""
    return is_windows() if system is None else system == "windows"


def _folder(override: str, var_win: str, var_xdg: str, win_fallback: tuple, posix_fallback: tuple,
            system, env, home) -> Path:
    """Return the app folder: override variable, else the per-OS base variable, else the home fallback."""
    environ = os.environ if env is None else env
    if environ.get(override):
        return Path(environ[override])
    windows = _is_win(system)
    base = environ.get(var_win if windows else var_xdg)
    if base:
        return Path(base) / APP
    root = Path(home) if home else Path.home()
    return root.joinpath(*(win_fallback if windows else posix_fallback)) / APP


def config_dir(system=None, env=None, home=None) -> Path:
    """Return the settings folder: ORCH_CONFIG_DIR, else %APPDATA% (Windows) or $XDG_CONFIG_HOME (Linux).

    system ('windows'/'linux'), env (mapping) and home let tests check the other OS branch.
    """
    return _folder("ORCH_CONFIG_DIR", "APPDATA", "XDG_CONFIG_HOME", ("AppData", "Roaming"), (".config",),
                   system, env, home)


def data_dir(system=None, env=None, home=None) -> Path:
    """Return the machine data folder: ORCH_DATA_DIR, else %LOCALAPPDATA% (Windows) or $XDG_DATA_HOME (Linux)."""
    return _folder("ORCH_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME", ("AppData", "Local"),
                   (".local", "share"), system, env, home)


def logs_dir(**kw) -> Path:
    """Return <data>/logs."""
    return data_dir(**kw) / "logs"


def exports_dir(**kw) -> Path:
    """Return <data>/exports."""
    return data_dir(**kw) / "exports"


def runs_dir(**kw) -> Path:
    """Return <data>/runs."""
    return data_dir(**kw) / "runs"
