"""Conservative process table inspection for version directory recovery."""
import ctypes
from ctypes import wintypes
import ntpath
import os
import sys
from pathlib import Path


def scan(directory):
    """Return absence only when the platform inspection can prove it."""
    if sys.platform.startswith('linux'):
        return scan_linux(directory)
    if sys.platform == 'win32':
        return scan_windows(directory)
    return None


def _contains(directory, value):
    # A command line can contain an option followed by an absolute path.
    # Substring matches intentionally err on the side of retaining a version.
    return str(directory) in value


def scan_linux(directory, proc=Path('/proc')):
    """Return True if in use, False if absent, None if inspection is uncertain."""
    directory = Path(directory).resolve()
    try:
        processes = list(proc.iterdir())
    except OSError:
        return None
    uncertain = False
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            stat = (process / 'stat').read_text(encoding='utf-8')
            # Zombie processes cannot use a version folder.
            if stat[stat.rfind(')') + 2:][:1] == 'Z':
                continue
            exe = os.readlink(process / 'exe')
            cwd = os.readlink(process / 'cwd')
            cmd = (process / 'cmdline').read_bytes().decode('utf-8', 'surrogateescape')
            if any(_contains(directory, v) for v in (exe, cwd, cmd)):
                return True
        except (OSError, ValueError):
            # Vanishing processes are harmless, but inaccessible live ones are
            # unknown. Recheck existence because proc entries race with exit.
            if process.exists():
                uncertain = True
    return None if uncertain else False


def _windows_path_text(value):
    value = ntpath.normcase(value)
    return value.replace('\\\\?\\unc\\', '\\\\').replace('\\\\?\\', '')


def scan_windows(directory):
    """Return True for a match, False for a clear scan, or None on doubt."""
    try:
        directories = {_windows_path_text(str(path)).rstrip('\\')
                       for path in (Path(directory).absolute(), Path(directory).resolve())}
        processes = _WindowsProcesses()
        pids = processes.pids()
    except (OSError, AttributeError, ValueError, RuntimeError):
        return None

    def matches(value):
        # The path can occur inside an argument, such as --script=<path>.
        value = _windows_path_text(value)
        return any(path in value for path in directories)

    uncertain = False
    for pid in pids:
        if pid == 0:  # The idle process has no user-mode address space.
            continue
        result = processes.uses(pid, matches)
        if result is True:
            return True
        if result is None:
            uncertain = True
    return None if uncertain else False


class _WindowsProcesses:
    _QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    _WAIT = 0x100000  # SYNCHRONIZE
    _READ = 0x0410  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ

    def __init__(self):
        self.kernel = kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
        dword = wintypes.DWORD
        handle = wintypes.HANDLE
        pointer = ctypes.c_void_p
        kernel.K32EnumProcesses.argtypes = [ctypes.POINTER(dword), dword, ctypes.POINTER(dword)]
        kernel.OpenProcess.argtypes = [dword, wintypes.BOOL, dword]
        kernel.OpenProcess.restype = handle
        kernel.CloseHandle.argtypes = [handle]
        kernel.WaitForSingleObject.argtypes = [handle, dword]
        kernel.WaitForSingleObject.restype = dword
        kernel.QueryFullProcessImageNameW.argtypes = [handle, dword, wintypes.LPWSTR, ctypes.POINTER(dword)]
        kernel.IsWow64Process2.argtypes = [handle, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT)]
        kernel.ReadProcessMemory.argtypes = [handle, pointer, pointer, ctypes.c_size_t,
                                             ctypes.POINTER(ctypes.c_size_t)]
        self.query.argtypes = [handle, dword, pointer, dword, pointer]

    def pids(self):
        for capacity in (1024, 4096, 16384, 65536):
            pids = (wintypes.DWORD * capacity)()
            used = wintypes.DWORD()
            size = ctypes.sizeof(pids)
            if not self.kernel.K32EnumProcesses(pids, size, ctypes.byref(used)):
                raise ctypes.WinError(ctypes.get_last_error())
            count, leftover = divmod(used.value, ctypes.sizeof(wintypes.DWORD))
            if 0 < used.value < size and not leftover:
                return pids[:count]
        raise OSError('Incomplete Windows process enumeration')

    def uses(self, pid, matches):
        kernel = self.kernel
        readable = True
        process = kernel.OpenProcess(self._QUERY | self._WAIT | self._READ, False, pid)
        if not process:
            readable = False
            process = kernel.OpenProcess(self._QUERY | self._WAIT, False, pid)
            if not process:
                # ERROR_INVALID_PARAMETER for a nonzero PID means it vanished.
                return False if ctypes.get_last_error() == 87 else None
        try:
            if kernel.WaitForSingleObject(process, 0) == 0:
                return False
            executable = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(executable))
            image_read = kernel.QueryFullProcessImageNameW(process, 0, executable, ctypes.byref(size))
            if image_read and matches(executable.value):
                return True
            if readable:
                try:
                    for value in self.parameters(process):
                        if value is not None and matches(value):
                            return True
                except (OSError, ValueError, OverflowError):
                    pass
            # Readable process fields do not reveal all loaded Python code.
            # Only a verified exit can establish that this process is absent.
            if kernel.WaitForSingleObject(process, 0) == 0:  # WAIT_OBJECT_0
                return False
            return None
        finally:
            kernel.CloseHandle(process)

    def read(self, process, address, size):
        buffer = ctypes.create_string_buffer(size)
        actual = ctypes.c_size_t()
        if (not address or not self.kernel.ReadProcessMemory(process, address, buffer, size, ctypes.byref(actual))
                or actual.value != size):
            raise OSError('Incomplete process memory read')
        return buffer.raw

    def parameters(self, process):
        machine = wintypes.USHORT()
        native = wintypes.USHORT()
        if not self.kernel.IsWow64Process2(process, ctypes.byref(machine), ctypes.byref(native)):
            raise OSError('Cannot determine process architecture')
        width = {0x14c: 4, 0x8664: 8}.get(machine.value or native.value)
        local_width = ctypes.sizeof(ctypes.c_void_p)
        if width is None or width > local_width or native.value not in (0x14c, 0x8664):
            raise OSError('Unsupported process architecture')

        if width < local_width:
            wow_peb = ctypes.c_size_t()
            status = self.query(process, 26, ctypes.byref(wow_peb), ctypes.sizeof(wow_peb), None)
            peb = wow_peb.value
        else:
            basic = (ctypes.c_size_t * 6)()
            status = self.query(process, 0, basic, ctypes.sizeof(basic), None)
            peb = basic[1]
        if status or not peb:
            raise OSError('Cannot query process environment block')

        # Native and WOW64 PEB/RTL_USER_PROCESS_PARAMETERS layouts, respectively
        # for x64 and x86. Validate the parameter header before reading strings.
        peb_offset, cwd_offset, command_offset = (0x20, 0x38, 0x70) if width == 8 else (0x10, 0x24, 0x40)
        size = command_offset + 2 * width
        parameters = int.from_bytes(self.read(process, peb + peb_offset, width), 'little')
        header = self.read(process, parameters, size)
        maximum = int.from_bytes(header[:4], 'little')
        length = int.from_bytes(header[4:8], 'little')
        flags = int.from_bytes(header[8:12], 'little')
        if not size <= length <= maximum or not flags & 1:  # RTL_USER_PROC_PARAMS_NORMALIZED
            raise OSError('Invalid process parameters')
        for offset in (cwd_offset, command_offset):
            length = int.from_bytes(header[offset:offset + 2], 'little')
            maximum = int.from_bytes(header[offset + 2:offset + 4], 'little')
            address = int.from_bytes(header[offset + width:offset + 2 * width], 'little')
            if length % 2 or length > maximum or (offset == cwd_offset and not length):
                yield None
                continue
            try:
                yield self.read(process, address, length).decode('utf-16-le') if length else ''
            except (OSError, ValueError, OverflowError):
                yield None
