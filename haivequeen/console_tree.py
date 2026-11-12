"""Windows process-tree console policy, enforced before native creation calls.

The debugger receives descendants before their first instruction and installs
creation-call breakpoints. No window visibility or GUI startup state
is changed. This module uses only the standard library.

This is not a universal Windows policy: a native debugger can start
its own DEBUG_PROCESS chain, which Windows then routes away from this broker.
This is an accepted limitation without adding native or external dependencies.
The regression remains an expected failure so a
future fix produces an unexpected success until the marker is removed.
x86/x64 Win32 startup and the internal CreateProcessInternalW /
process-parameters layouts are the
supported interception points; this is not a security isolation boundary.
"""
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import pickle
import struct
import subprocess
import sys


class _CreateProcess(c.Structure):
    _fields_ = [('file', w.HANDLE), ('process', w.HANDLE), ('thread', w.HANDLE),
                ('base', w.LPVOID), ('offset', w.DWORD), ('size', w.DWORD),
                ('tls', w.LPVOID), ('start', w.LPVOID), ('name', w.LPVOID), ('unicode', w.WORD)]


class _Payload(c.Union):
    _fields_ = [('create', _CreateProcess), ('raw', c.c_ubyte * 160)]


class _Event(c.Structure):
    _fields_ = [('code', w.DWORD), ('pid', w.DWORD), ('tid', w.DWORD), ('data', _Payload)]


class _Context(c.Structure):
    _fields_ = ([('home', c.c_uint64 * 6), ('flags', w.DWORD), ('mxcsr', w.DWORD),
                ('segments', w.WORD * 6), ('eflags', w.DWORD)] +
               [(name, c.c_uint64) for name in
                ('dr0', 'dr1', 'dr2', 'dr3', 'dr6', 'dr7', 'rax', 'rcx', 'rdx', 'rbx',
                 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13',
                 'r14', 'r15', 'rip')] + [('rest', c.c_byte * 976)])


class _Context32(c.Structure):
    _fields_ = ([('flags', w.DWORD), ('debug', w.DWORD * 6), ('floating', c.c_byte * 112),
                 ('segments', w.DWORD * 4)] +
                [(name, w.DWORD) for name in ('edi', 'esi', 'ebx', 'edx', 'ecx', 'eax',
                                             'ebp', 'eip', 'cs', 'eflags', 'esp', 'ss')] +
                [('extended', c.c_byte * 512)])
    rip = property(lambda self: self.eip, lambda self, value: setattr(self, 'eip', value))
    rsp = property(lambda self: self.esp, lambda self, value: setattr(self, 'esp', value))
    rax = property(lambda self: self.eax, lambda self, value: setattr(self, 'eax', value))


def _context(bits):
    if bits == 32:
        ctx = _Context32()
        ctx.flags = 0x10003
        return ctx
    # CONTEXT must be aligned to 16 bytes even though ctypes gives it alignment 8.
    storage = c.create_string_buffer(c.sizeof(_Context) + 15)
    ctx = _Context.from_buffer(storage, (-c.addressof(storage)) % 16)
    ctx.flags = 0x100003  # AMD64 | CONTROL | INTEGER
    return ctx


class _Debugger:
    def __init__(self):
        self.kernel = k = c.WinDLL('kernel32', use_last_error=True)
        self.ntdll = c.WinDLL('ntdll')
        self.ntdll.NtQueryInformationProcess.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID]
        k.WaitForDebugEvent.argtypes = [c.POINTER(_Event), w.DWORD]
        k.ContinueDebugEvent.argtypes = [w.DWORD, w.DWORD, w.DWORD]
        k.ReadProcessMemory.argtypes = [w.HANDLE, w.LPVOID, w.LPVOID, c.c_size_t, c.POINTER(c.c_size_t)]
        k.WriteProcessMemory.argtypes = k.ReadProcessMemory.argtypes
        k.GetThreadContext.argtypes = [w.HANDLE, w.LPVOID]
        k.SetThreadContext.argtypes = k.GetThreadContext.argtypes
        if c.sizeof(c.c_void_p) == 8:
            k.Wow64GetThreadContext.argtypes = k.GetThreadContext.argtypes
            k.Wow64SetThreadContext.argtypes = k.GetThreadContext.argtypes
        k.SuspendThread.argtypes = [w.HANDLE]
        k.ResumeThread.argtypes = [w.HANDLE]
        k.FlushInstructionCache.argtypes = [w.HANDLE, w.LPVOID, c.c_size_t]
        k.CloseHandle.argtypes = [w.HANDLE]
        k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        k.DebugActiveProcessStop.argtypes = [w.DWORD]
        k.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
        self.processes = {}
        self.bits = {}
        self.threads = {}
        self.targets = {}
        self.initial_breaks = set()
        self.saved = {}
        self.stepping = {}

    def check(self, result):
        if not result:
            raise c.WinError(c.get_last_error())
        return result

    def read(self, process, address, size):
        buf = c.create_string_buffer(size)
        actual = c.c_size_t()
        self.check(self.kernel.ReadProcessMemory(process, address, buf, size, c.byref(actual)))
        if actual.value != size:
            raise OSError('Incomplete process memory read')
        return buf.raw

    def write(self, process, address, data):
        actual = c.c_size_t()
        self.check(self.kernel.WriteProcessMemory(process, address, data, len(data), c.byref(actual)))
        if actual.value != len(data):
            raise OSError('Incomplete process memory write')

    def instruction(self, pid, address, data):
        process = self.processes[pid]
        self.write(process, address, data)
        self.check(self.kernel.FlushInstructionCache(process, address, len(data)))

    def has_console(self, process, bits):
        basic = (c.c_size_t * 6)()
        wow = bits == 32 and c.sizeof(c.c_void_p) == 8
        status = self.ntdll.NtQueryInformationProcess(process, 26 if wow else 0, basic,
                                                    c.sizeof(c.c_size_t) if wow else c.sizeof(basic), None)
        if status:
            raise OSError('Cannot query console parent: NTSTATUS %x' % (status & 0xffffffff))
        peb = basic[0] if wow else basic[1]
        size = bits // 8
        parameters = int.from_bytes(self.read(process, peb + (0x20 if bits == 64 else 0x10), size), 'little')
        return bool(int.from_bytes(self.read(process, parameters + 0x10, size), 'little'))

    def exports(self, pid, base):
        """Read actual remote exports, including WOW64 and per-image ASLR."""
        process = self.processes[pid]
        pe = int.from_bytes(self.read(process, base + 0x3c, 4), 'little')
        offset = 0x88 if self.bits[pid] == 64 else 0x78
        rva, size = struct.unpack('<II', self.read(process, base + pe + offset, 8))
        data = self.read(process, base + rva, size)
        table = struct.unpack_from('<IIHHIIIIIII', data)
        functions = self.read(process, base + table[8], table[6] * 4)
        names = self.read(process, base + table[9], table[7] * 4)
        ordinals = self.read(process, base + table[10], table[7] * 2)
        found = {}
        for index in range(table[7]):
            name = struct.unpack_from('<I', names, index * 4)[0]
            begin = name - rva
            if begin < 0 or begin >= len(data):
                continue
            name = data[begin:data.index(0, begin)]
            if name in (b'CreateProcessInternalW', b'AllocConsole'):
                ordinal = struct.unpack_from('<H', ordinals, index * 2)[0]
                target = struct.unpack_from('<I', functions, ordinal * 4)[0]
                if rva <= target < rva + size:
                    raise OSError('Forwarded console policy export')
                found[name] = base + target
        return [found[name] for name in (b'CreateProcessInternalW', b'AllocConsole')]

    def loaded(self, pid, file, base):
        if not file:
            return
        name = c.create_unicode_buffer(32768)
        got = self.kernel.GetFinalPathNameByHandleW(file, name, len(name), 0)
        if got and name.value.lower().endswith('\\kernelbase.dll'):
            self.targets[pid] = self.exports(pid, base)
            for address in self.targets[pid]:
                self.saved[pid, address] = self.read(self.processes[pid], address, 1)
                self.instruction(pid, address, b'\xcc')

    def exception(self, event):
        code = int.from_bytes(bytes(event.data.raw[:4]), 'little')
        pid, tid = event.pid, event.tid
        bits = self.bits[pid]
        ctx = _context(bits)
        thread = self.threads[pid, tid]
        wow = bits == 32 and c.sizeof(c.c_void_p) == 8
        get_context = self.kernel.Wow64GetThreadContext if wow else self.kernel.GetThreadContext
        set_context = self.kernel.Wow64SetThreadContext if wow else self.kernel.SetThreadContext
        self.check(get_context(thread, c.byref(ctx)))
        if code in (0x80000004, 0x4000001e) and pid in self.stepping:
            owner, address, suspended = self.stepping[pid]
            if tid != owner:
                return 0x80010001
            self.stepping.pop(pid)
            self.instruction(pid, address, b'\xcc')
            ctx.eflags &= ~0x100
            self.check(set_context(thread, c.byref(ctx)))
            for handle in suspended:
                self.kernel.ResumeThread(handle)
            return 0x10002
        if code in (0x80000003, 0x4000001f) and ctx.rip - 1 not in self.targets.get(pid, ()):
            if (pid, code) not in self.initial_breaks:
                self.initial_breaks.add((pid, code))
                return 0x10002
            return 0x80010001
        if code not in (0x80000003, 0x4000001f) or pid not in self.targets:
            return 0x80010001  # let the application handle its own exceptions
        address = ctx.rip - 1
        if pid in self.stepping:
            # Another thread may already have trapped before Windows stopped
            # the process. It remains explicitly suspended until rearming; make
            # it hit the restored breakpoint again instead of losing that trap.
            ctx.rip = address
            self.check(set_context(thread, c.byref(ctx)))
            return 0x10002
        create, alloc = self.targets[pid]
        process = self.processes[pid]
        if address == create:
            # CreateProcessInternalW's seventh parameter is creation flags.
            address = ctx.rsp + (0x38 if bits == 64 else 0x1c)
            flags = int.from_bytes(self.read(process, address, 4), 'little')
            if not flags & 8 and (flags & 0x10 or not self.has_console(process, bits)):
                self.write(process, address, ((flags & ~0x10) | 0x08000000).to_bytes(4, 'little'))
            suspended = []
            for (other_pid, other_tid), handle in self.threads.items():
                if other_pid == pid and other_tid != tid:
                    if self.kernel.SuspendThread(handle) == 0xffffffff:
                        raise c.WinError(c.get_last_error())
                    suspended.append(handle)
            self.stepping[pid] = (tid, create, suspended)
            self.instruction(pid, create, self.saved[pid, create])
            ctx.rip = create
            ctx.eflags |= 0x100  # execute the original instruction, then rearm
        elif address == alloc:
            # AllocConsole is an explicit late allocation; return FALSE.
            size = bits // 8
            ctx.rip = int.from_bytes(self.read(process, ctx.rsp, size), 'little')
            ctx.rsp += size
            ctx.rax = 0
        else:
            return 0x80010001
        self.check(set_context(thread, c.byref(ctx)))
        return 0x10002

    def run(self):
        k = self.kernel
        while True:
            event = _Event()
            self.check(k.WaitForDebugEvent(c.byref(event), 0xffffffff))
            code, pid, tid = event.code, event.pid, event.tid
            status = 0x10002
            info = event.data.create
            if code == 3:
                self.processes[pid] = info.process
                pe = int.from_bytes(self.read(info.process, info.base + 0x3c, 4), 'little')
                machine = int.from_bytes(self.read(info.process, info.base + pe + 4, 2), 'little')
                if machine not in (0x14c, 0x8664):
                    raise OSError('Unsupported console policy machine: %x' % machine)
                self.bits[pid] = 64 if machine == 0x8664 else 32
                self.threads[pid, tid] = info.thread
                if info.file:
                    k.CloseHandle(info.file)
            elif code == 2:
                self.threads[pid, tid] = info.file  # first member is hThread
            elif code == 4:
                self.threads.pop((pid, tid), None)
            elif code == 5:
                self.processes.pop(pid, None)
                self.targets.pop(pid, None)
                self.bits.pop(pid, None)
                self.initial_breaks = {key for key in self.initial_breaks if key[0] != pid}
                self.saved = {key: value for key, value in self.saved.items() if key[0] != pid}
                self.stepping.pop(pid, None)
                for key in list(self.threads):
                    if key[0] == pid:
                        self.threads.pop(key)
            elif code == 6:
                try:
                    self.loaded(pid, info.file, info.process)  # LOAD_DLL: hFile, lpBaseOfDll
                finally:
                    if info.file:
                        k.CloseHandle(info.file)
            elif code == 1:
                status = self.exception(event)
            self.check(k.ContinueDebugEvent(pid, tid, status))
            if not self.processes:
                return

    def abort(self):
        """An enforcement error must not leave a suspended or unprotected tool."""
        for pid, process in list(self.processes.items()):
            self.kernel.TerminateProcess(process, 127)
            self.kernel.DebugActiveProcessStop(pid)


def _receive(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise OSError('Console supervisor ended before responding')
    size = struct.unpack('<I', header)[0]
    payload = stream.read(size)
    if len(payload) != size:
        raise OSError('Incomplete console supervisor response')
    return pickle.loads(payload)


def _send(stream, value):
    payload = pickle.dumps(value)
    stream.write(struct.pack('<I', len(payload)))
    stream.write(payload)
    stream.flush()


def create_process(original, args):
    """An independent supervisor keeps detached trees protected after caller exit.

    Only inherited anonymous pipes carry the request and reply. Process/thread
    handles are duplicated back to the caller, so Popen still owns the actual
    command's PID, exit status, termination and redirected streams.
    """
    import _winapi
    import msvcrt
    request_read, request_write = _winapi.CreatePipe(None, 0)
    reply_read, reply_write = _winapi.CreatePipe(None, 0)
    owned = {request_read, request_write, reply_read, reply_write}
    try:
        os.set_handle_inheritable(request_read, True)
        os.set_handle_inheritable(reply_write, True)
        startup = subprocess.STARTUPINFO()
        attributes = getattr(args[8], 'lpAttributeList', None)
        if not args[4] or attributes and 'handle_list' in attributes:
            inherited = list(attributes.get('handle_list', ())) if args[4] and attributes else []
            startup.lpAttributeList = {'handle_list': list(set(inherited + [request_read, reply_write]))}
        executable = Path(sys.executable).with_name('pythonw.exe')
        if not executable.exists():
            executable = Path(sys.executable)
        command = subprocess.list2cmdline([str(executable), '-I', str(Path(__file__).resolve()),
                                           str(request_read), str(reply_write), str(os.getpid())])
        broker, thread, _pid, _tid = original(None, command, None, None, True, 0x08000208,
                                             None, None, startup)
        _winapi.CloseHandle(thread)
        _winapi.CloseHandle(broker)
        for handle in (request_read, reply_write):
            _winapi.CloseHandle(handle)
            owned.remove(handle)
        request_fd = msvcrt.open_osfhandle(request_write, os.O_WRONLY | os.O_BINARY)
        owned.remove(request_write)
        with os.fdopen(request_fd, 'wb') as stream:
            _send(stream, args)
        reply_fd = msvcrt.open_osfhandle(reply_read, os.O_RDONLY | os.O_BINARY)
        owned.remove(reply_read)
        with os.fdopen(reply_fd, 'rb') as stream:
            value = _receive(stream)
        if isinstance(value, BaseException):
            raise value
        return value
    finally:
        for handle in owned:
            _winapi.CloseHandle(handle)


def _supervise(request, reply, parent_pid):
    import _winapi
    import msvcrt
    # Do not let the actual tool inherit the private supervisor protocol.
    os.set_handle_inheritable(request, False)
    os.set_handle_inheritable(reply, False)
    debugger = _Debugger()
    reply_fd = msvcrt.open_osfhandle(reply, os.O_WRONLY | os.O_BINARY)
    with os.fdopen(reply_fd, 'wb') as output:
        try:
            request_fd = msvcrt.open_osfhandle(request, os.O_RDONLY | os.O_BINARY)
            with os.fdopen(request_fd, 'rb') as source:
                args = list(_receive(source))
            args[5] |= 1  # DEBUG_PROCESS receives every native descendant
            process, thread, pid, tid = _winapi.CreateProcess(*args)
            # The tool owns these inherited handles now. Keeping pipe writers
            # here would make communicate() wait for unrelated detached leaves.
            if args[4]:
                info = args[8]
                attributes = getattr(info, 'lpAttributeList', None) or {}
                inherited = set(attributes.get('handle_list', ()))
                inherited.update(getattr(info, name, None) for name in ('hStdInput', 'hStdOutput', 'hStdError'))
                for handle in inherited - {None, 0, -1, request, reply}:
                    try:
                        _winapi.CloseHandle(handle)
                    except OSError:
                        pass
            parent = _winapi.OpenProcess(0x40, False, parent_pid)  # PROCESS_DUP_HANDLE
            try:
                current = _winapi.GetCurrentProcess()
                transferred = tuple(_winapi.DuplicateHandle(current, h, parent, 0, False, 2)
                                    for h in (process, thread))
            finally:
                _winapi.CloseHandle(parent)
                _winapi.CloseHandle(process)
                _winapi.CloseHandle(thread)
            _send(output, (*transferred, pid, tid))
        except BaseException as exc:
            _send(output, exc)
            return
    try:
        debugger.run()
    except BaseException:
        # There is no parent stderr in pythonw; fail closed with exit status 127
        # on tools that cannot be supervised, rather than silently leaking a UI.
        debugger.abort()
        raise


if __name__ == '__main__':
    _supervise(*(int(value) for value in sys.argv[1:]))
