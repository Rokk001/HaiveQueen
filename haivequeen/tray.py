"""Windows notification-area controls for the local service and orchestrator."""
import os
import subprocess
import threading
from contextlib import closing
from pathlib import Path

from . import config, db, force_stop, loop, web_service
from . import platform as osplat


def menu(state, web_running, work_running=False):
    return [(1, 'Open web interface', True),
            (2, 'Start', state == 'Stopped'),
            (3, 'Stop gracefully', state == 'Running'),
            (4, 'Force stop', state != 'Stopped' or work_running),
            (5, 'Start', not web_running),
            (6, 'Stop', web_running),
            (7, 'Restart', web_running)]


def work_running():
    with closing(db.connect(config.db_path())) as conn:
        return force_stop.has_work(conn)


def state():
    pid = loop.loop_running()
    return 'Stopping' if pid and loop.stop_flag().exists() else 'Running' if pid else 'Stopped'


def run_command(args):
    executable = osplat.python_executable()
    cli = str(Path(__file__).with_name('cli.py'))
    subprocess.Popen([executable, cli] + args, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def confirm():
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, force_stop.WARNING, 'HaiveQueen', 0x24 | 0x10000) == 6


def invoke(number):
    if number == 1:
        web_service.open_browser()
    elif number == 4:
        if confirm():
            run_command(['stop', '--force'])
    else:
        commands = {2: ['start'], 3: ['stop'], 5: ['web', 'start'],
                    6: ['web', 'stop'], 7: ['web', 'restart']}
        if number in commands:
            run_command(commands[number])


def ensure_started():
    if os.name != 'nt' or os.environ.get('BZZ_NO_DESKTOP'):
        return
    try:
        process = subprocess.Popen([osplat.python_executable(), '-m', 'haivequeen.tray'],
                                   cwd=str(Path(__file__).resolve().parent.parent),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    except OSError:
        return
    threading.Thread(target=process.wait, daemon=True).start()


def main():
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes as w
    user, shell, kernel = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    mutex = kernel.CreateMutexW(None, False, 'Local\\HaiveQueenTray')
    if kernel.GetLastError() == 183:
        kernel.CloseHandle(mutex)
        return
    # All Win32 handles and callback results must remain pointer-sized on 64-bit Windows.
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    class WNDCLASS(ctypes.Structure):
        _fields_ = [('style', w.UINT), ('proc', WNDPROC), ('extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                    ('instance', w.HINSTANCE), ('icon', w.HICON), ('cursor', w.HANDLE), ('background', w.HANDLE),
                    ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]
    class NID(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('window', w.HWND), ('id', w.UINT), ('flags', w.UINT),
                    ('callback', w.UINT), ('icon', w.HICON), ('tip', w.WCHAR * 128),
                    ('state', w.DWORD), ('state_mask', w.DWORD), ('info', w.WCHAR * 256),
                    ('version', w.UINT), ('title', w.WCHAR * 64), ('info_flags', w.DWORD),
                    ('guid', ctypes.c_byte * 16), ('balloon_icon', w.HICON)]
    for fn in ('CreateWindowExW', 'LoadIconW', 'CreatePopupMenu'):
        getattr(user, fn).restype = w.HANDLE
    kernel.GetModuleHandleW.restype = w.HMODULE
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
    user.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, ctypes.c_void_p]
    user.DestroyMenu.argtypes = [w.HMENU]
    user.SetForegroundWindow.argtypes = [w.HWND]
    user.DestroyWindow.argtypes = [w.HWND]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    user.DefWindowProcW.restype = LRESULT
    user.TrackPopupMenu.restype = w.UINT
    current = {'state': '', 'web': False, 'work': False}
    icon = NID()
    icon.size = ctypes.sizeof(NID)
    icon.id = 1
    icon.flags = 1 | 2 | 4
    icon.callback = 0x8001
    icon.icon = user.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), w.LPCWSTR))

    def refresh(hwnd):
        try:
            current['state'] = state()
            status = web_service.control('status')
            current['web'] = bool(status and status.get('running'))
            current['work'] = current['state'] == 'Stopped' and work_running()
        except (OSError, ValueError, KeyError):
            return
        icon.tip = 'HaiveQueen - ' + current['state']
        shell.Shell_NotifyIconW(1, ctypes.byref(icon))
        if current['state'] == 'Stopped' and not current['web']:
            user.PostMessageW(hwnd, 0x10, 0, 0)

    @WNDPROC
    def window(hwnd, msg, wp, lp):
        if msg == 0x113:
            refresh(hwnd)
            return 0
        if msg == 0x8001 and lp in (0x205, 0x202):
            popup = user.CreatePopupMenu()
            entries = menu(current['state'], current['web'], current['work'])
            for index, (number, label, enabled) in enumerate(entries):
                if index in (1, 4):
                    user.AppendMenuW(popup, 0x800, 0, None)
                    user.AppendMenuW(popup, 0x1, 0, 'Orchestrator' if index == 1 else 'Web server')
                user.AppendMenuW(popup, 0 if enabled else 0x1, number, label)
            point = w.POINT()
            user.GetCursorPos(ctypes.byref(point))
            user.SetForegroundWindow(hwnd)
            choice = user.TrackPopupMenu(popup, 0x100 | 0x2, point.x, point.y, 0, hwnd, None)
            user.DestroyMenu(popup)
            if choice:
                threading.Thread(target=invoke, args=(choice,), daemon=True).start()
            return 0
        if msg == 0x10:
            user.DestroyWindow(hwnd)
            return 0
        if msg == 0x2:
            shell.Shell_NotifyIconW(2, ctypes.byref(icon))
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, msg, wp, lp)

    instance = kernel.GetModuleHandleW(None)
    cls = WNDCLASS(0, window, 0, 0, instance, None, None, None, None, 'HaiveQueenTrayWindow')
    user.RegisterClassW(ctypes.byref(cls))
    hwnd = user.CreateWindowExW(0, cls.name, 'HaiveQueen', 0, 0, 0, 0, 0, None, None, instance, None)
    if hwnd:
        icon.window = hwnd
        icon.tip = 'HaiveQueen - ' + state()
        shell.Shell_NotifyIconW(0, ctypes.byref(icon))
        refresh(hwnd)
        user.SetTimer(hwnd, 1, 2000, None)
        message = w.MSG()
        while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
    kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
