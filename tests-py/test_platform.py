"""Tests for haivequeen.platform: stubbed branches for both systems plus real calls on the current machine."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from haivequeen import platform as osplat  # noqa: E402


class TestStandardModule(unittest.TestCase):
    def test_stdlib_platform_still_works(self):
        import platform
        self.assertTrue(hasattr(platform, "system"))
        self.assertTrue(platform.node() or True)
        self.assertIsNot(platform, osplat)


class TestPaths(unittest.TestCase):
    def test_windows_branch(self):
        env = {"APPDATA": "/r/roam", "LOCALAPPDATA": "/r/local"}
        self.assertEqual(osplat.config_dir("windows", env), Path("/r/roam/HaiveQueen"))
        self.assertEqual(osplat.data_dir("windows", env), Path("/r/local/HaiveQueen"))

    def test_windows_fallback_home(self):
        self.assertEqual(osplat.config_dir("windows", {}, "/h"), Path("/h/AppData/Roaming/HaiveQueen"))
        self.assertEqual(osplat.data_dir("windows", {}, "/h"), Path("/h/AppData/Local/HaiveQueen"))

    def test_linux_xdg(self):
        env = {"XDG_CONFIG_HOME": "/x/c", "XDG_DATA_HOME": "/x/d", "APPDATA": "/ignored"}
        self.assertEqual(osplat.config_dir("linux", env), Path("/x/c/HaiveQueen"))
        self.assertEqual(osplat.data_dir("linux", env), Path("/x/d/HaiveQueen"))

    def test_linux_defaults(self):
        self.assertEqual(osplat.config_dir("linux", {}, "/h"), Path("/h/.config/HaiveQueen"))
        self.assertEqual(osplat.data_dir("linux", {}, "/h"), Path("/h/.local/share/HaiveQueen"))

    def test_override_wins_on_both(self):
        env = {"BZZ_CONFIG_DIR": "/o/c", "BZZ_DATA_DIR": "/o/d", "APPDATA": "/a", "XDG_DATA_HOME": "/x"}
        for system in ("windows", "linux"):
            self.assertEqual(osplat.config_dir(system, env), Path("/o/c"))
            self.assertEqual(osplat.data_dir(system, env), Path("/o/d"))

    def test_subfolders(self):
        env = {"BZZ_DATA_DIR": "/o/d"}
        self.assertEqual(osplat.logs_dir(system="linux", env=env), Path("/o/d/logs"))
        self.assertEqual(osplat.exports_dir(system="linux", env=env), Path("/o/d/exports"))
        self.assertEqual(osplat.runs_dir(system="linux", env=env), Path("/o/d/runs"))

    def test_config_module_uses_platform(self):
        from haivequeen import config
        with mock.patch.dict("os.environ", {"BZZ_DATA_DIR": "dx"}):
            self.assertEqual(config.data_dir(), Path("dx"))
            self.assertEqual(config.db_path(), Path("dx") / "haivequeen.db")


class TestFlags(unittest.TestCase):
    def test_flags(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(osplat.is_linux())
            self.assertFalse(osplat.is_windows())
        with mock.patch.object(sys, "platform", "win32"):
            self.assertTrue(osplat.is_windows())
            self.assertFalse(osplat.is_linux())

    def test_helpers(self):
        self.assertTrue(osplat.host_name())
        self.assertTrue(Path(osplat.python_executable()).exists())
        self.assertGreaterEqual(osplat.logical_cores(), 1)


class TestStubbedBranches(unittest.TestCase):
    def test_free_ram_dispatch(self):
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(osplat, "_win_free_ram_mb", return_value=1234):
            self.assertEqual(osplat.free_ram_mb(), 1234)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat, "_linux_free_ram_mb", return_value=77):
            self.assertEqual(osplat.free_ram_mb(), 77)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat, "_linux_free_ram_mb", side_effect=OSError):
            self.assertEqual(osplat.free_ram_mb(), 0)

    def test_linux_meminfo_parse(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "meminfo"
            f.write_text("MemTotal:  16000000 kB\nMemFree: 1048576 kB\nMemAvailable: 2097152 kB\n")
            self.assertEqual(osplat._linux_free_ram_mb(str(f)), 2048)
            f.write_text("MemFree: 1048576 kB\n")
            self.assertEqual(osplat._linux_free_ram_mb(str(f)), 1024)

    def test_alive_dispatch_and_bad_input(self):
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(osplat, "_win_process_alive", return_value=True) as win:
            self.assertTrue(osplat.process_alive(5))
            win.assert_called_once_with(5)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat, "_posix_process_alive", return_value=False):
            self.assertFalse(osplat.process_alive(5))
        for bad in (None, "x", 0, -3):
            self.assertFalse(osplat.process_alive(bad))

    def test_posix_alive_errors(self):
        with mock.patch("os.kill", side_effect=ProcessLookupError):
            self.assertFalse(osplat._posix_process_alive(9))
        with mock.patch("os.kill", side_effect=PermissionError):
            self.assertTrue(osplat._posix_process_alive(9))
        with mock.patch("os.kill", return_value=None):
            self.assertTrue(osplat._posix_process_alive(9))

    def test_windows_kill_uses_taskkill(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run", return_value=done) as run:
            self.assertTrue(osplat.kill_process_tree(42))
            self.assertEqual(run.call_args[0][0], ["taskkill", "/PID", "42", "/T", "/F"])
        failed = subprocess.CompletedProcess([], 128, "", "")
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run", return_value=failed), \
                mock.patch.object(osplat, "process_alive", return_value=True):
            self.assertFalse(osplat.kill_process_tree(42))
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run", side_effect=OSError):
            self.assertFalse(osplat.kill_process_tree(42))

    def test_posix_kill_signals_group(self):
        import signal as sg
        sent = []
        alive = iter([True, False])  # alive after SIGTERM check once, then gone
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat.os, "getpgid", create=True, return_value=999), \
                mock.patch.object(osplat.os, "getpgrp", create=True, return_value=1), \
                mock.patch.object(osplat.os, "killpg", create=True, side_effect=lambda g, s: sent.append((g, s))), \
                mock.patch.object(osplat, "_posix_process_alive", side_effect=lambda p: next(alive, False)):
            self.assertTrue(osplat.kill_process_tree(500))
        self.assertEqual(sent[0], (999, sg.SIGTERM))

    def test_posix_kill_escalates_to_sigkill(self):
        import signal as sg
        sent = []
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat.os, "getpgid", create=True, return_value=999), \
                mock.patch.object(osplat.os, "getpgrp", create=True, return_value=1), \
                mock.patch.object(osplat.os, "killpg", create=True, side_effect=lambda g, s: sent.append(s)), \
                mock.patch.object(osplat, "_posix_process_alive", return_value=True), \
                mock.patch.object(osplat.time, "sleep"):
            osplat._posix_kill_tree(500, grace=0.0)
        self.assertEqual(sent, [sg.SIGTERM, getattr(sg, "SIGKILL", 9)])

    def test_windows_process_list_parse(self):
        out = '"System","4","Services","0","10 K"\n"Python.EXE","123","Console","1","9 K"\n"python.exe","7","Console","1","9 K"\n'
        done = subprocess.CompletedProcess([], 0, out, "")
        with mock.patch.object(sys, "platform", "win32"), mock.patch("subprocess.run", return_value=done):
            self.assertEqual(osplat.list_processes(["PYTHON.exe"]), [(123, "Python.EXE"), (7, "python.exe")])
            self.assertEqual(osplat.count_processes(["python.exe", "system"]), 3)
            self.assertEqual(osplat.list_processes([]), [])

    def test_linux_process_list_prefix(self):
        rows = [(1, "init"), (2, "averyveryverylo"), (3, "CL")]
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.object(osplat, "_linux_process_list", return_value=rows):
            self.assertEqual(osplat.list_processes(["averyveryverylongname"]), [(2, "averyveryverylo")])
            self.assertEqual(osplat.list_processes(["cl"]), [(3, "CL")])
            self.assertEqual(osplat.list_processes(None), [])


class TestRealWindowsOrLinux(unittest.TestCase):
    def test_free_ram(self):
        self.assertGreater(osplat.free_ram_mb(), 0)

    def test_own_pid_alive(self):
        import os
        self.assertTrue(osplat.process_alive(os.getpid()))
        self.assertFalse(osplat.process_alive(2 ** 22 + 12345))

    def test_kill_child_tree(self):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if osplat.is_windows() else 0
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 creationflags=flags, start_new_session=not osplat.is_windows())
        try:
            self.assertTrue(osplat.process_alive(child.pid))
            self.assertTrue(osplat.kill_process_tree(child.pid))
            child.wait(timeout=10)
            self.assertFalse(osplat.process_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_list_finds_own_python(self):
        import os
        name = Path(sys.executable).name
        pids = [p for p, _ in osplat.list_processes([name.upper()])]
        self.assertIn(os.getpid(), pids)
        self.assertGreaterEqual(osplat.count_processes([name]), 1)


class TestNetworkPath(unittest.TestCase):
    """Detection of network drives (C3): stubbed system values for both systems."""

    def test_windows_unc_and_drive_types(self):
        """A UNC path is a network path; a drive letter depends on the drive type (4 = remote)."""
        bs = chr(92)
        self.assertTrue(osplat._win_is_network(bs * 2 + "server" + bs + "share" + bs + "db"))
        self.assertTrue(osplat._win_is_network(bs * 2 + "?" + bs + "UNC" + bs + "server" + bs + "share" + bs + "db"))
        self.assertTrue(osplat._win_is_network("Z:" + bs + "data", drive_type=4))
        self.assertFalse(osplat._win_is_network("C:" + bs + "data", drive_type=3))

    def test_linux_mount_table(self):
        """The longest mount point decides; nfs/cifs are network, ext4 and the root are not."""
        mounts = chr(10).join(["/dev/sda1 / ext4 rw 0 0", "srv:/x /mnt/nas nfs4 rw 0 0", "//h/s /mnt/win cifs rw 0 0",
                               "/dev/sdb /mnt/nas/local ext4 rw 0 0"])
        self.assertTrue(osplat._linux_is_network("/mnt/nas/db", mounts))
        self.assertTrue(osplat._linux_is_network("/mnt/win/a/b", mounts))
        self.assertFalse(osplat._linux_is_network("/mnt/nas/local/db", mounts))
        self.assertFalse(osplat._linux_is_network("/home/me/db", mounts))
        self.assertFalse(osplat._linux_is_network("/mnt/nasty/db", mounts))

    def test_local_temp_folder_is_not_network(self):
        """The temp folder of the test machine is a local disk."""
        import tempfile
        self.assertFalse(osplat.is_network_path(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
