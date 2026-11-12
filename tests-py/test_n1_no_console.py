"""N1: no console window opens anywhere in a Queen or Agent process tree (Windows), graphical windows stay."""
import subprocess
import sys
import tempfile
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
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([sys.executable, '-I', '-c', %r], check=True)" % native)
            self.assertTrue(Path(result).exists(), out)
            self.assertEqual(Path(result).read_text(), "0", out)

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
            out = run_without_console("from haivequeen import platform\nimport subprocess\n"
                                      "subprocess.run([%r, '-I', '-c', %r], creationflags=0x10, check=True)"
                                      % (str(Path(sys.executable).with_name('pythonw.exe')), gui))
            self.assertTrue(Path(result).exists(), out)
            self.assertEqual(Path(result).read_text(), "1", out)

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
