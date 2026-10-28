"""Small local process helpers for the terminal backends (a later step replaces them by haivequeen.platform)."""
import os
import signal
import subprocess
import sys
import time


def pid_alive(pid):
    """Return True when a process with this pid is still running."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_tree(pid):
    """End a process and all its children: `taskkill /T /F` on Windows, the process group on POSIX."""
    if not pid or pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    try:
        group = os.getpgid(int(pid))
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            if not pid_alive(pid):
                return
            time.sleep(0.1)
