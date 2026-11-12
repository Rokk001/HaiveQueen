"""N1: hide Windows process-tree consoles and preserve graphical windows.

Accepted limitation: native tools starting their own
DEBUG_PROCESS chains take debug-event ownership away from the supervisor.
The native-debugger regression stays executable as an expected failure.
"""
import subprocess
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETACHED_PROCESS, NEW_GROUP, NO_WINDOW = 0x00000008, 0x00000200, 0x08000000

# Prints whether this process owns a console window (0 = none).
PROBE = "import ctypes;print('CONSOLE=%d'%(ctypes.windll.kernel32.GetConsoleWindow() or 0))"
GRAND = "import subprocess,sys;subprocess.run([sys.executable,'-c',%r])" % PROBE


def run_without_console(code, timeout=90):
    """Run `code` in a Python process that has no console at all (like the GUI or a detached runner); return what it printed."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        script = Path(tmp) / "driver.py"
        head = "import sys\nsys.path.insert(0, %r)\nsys.stdout = open(%r, 'w', buffering=1)\n" % (str(ROOT), str(out))
        head += "sys.stderr = sys.stdout\n"
        script.write_text(head + code + "\n", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, "-I", str(script)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=DETACHED_PROCESS | NEW_GROUP)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        output = out.read_text(encoding="utf-8") if out.exists() else ""
        if proc.returncode:
            raise AssertionError("Driver exited %d:\n%s" % (proc.returncode, output))
        return output


@unittest.skipUnless(sys.platform == "win32", "Windows consoles")
class NoConsoleTest(unittest.TestCase):

    def test_late_native_console_allocation_remains_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / 'allocated.txt')
            child = ("import ctypes as c;k=c.WinDLL('kernel32');u=c.WinDLL('user32');"
                     "k.GetConsoleWindow.restype=c.c_void_p;u.IsWindowVisible.argtypes=[c.c_void_p];"
                     "k.FreeConsole();k.AllocConsole();"
                     "open(%r,'w').write(str(int(bool(u.IsWindowVisible(k.GetConsoleWindow())))))" % result)
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([sys.executable, '-I', '-c', %r], check=True)" % child)
            self.assertEqual(Path(result).read_text(), '0', out)

    def test_detached_parent_default_descendant_remains_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / 'detached.txt')
            probe = "import ctypes;open(%r,'w').write(str(ctypes.windll.kernel32.GetConsoleWindow()))" % result
            child = "import subprocess,sys;subprocess.run([sys.executable,'-I','-c',%r],check=True)" % probe
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([sys.executable, '-I', '-c', %r], creationflags=8, check=True)" % child)
            self.assertEqual(Path(result).read_text(), '0', out)

    def test_concurrent_native_descendants_remain_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = [str(Path(tmp) / ('thread%d.txt' % i)) for i in range(12)]
            probes = ["import ctypes;open(%r,'w').write(str(ctypes.windll.kernel32.GetConsoleWindow()))" % p
                      for p in results]
            child = ("import concurrent.futures,subprocess,sys,threading\n"
                     "barrier=threading.Barrier(6)\n"
                     "def launch(probe):\n"
                     "    barrier.wait()\n"
                     "    subprocess.run([sys.executable,'-I','-c',probe],creationflags=0x10,check=True)\n"
                     "with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:\n"
                     "    list(pool.map(launch,%r))\n" % probes)
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([sys.executable,'-I','-c',%r],check=True)" % child)
            for result in results:
                self.assertEqual(Path(result).read_text(), '0', out)

    def test_wow64_native_descendant_remains_hidden(self):
        cmd = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'SysWOW64' / 'cmd.exe'
        if not cmd.exists():
            self.skipTest('WOW64 command processor is unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / 'wow64.txt')
            script = Path(tmp) / 'probe.py'
            script.write_text("import ctypes;open(%r,'w').write(str(ctypes.windll.kernel32.GetConsoleWindow()))" % result,
                              encoding='utf-8')
            command = 'start "" /wait "%s" "%s"' % (sys.executable, script)
            command = subprocess.list2cmdline([str(cmd)]) + ' /d /c ' + command
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run(%r,check=True)" % command)
            self.assertTrue(Path(result).exists(), out)
            self.assertEqual(Path(result).read_text(), '0', out)

    def test_detached_tree_stays_protected_after_launcher_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / 'release'
            result = Path(tmp) / 'survived.txt'
            probe = "import ctypes;open(%r,'w').write(str(ctypes.windll.kernel32.GetConsoleWindow()))" % str(result)
            child = ("import pathlib,subprocess,sys,time\n"
                     "deadline=time.monotonic()+10\n"
                     "while not pathlib.Path(%r).exists() and time.monotonic()<deadline:\n"
                     "    time.sleep(0.02)\n"
                     "subprocess.run([sys.executable,'-I','-c',%r],creationflags=0x10,check=True)\n" % (str(release), probe))
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "p=subprocess.Popen([sys.executable,'-I','-c',%r],creationflags=8,"
                                      "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                                      "print(p.pid)" % child)
            release.touch()
            deadline = time.monotonic() + 15
            while not result.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(result.exists(), out)
            self.assertEqual(result.read_text(), '0', out)

    def test_supervisor_does_not_keep_finished_commands_pipes_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = str(Path(tmp) / 'release')
            leaf = ("import pathlib,time\ndeadline=time.monotonic()+10\n"
                    "while not pathlib.Path(%r).exists() and time.monotonic()<deadline:\n"
                    "    time.sleep(0.02)\n" % release)
            child = ("import subprocess,sys\n"
                     "subprocess.Popen([sys.executable,'-I','-c',%r],creationflags=8,"
                     "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                     "print('finished')\n" % leaf)
            out = run_without_console("from haivequeen import platform\nimport subprocess,pathlib\n"
                                      "try:\n"
                                      "    r=subprocess.run([sys.executable,'-I','-c',%r],capture_output=True,text=True,timeout=3,check=True)\n"
                                      "    print(r.stdout)\n"
                                      "finally:\n"
                                      "    pathlib.Path(%r).touch()\n" % (child, release))
            self.assertIn('finished', out)

    @unittest.expectedFailure
    def test_native_debugger_descendant_remains_hidden(self):
        """Accepted limitation: Windows routes a tool's debug chain away from us."""
        self._debugger_descendant_probe()

    def test_existing_debugger_keeps_native_descendants_hidden(self):
        """An already-debugged caller can launch an independent console supervisor."""
        for debug_children in (False, True):
            with self.subTest(debug_children=debug_children):
                self._debugger_descendant_probe(import_policy=True, debug_children=debug_children)

    def _debugger_descendant_probe(self, import_policy=False, debug_children=False):
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / 'debugged.txt')
            probe = ("import ctypes as c;k=c.WinDLL('kernel32');u=c.WinDLL('user32');"
                     "k.GetConsoleWindow.restype=c.c_void_p;u.IsWindowVisible.argtypes=[c.c_void_p];"
                     "open(%r,'w').write(str(int(bool(u.IsWindowVisible(k.GetConsoleWindow())))))" % result)
            child = "import subprocess,sys;subprocess.run([sys.executable,'-I','-c',%r],creationflags=0x10,check=True)" % probe
            if import_policy:
                child = ("import ctypes,sys;assert ctypes.windll.kernel32.IsDebuggerPresent();"
                         "sys.path.insert(0,%r);from haivequeen import platform;import subprocess;"
                         "subprocess.run([sys.executable,'-I','-c',%r],check=True)" % (str(ROOT), child))
            native = '''import _winapi,ctypes as c,subprocess,sys
from ctypes import wintypes as w
class U(c.Union):
    _fields_=[('pointer',w.HANDLE),('raw',c.c_ubyte*160)]
class E(c.Structure):
    _fields_=[('code',w.DWORD),('pid',w.DWORD),('tid',w.DWORD),('data',U)]
k=c.WinDLL('kernel32',use_last_error=True)
k.WaitForDebugEvent.argtypes=[c.POINTER(E),w.DWORD]
k.ContinueDebugEvent.argtypes=[w.DWORD,w.DWORD,w.DWORD]
k.CloseHandle.argtypes=[w.HANDLE]
p,t,pid,tid=_winapi.CreateProcess(None,%r,None,None,False,%r,None,None,subprocess.STARTUPINFO())
count=0
try:
    while True:
        e=E()
        if not k.WaitForDebugEvent(c.byref(e),30000):
            raise c.WinError(c.get_last_error())
        status=0x10002
        if e.code==3:
            count+=1
        elif e.code==5:
            count-=1
        elif e.code==1:
            code=int.from_bytes(bytes(e.data.raw[:4]),'little')
            if code not in (0x80000003,0x4000001f):
                status=0x80010001
        if e.code in (3,6) and e.data.pointer:
            k.CloseHandle(e.data.pointer)
        if not k.ContinueDebugEvent(e.pid,e.tid,status):
            raise c.WinError(c.get_last_error())
        if count==0:
            break
finally:
    _winapi.CloseHandle(p)
    _winapi.CloseHandle(t)
''' % (subprocess.list2cmdline([sys.executable, '-I', '-c', child]),
       0x08000003 if import_policy and not debug_children else 0x08000001)
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([sys.executable,'-I','-c',%r],check=True)" % native)
            self.assertTrue(Path(result).exists(), out)
            self.assertEqual(Path(result).read_text(), '0', out)

    def test_agent_cleanup_preserves_exempt_commands_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_without_console("import os\nos.environ['BZZ_DATA_DIR']=%r\n"
                                      "from haivequeen import platform,proctree\nimport subprocess\n"
                                      "p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'],"
                                      "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                                      "try:\n"
                                      "    proctree.exempt_register(p.pid)\n"
                                      "    children=proctree.descendants(os.getpid(),proctree.snapshot())\n"
                                      "    recorded={pid:{'ppid':parent,'identity':platform.process_identity(pid)} for pid,parent in children.items()}\n"
                                      "    proctree.kill_recorded(recorded)\n"
                                      "    import time;time.sleep(0.1)\n"
                                      "    assert p.poll() is None, 'exempt command was killed through its supervisor'\n"
                                      "    print('EXEMPT_OK')\n"
                                      "finally:\n"
                                      "    proctree.exempt_clear(p.pid)\n"
                                      "    p.terminate()\n"
                                      "    p.wait(timeout=5)\n" % tmp)
            self.assertIn('EXEMPT_OK', out)

    def test_native_createprocess_descendant_remains_hidden(self):
        """An unmodified tool can call Win32 directly, bypassing Python's Popen."""
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / "native-visible.txt")
            probe = ("import ctypes; k=ctypes.WinDLL('kernel32'); u=ctypes.WinDLL('user32'); "
                     "k.GetConsoleWindow.restype=ctypes.c_void_p; "
                     "u.IsWindowVisible.argtypes=[ctypes.c_void_p]; "
                     "open(%r,'w').write(str(int(bool(u.IsWindowVisible(k.GetConsoleWindow())))))" % result)
            command = subprocess.list2cmdline([sys.executable, "-I", "-c", probe])
            native = '''import ctypes
from ctypes import wintypes as w
class SI(ctypes.Structure):
    _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR),
               ('title', w.LPWSTR), ('x', w.DWORD), ('y', w.DWORD),
               ('xs', w.DWORD), ('ys', w.DWORD), ('xc', w.DWORD),
               ('yc', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
               ('show', w.WORD), ('size', w.WORD), ('reserved2', ctypes.c_void_p),
               ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]
class PI(ctypes.Structure):
    _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                            w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                            ctypes.POINTER(SI), ctypes.POINTER(PI)]
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.CloseHandle.argtypes = [w.HANDLE]
si, pi = SI(), PI()
si.cb = ctypes.sizeof(si)
if not k.CreateProcessW(None, ctypes.create_unicode_buffer(%r), None, None,
                        False, 0x10, None, None, ctypes.byref(si), ctypes.byref(pi)):
    raise ctypes.WinError(ctypes.get_last_error())
try:
    assert k.WaitForSingleObject(pi.process, 30000) == 0
finally:
    k.CloseHandle(pi.thread)
    k.CloseHandle(pi.process)
''' % command
            script = Path(tmp) / 'native.py'
            script.write_text(native, encoding='utf-8')
            argv = [sys.executable, '-I', str(script)]
            routes = {
                'compile': "from haivequeen import integration\nprint(integration._watch_compile(%r,'.',60,120,0.1))" % subprocess.list2cmdline(argv),
                'build': "from haivequeen import platform\nprint(platform.run_with_timeout(%r,60))" % argv,
                'agent': "from haivequeen import agent_runner\nimport subprocess\nsubprocess.run(%r,check=True)" % argv,
            }
            for route, code in routes.items():
                with self.subTest(route=route):
                    Path(result).unlink(missing_ok=True)
                    out = run_without_console(code)
                    self.assertTrue(Path(result).exists(), out)
                    self.assertEqual(Path(result).read_text(), '0', out)

    def test_real_gui_window_remains_visible(self):
        """A GUI executable must actually show a window, including its first ShowWindow call."""
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / "gui-visible.txt")
            gui = '''import ctypes
u = ctypes.WinDLL('user32', use_last_error=True)
u.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p,
    ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
u.CreateWindowExW.restype = ctypes.c_void_p
u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
u.IsWindowVisible.argtypes = [ctypes.c_void_p]
u.DestroyWindow.argtypes = [ctypes.c_void_p]
h = u.CreateWindowExW(0, 'STATIC', 'N1 GUI visibility test', 0x00cf0000,
                      0, 0, 160, 80, None, None, None, None)
if not h:
    raise ctypes.WinError(ctypes.get_last_error())
try:
    u.ShowWindow(h, 1)
    open(%r, 'w').write(str(int(bool(u.IsWindowVisible(h)))))
finally:
    u.DestroyWindow(h)
''' % result
            gui_command = ("import subprocess\nsubprocess.run([%r, '-I', '-c', %r],creationflags=0x10,check=True)"
                           % (str(Path(sys.executable).with_name('pythonw.exe')), gui))
            for descendant in (False, True):
                with self.subTest(descendant=descendant):
                    Path(result).unlink(missing_ok=True)
                    launch = ("subprocess.run([sys.executable,'-I','-c',%r],check=True)" % gui_command
                              if descendant else gui_command)
                    out = run_without_console("from haivequeen import platform\nimport subprocess\n" + launch)
                    self.assertTrue(Path(result).exists(), out)
                    self.assertEqual(Path(result).read_text(), '1', out)

    def test_direct_new_console_request_has_no_window(self):
        out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                  "r = subprocess.run([sys.executable, '-c', %r], creationflags=0x210, capture_output=True, text=True, check=True)\n"
                                  "print(r.stdout)" % PROBE)
        self.assertIn("CONSOLE=0", out)

    def test_descendant_requesting_new_console_remains_hidden(self):
        """A tool requesting a fresh console must not expose it to the user."""
        with tempfile.TemporaryDirectory() as tmp:
            result = str(Path(tmp) / "visible.txt")
            probe = ("import ctypes; h=ctypes.windll.kernel32.GetConsoleWindow(); "
                     "open(%r, 'w').write(str(int(bool(h and ctypes.windll.user32.IsWindowVisible(h)))))" % result)
            child = ("import subprocess,sys; "
                     "subprocess.run([sys.executable, '-c', %r], creationflags=0x10, check=True)" % probe)
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "r = subprocess.run([sys.executable, '-c', %r], capture_output=True, text=True)\n"
                                      "print(r.returncode, r.stderr)" % child)
            self.assertTrue(Path(result).exists(), out)
            self.assertEqual(Path(result).read_text(), "0", out)

    def test_compile_check_grandchild_has_no_console(self):
        """The compile check command, through cmd.exe, a child and a grandchild, opens no console window from a console-less parent."""
        with tempfile.TemporaryDirectory() as tmp:
            grand = Path(tmp) / "grand.py"
            grand.write_text(GRAND + "\n", encoding="utf-8")
            command = '"%s" "%s"' % (sys.executable, grand)
            out = run_without_console("from haivequeen import integration\nprint(integration._watch_compile(%r, '.', 60, 120, 0.1))" % command)
        self.assertIn("CONSOLE=0", out)

    def test_build_tool_grandchild_has_no_console(self):
        """A program started through platform.run_with_timeout (build commands) and its child get no console window."""
        out = run_without_console("from haivequeen import platform as p\ncode, o, e, t = p.run_with_timeout([sys.executable, '-c', %r], 60)\nprint(o.decode())" % GRAND)
        self.assertIn("CONSOLE=0", out)

    def test_agent_command_grandchild_has_no_console(self):
        """A plain subprocess call of the agent runner and what that program starts below it get no console window."""
        out = run_without_console("from haivequeen import agent_runner\nimport subprocess\n"
                                  "r = subprocess.run([sys.executable, '-c', %r], capture_output=True, text=True)\nprint(r.stdout)" % GRAND)
        self.assertIn("CONSOLE=0", out)

    def test_positional_creationflags_get_no_window(self):
        """A Popen call that passes creationflags as a positional argument still gets CREATE_NO_WINDOW."""
        out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                  "args = [[sys.executable, '-c', %r], -1, None, None, subprocess.PIPE, None, None, True, False, None, None, False, None, 0x200]\n"
                                  "r = subprocess.Popen(*args)\nprint(r.communicate()[0].decode())" % PROBE)
        self.assertIn("CONSOLE=0", out)

    def test_hook_process_hides_its_git_children(self):
        """The branch guard hook, started by git as a plain script, hides the console of the programs it starts."""
        hook = ROOT / "haivequeen" / "branchguard.py"
        with tempfile.TemporaryDirectory() as tmp:
            check = Path(tmp) / "check.py"
            check.write_text("import runpy, subprocess, sys\n"
                             "seen = []\n"
                             "real = subprocess.Popen.__init__\n"
                             "def spy(self, *a, **k):\n"
                             "    seen.append(k.get('creationflags', 0))\n"
                             "    real(self, *a, **k)\n"
                             "subprocess.Popen.__init__ = spy\n"
                             "sys.argv = [%r, %r, 'pre-commit']\n"
                             "try:\n"
                             "    runpy.run_path(%r, run_name='__main__')\n"
                             "except BaseException as e:\n"
                             "    import traceback; traceback.print_exc(file=sys.stdout)\n"
                             "print('FLAGS', ' '.join(hex(f) for f in seen))\n" % (str(hook), tmp, str(hook)), encoding="utf-8")
            (Path(tmp) / "policy.json").write_text('{"branch": "x", "original": "%s"}' % tmp.replace("\\", "/"), encoding="utf-8")
            done = subprocess.run([sys.executable, "-I", str(check)], capture_output=True, text=True, cwd=tmp, creationflags=DETACHED_PROCESS)
        self.assertIn("FLAGS", done.stdout, done.stderr)
        flags = [int(f, 16) for f in done.stdout.split("FLAGS", 1)[1].split()]
        self.assertTrue(flags, done.stdout + done.stderr)
        for flag in flags:
            self.assertNotEqual(flag & NO_WINDOW, 0, done.stdout)

    def test_graphical_windows_are_preserved(self):
        """Only console creation flags change; GUI startup visibility is untouched."""
        out = run_without_console("from haivequeen import platform\nimport subprocess, _winapi\n"
                                  "seen = []\n"
                                  "orig = _winapi.CreateProcess\n"
                                  "def spy(*a):\n"
                                  "    seen.append((a[5], a[8]))\n"
                                  "    return orig(*a)\n"
                                  "_winapi.CreateProcess = spy\n"
                                  "subprocess.run([sys.executable, '-c', 'pass'], creationflags=0x10)\n"
                                  "subprocess.run([sys.executable, '-c', 'pass'], creationflags=0x8)\n"
                                  "subprocess.run([sys.executable, '-c', 'pass'])\n"
                                  "for flags, info in seen:\n"
                                  "    print('FLAGS', hex(flags), bool(getattr(info, 'dwFlags', 0) & 1))\n")
        lines = [line.split() for line in out.splitlines() if line.startswith("FLAGS")]
        self.assertEqual(len(lines), 3, out)
        self.assertNotEqual(int(lines[0][1], 16) & NO_WINDOW, 0)
        self.assertEqual(int(lines[0][1], 16) & 0x10, 0)
        self.assertEqual(int(lines[1][1], 16) & NO_WINDOW, 0)   # DETACHED_PROCESS
        self.assertNotEqual(int(lines[2][1], 16) & NO_WINDOW, 0)
        for line in lines:
            self.assertEqual(line[2], "False")   # no STARTF_USESHOWWINDOW: a graphical window is never forced hidden


if __name__ == "__main__":
    unittest.main()
