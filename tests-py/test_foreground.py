"""Tests for preserving the foreground window during terminal commands."""
import unittest
from unittest import mock

from haivequeen import platform as osplat


class ForegroundTest(unittest.TestCase):
    """Exercise foreground restoration with fake Windows APIs."""

    def api(self, foreground, title="Editor"):
        """Build fake Windows APIs and record their calls."""
        user = mock.Mock()
        kernel = mock.Mock()
        user.GetForegroundWindow.side_effect = foreground
        kernel.GetCurrentThreadId.return_value = 40
        user.GetWindowThreadProcessId.return_value = 50
        user.AttachThreadInput.return_value = 1

        def name(hwnd, buf, size):
            buf.value = "CASCADIA_HOSTING_WINDOW_CLASS" if hwnd == 2 or title.startswith("HaiveQueen") else "EditorClass"

        def text(hwnd, buf, size):
            buf.value = title if hwnd == 1 else "Terminal"

        user.GetClassNameW.side_effect = name
        user.GetWindowTextW.side_effect = text
        return user, kernel

    def test_restores_after_terminal_takes_focus(self):
        """The caller attaches input before restoring the saved window and detaches afterwards."""
        user, kernel = self.api([1, 2])
        with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(osplat.ctypes, "windll", create=True) as dll:
            dll.user32, dll.kernel32 = user, kernel
            with osplat.keep_foreground():
                user.marker()
        calls = user.mock_calls
        self.assertLess(calls.index(mock.call.AttachThreadInput(40, 50, True)), calls.index(mock.call.SetForegroundWindow(1)))
        self.assertLess(calls.index(mock.call.SetForegroundWindow(1)), calls.index(mock.call.BringWindowToTop(1)))
        self.assertLess(calls.index(mock.call.BringWindowToTop(1)), calls.index(mock.call.AttachThreadInput(40, 50, False)))

    def test_skips_empty_and_haivequeen_foreground(self):
        """A missing window or an already foreground HaiveQueen window needs no restoration."""
        for handle, title in [(0, ""), (1, "HaiveQueen | test")]:
            user, kernel = self.api([handle], title)
            with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(osplat.ctypes, "windll", create=True) as dll:
                dll.user32, dll.kernel32 = user, kernel
                with osplat.keep_foreground():
                    pass
            user.SetForegroundWindow.assert_not_called()
            user.AttachThreadInput.assert_not_called()

    def test_timeout_does_not_change_foreground(self):
        """No terminal activation within three seconds leaves the window untouched."""
        user, kernel = self.api(None)
        user.GetForegroundWindow.return_value = 1
        with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(osplat.ctypes, "windll", create=True) as dll, \
                mock.patch.object(osplat.time, "monotonic", side_effect=[0, 3]):
            dll.user32, dll.kernel32 = user, kernel
            with osplat.keep_foreground():
                pass
        user.SetForegroundWindow.assert_not_called()

    # Verify slow pane starts remain covered by the foreground guard.
    def test_slow_launch_restores_foreground(self):
        """A terminal taking focus after a slow launch still restores the editor."""
        import time
        focus = [1]
        user, kernel = self.api(None)
        user.GetForegroundWindow.side_effect = lambda: focus[0]
        with mock.patch.object(osplat, "is_windows", return_value=True), mock.patch.object(osplat.ctypes, "windll", create=True) as dll:
            dll.user32, dll.kernel32 = user, kernel
            with osplat.keep_foreground():
                time.sleep(3.2)
                focus[0] = 2
                time.sleep(0.2)
        user.SetForegroundWindow.assert_called_with(1)

    def test_non_windows_is_noop(self):
        """Other platforms run the body without accessing Windows APIs."""
        with mock.patch.object(osplat, "is_windows", return_value=False):
            with osplat.keep_foreground():
                pass

    def test_launch_wraps_process_and_pid_wait(self):
        """The terminal command and its pid wait both run inside the foreground guard."""
        import contextlib
        import tempfile
        from haivequeen.terminal import wt
        calls = []

        @contextlib.contextmanager
        def guard():
            """Record the guard around the terminal operation."""
            calls.append("enter")
            yield
            calls.append("exit")

        with tempfile.TemporaryDirectory() as folder:
            term = wt.WindowsTerminal(folder, settle=0)
            with mock.patch.object(wt.osplat, "keep_foreground", guard), \
                    mock.patch.object(wt.subprocess, "Popen", side_effect=lambda *a, **k: calls.append("launch")), \
                    mock.patch.object(term, "_wait_pids", side_effect=lambda *a: calls.append("wait") or (1, 2)):
                term.open_window("test", "test", ["command"])
        self.assertEqual(calls, ["enter", "launch", "wait", "exit"])
