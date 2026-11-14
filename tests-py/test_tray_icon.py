"""The packaged Windows tray uses the official application icon."""
import ctypes
import os
from pathlib import Path
import tomllib
import unittest

import isolation
from haivequeen import tray


class TrayIcon(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows icon loader")
    def test_official_packaged_icon_loads_as_native_handle(self):
        path = Path(tray.__file__).with_name("app.ico")
        self.assertTrue(path.is_file(), "official icon must be available in the installed package")
        root = path.parent.parent
        self.assertEqual(path.read_bytes(), (root / "assets" / "gui" / "app.ico").read_bytes())
        package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
        self.assertIn("app.ico", package["haivequeen"])
        user = ctypes.windll.user32
        handle = tray.load_icon(user)
        self.assertTrue(handle)
        user.DestroyIcon.argtypes = [ctypes.c_void_p]
        self.assertTrue(user.DestroyIcon(handle))
