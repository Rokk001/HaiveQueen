"""Keeps the whole test session away from the real data and settings folders.

Importing this module (bzztest.py and test_00_isolation.py do) points %LOCALAPPDATA%, %APPDATA% and the XDG folders to a temp folder.
Tests that set BZZ_DATA_DIR, BZZ_CONFIG_DIR or BZZ_LOG_DIR still use their own folders; a test that unsets them, or never sets them,
falls back to the temp folder instead of the real one. test_zz_isolation_check.py turns the suite red when anything was written there,
so such a test is found and given its own folders.
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="hq-tests-appdata-"))
REDIRECTED = {"LOCALAPPDATA": ROOT / "local", "APPDATA": ROOT / "roaming", "XDG_DATA_HOME": ROOT / "local", "XDG_CONFIG_HOME": ROOT / "roaming"}
for _var, _path in REDIRECTED.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_var] = str(_path)


def leaked():
    """Return the files below the redirected folders: what a test wrote without its own BZZ_* folders."""
    return sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())
