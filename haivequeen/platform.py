"""Operating-system helpers (Windows and Linux): memory, processes, folders. Standard library only.

Inside the package use `from haivequeen import platform as osplat`; the standard module `platform`
is not affected because imports in this package are absolute.
"""
import ctypes
import inspect
import ntpath
import os
import posixpath
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime

APP = "HaiveQueen"
STILL_ACTIVE = 259


def is_windows() -> bool:
    """Return True when running on Windows."""
    return sys.platform == "win32"


def is_linux() -> bool:
    """Return True when running on Linux."""
    return sys.platform.startswith("linux")


# Suppress console allocation for subprocess launches in this interpreter.
def hide_console_windows() -> None:
    """On Windows replace console creation requests with CREATE_NO_WINDOW.

    Detached calls keep their flags. GUI startup visibility is untouched. Safe
    to call more than once. A native debugger broker intercepts ordinary Win32
    descendant creation before allocation; console_tree documents its
    known limitation, accepted under owner decision 1117, when native tools
    establish their own debugging chains and Windows redirects those events.
    """
    if not is_windows() or getattr(subprocess.Popen, "_hq_no_window", False):
        return
    no_window, detached, new_console = 0x08000000, 0x00000008, 0x00000010
    original = subprocess.Popen.__init__

    try:
        position = list(inspect.signature(original).parameters).index("creationflags") - 1   # without self
    except ValueError:   # another wrapper without the named parameter is already installed
        position = 13

    def patched(self, *args, **kwargs):
        positional = len(args) > position
        flags = (args[position] if positional else kwargs.get("creationflags", 0)) or 0
        if not flags & detached:
            flags = (flags & ~new_console) | no_window
            if positional:
                args = args[:position] + (flags,) + args[position + 1:]
            else:
                kwargs["creationflags"] = flags
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched
    subprocess.Popen._hq_no_window = True

    import _winapi
    if ctypes.windll.kernel32.IsDebuggerPresent():
        return  # an existing debugger owns this process's debug events
    import importlib.util
    spec = importlib.util.spec_from_file_location('_hq_console_tree', Path(__file__).with_name('console_tree.py'))
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    create = _winapi.CreateProcess

    def create_process(*args):
        return policy.create_process(create, args)

    _winapi.CreateProcess = create_process


hide_console_windows()


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


# Read the immutable creation tick count of a Windows process.
def _win_process_creation(pid):
    """Return a Windows FILETIME creation value, or None when inaccessible."""
    try:
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None
        try:
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(ctypes.c_void_p(handle), ctypes.byref(created),
                                          ctypes.byref(exited), ctypes.byref(kernel_time),
                                          ctypes.byref(user_time)):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    except (OSError, AttributeError, TypeError, ValueError, OverflowError):
        return None


# Tell whether Windows refuses this program access to a process.
def process_inaccessible(pid) -> bool:
    """True when a process exists but Windows denies access to it (error 5): a process an agent of this user started can always be opened,
    so a denied one (for example a system service that got a recycled PID) is a stranger. False on other systems and for any other failure."""
    if not is_windows():
        return False
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if handle:
            kernel.CloseHandle(ctypes.c_void_p(handle))
            return False
        return ctypes.get_last_error() == 5
    except (OSError, AttributeError, TypeError, ValueError, OverflowError):
        return False


# Return a stable identity that distinguishes a reused PID on Windows or Linux.
def process_identity(pid):
    """Return a process birth identity, or None when it cannot be verified."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if is_windows():
            created = _win_process_creation(pid)
            return "win:%d" % created if created is not None else None
        if is_linux():
            with open('/proc/sys/kernel/random/boot_id', encoding='ascii') as boot:
                boot_id = boot.read().strip()
            with open('/proc/%d/stat' % pid, encoding='ascii') as stat:
                fields = stat.read().rsplit(')', 1)[1].split()
            return "linux:%s:%s" % (boot_id, fields[19]) if boot_id and fields[19].isdigit() else None
    except (OSError, ValueError, TypeError, IndexError):
        return None
    return None


# Return a Windows process creation time for records made before identities existed.
def process_start_time(pid):
    """Return local Windows process creation time, or None when unavailable."""
    if not is_windows():
        return None
    ticks = _win_process_creation(pid)
    if ticks is None:
        return None
    try:
        return datetime.fromtimestamp(ticks / 10000000 - 11644473600)
    except (OSError, ValueError, OverflowError):
        return None


# Return the command line of a running process.
def process_command_line(pid):
    """Command line of the process as one text ("" when it has none); None when it cannot be read (no such process, no access, no tool)."""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if is_linux():
            with open('/proc/%d/cmdline' % pid, 'rb') as handle:
                return handle.read().replace(bytes([0]), b' ').decode('utf-8', 'replace').strip()
        if is_windows():
            script = "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid
            res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=0x08000000)   # CREATE_NO_WINDOW
            return res.stdout.strip() if res.returncode == 0 else None
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None
    return None


def is_console_supervisor(pid) -> bool:
    """Recognize this installation's private supervisor command exactly."""
    if not is_windows():
        return False
    executable = Path(sys.executable).with_name('pythonw.exe')
    if not executable.exists():
        executable = Path(sys.executable)
    prefix = subprocess.list2cmdline([str(executable), '-I', str(Path(__file__).with_name('console_tree.py').resolve())]) + ' '
    command = process_command_line(pid) or ''
    if not command.casefold().startswith(prefix.casefold()):
        return False
    arguments = command[len(prefix):].split()
    return len(arguments) == 3 and all(value.isdecimal() for value in arguments)


# Collect a process and everything it started, children before parents.
def _win_cpu_seconds(pid: int) -> float:
    """Windows: kernel plus user CPU time of one process in seconds; 0 when the process is gone or not accessible."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0
    try:
        created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, exited, kern, user))):
            return 0.0
        return sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in (kern, user)) / 1e7
    finally:
        kernel.CloseHandle(handle)


def _linux_cpu_seconds(pid: int, proc: str = "/proc") -> float:
    """Linux: user plus system CPU time of one process in seconds; 0 when the process is gone."""
    try:
        with open("%s/%s/stat" % (proc, pid), encoding="ascii", errors="replace") as stat:
            fields = stat.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return 0.0


def process_cpu_seconds(pid) -> float:
    """CPU time used so far by one process in seconds (0 when it is gone or not readable)."""
    try:
        return _win_cpu_seconds(int(pid)) if is_windows() else _linux_cpu_seconds(int(pid))
    except (OSError, ValueError, AttributeError):
        return 0.0


def _win_tree(pid: int) -> list:
    """Return `pid` and all its descendants from the process snapshot, deepest first.

    A process counts as a child only when its parent id matches and it was created after that parent: Windows reuses process ids,
    so a stranger whose recorded parent id equals a long-gone parent's must not be pulled in."""
    children = {}
    for child, parent, _name in _win_snapshot():
        children.setdefault(parent, []).append(child)
    order, stack, seen = [], [pid], {pid}
    while stack:
        current = stack.pop()
        order.append(current)
        born = _win_process_creation(current)
        for child in children.get(current, []):
            created = _win_process_creation(child)
            if child in seen or (born is not None and created is not None and created < born):
                continue
            seen.add(child)
            stack.append(child)
    return order[::-1]


# End one Windows process at once.
def _win_terminate(pid: int) -> None:
    """Windows: TerminateProcess on `pid` and wait until the process object is signalled (nothing happens when it is gone or may not be ended).

    Waiting for the signal, not only for the exit code, matters: right after the exit code shows, Windows may still hold the process's open files,
    so a folder removed at once could not be deleted."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100001, False, pid)  # SYNCHRONIZE | PROCESS_TERMINATE
    if not handle:
        return
    try:
        kernel32.TerminateProcess(ctypes.c_void_p(handle), 1)
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 5000)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _win_kill_tree(pid: int) -> bool:
    """Windows: end the process and all its descendants (what `taskkill /PID <pid> /T /F` did, without starting a program); True when it is gone."""
    for victim in _win_tree(pid):
        _win_terminate(victim)
    deadline = time.monotonic() + 5
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not process_alive(pid)


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


def run_with_timeout(argv: list, timeout: float, cwd=None, input_bytes=None, env=None) -> tuple:
    """Run a program and end it WITH ALL ITS CHILDREN when it does not finish within `timeout` seconds.

    Returns (exit code, stdout bytes, stderr bytes, timed_out). A plain subprocess.run timeout only ends the direct child, a
    grandchild that keeps the pipes open would hang the caller; here the whole tree is ended. A missing program raises OSError.
    """
    kwargs = {} if is_windows() else {"start_new_session": True}
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    try:
        out, err = proc.communicate(input_bytes, timeout=timeout)
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = b"", b""
        return -1, out or b"", err or b"", True


def _win_process_list() -> list:
    """Windows: all (pid, image name) pairs from a Toolhelp snapshot."""
    return [(pid, name) for pid, _parent, name in _win_snapshot()]


_WIN_SNAPSHOT_LOCK = threading.Lock()
_WIN_SNAPSHOT_API = None


def _win_snapshot_api() -> tuple:
    """Windows: returns (kernel32, ProcessEntry class) and sets the Toolhelp function types exactly once.

    The structure class and the argtypes of the shared kernel32 functions must be the same for every caller: a class created
    per call made concurrent callers (process cleanup and the build monitor) overwrite each other's argtypes, so ctypes rejected
    a pointer to the other thread's ProcessEntry class."""
    global _WIN_SNAPSHOT_API
    with _WIN_SNAPSHOT_LOCK:
        if _WIN_SNAPSHOT_API is None:
            from ctypes import wintypes

            class ProcessEntry(ctypes.Structure):
                """PROCESSENTRY32W of tlhelp32.h."""
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                            ("szExeFile", ctypes.c_wchar * 260)]

            kernel = ctypes.windll.kernel32
            kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
            kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            _WIN_SNAPSHOT_API = (kernel, ProcessEntry)
        return _WIN_SNAPSHOT_API


def _win_snapshot() -> list:
    """Windows: all (pid, parent pid, image name) triples from a Toolhelp snapshot.

    The Windows API is called directly: a helper program such as `tasklist` opens a console window each time when the caller
    (the graphical dashboard, a detached process) has no console of its own. Raises OSError when the snapshot cannot be taken."""
    kernel, ProcessEntry = _win_snapshot_api()
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("process snapshot failed")
    rows = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
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


# ---------------------------------------------------------------- RAM of a process tree (build measurement)

_WIN_MEMORY_LOCK = threading.Lock()
_WIN_MEMORY_API = None


def _win_memory_api() -> tuple:
    """Windows: returns (kernel32, Counters class) and sets the process memory function types exactly once.

    Same reason as for the process snapshot: a class created per call made concurrent callers overwrite each other's argtypes."""
    global _WIN_MEMORY_API
    with _WIN_MEMORY_LOCK:
        if _WIN_MEMORY_API is None:
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                """PROCESS_MEMORY_COUNTERS of psapi.h."""
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            kernel = ctypes.windll.kernel32
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), wintypes.DWORD]
            _WIN_MEMORY_API = (kernel, Counters)
        return _WIN_MEMORY_API


def _win_working_set_mb(pid: int) -> float:
    """Windows: the working set (RAM in use) of one process in MB; 0 when the process is gone or not accessible."""
    kernel, Counters = _win_memory_api()
    handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.WorkingSetSize / (1024.0 * 1024.0)
    finally:
        kernel.CloseHandle(handle)


def _linux_parent_rows(proc: str = "/proc") -> list:
    """Linux: all (pid, parent pid, comm) triples from /proc/<pid>/stat."""
    rows = []
    for entry in os.listdir(proc):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc, entry, "stat"), encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        left, right = text.find("("), text.rfind(")")
        if left < 0 or right < left:
            continue
        fields = text[right + 2:].split()
        if len(fields) >= 2:
            rows.append((int(entry), int(fields[1]), text[left + 1:right]))
    return rows


def _linux_rss_mb(pid: int, proc: str = "/proc") -> float:
    """Linux: resident memory of one process in MB (statm pages x page size); 0 when the process is gone."""
    try:
        with open(os.path.join(proc, str(pid), "statm"), encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)
    except (OSError, ValueError, IndexError, AttributeError):
        return 0.0


def process_tree_ram(root_pid, names) -> tuple:
    """(RAM in MB, number of processes) of the processes below `root_pid` (children, grandchildren, ...) whose name is in `names`.

    Only the tree of the one process counts: a process with the same name that another program started is not part of it. The root itself
    counts when its name matches. Without a root, without names or when the process list cannot be read the result is (0.0, 0)."""
    wanted = {str(n).lower() for n in (names or []) if n}
    try:
        root = int(root_pid)
    except (TypeError, ValueError):
        return 0.0, 0
    if not wanted or root <= 0:
        return 0.0, 0
    try:
        rows = _win_snapshot() if is_windows() else _linux_parent_rows()
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0
    children = {}
    for pid, parent, _name in rows:
        children.setdefault(parent, []).append(pid)
    names_by_pid = {pid: name for pid, _parent, name in rows}
    short = {w[:15] for w in wanted}
    seen, stack, total, count = {root}, [root], 0.0, 0
    while stack:
        pid = stack.pop()
        name = names_by_pid.get(pid, "").lower()
        if name in wanted or (not is_windows() and name in short):
            total += _win_working_set_mb(pid) if is_windows() else _linux_rss_mb(pid)
            count += 1
        for child in children.get(pid, ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return total, count


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
    """Return the settings folder: BZZ_CONFIG_DIR, else %APPDATA% (Windows) or $XDG_CONFIG_HOME (Linux).

    system ('windows'/'linux'), env (mapping) and home let tests check the other OS branch.
    """
    return _folder("BZZ_CONFIG_DIR", "APPDATA", "XDG_CONFIG_HOME", ("AppData", "Roaming"), (".config",),
                   system, env, home)


def data_dir(system=None, env=None, home=None) -> Path:
    """Return the machine data folder: BZZ_DATA_DIR, else %LOCALAPPDATA% (Windows) or $XDG_DATA_HOME (Linux)."""
    return _folder("BZZ_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME", ("AppData", "Local"),
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
