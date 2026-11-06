"""Keeps the whole test session away from the real data and settings folders.

Importing this module (bzztest.py and test_00_isolation.py do) points %LOCALAPPDATA%, %APPDATA% and the XDG folders to a temp folder.
Tests that set BZZ_DATA_DIR, BZZ_CONFIG_DIR or BZZ_LOG_DIR still use their own folders; a test that unsets them, or never sets them,
falls back to the temp folder instead of the real one. test_zz_isolation_check.py turns the suite red when anything was written there,
so such a test is found and given its own folders.
"""
import os
import tempfile
from pathlib import Path

REAL_FOLDERS = [Path(os.environ[_v]) / "HaiveQueen" for _v in ("LOCALAPPDATA", "APPDATA") if os.environ.get(_v)]
LIVE_TOP = ("haivequeen.db", "loop.lock", "exports", "runs", "STOP", "self-update.lock", "hive-schedule.lock", "start.lock", "start.pending", "gui.pid", "worktree-cleanup.lock")  # written by a running loop (STOP: set by a self-update) or an open graphical dashboard (gui.pid), not by tests


def is_live(rel: Path) -> bool:
    """True when the file (path relative to a data folder) is written by a running loop: a top-level live file, anything below logs/<project>,
    or a file a CLI writes at run time below profile/<cli> (session logs, caches, state). The settings files HaiveQueen writes into the profile
    itself (profile.SPECS) and anything directly in profile/ are not live, so a test that writes them is still found."""
    if rel.parts[0].startswith(LIVE_TOP) or (rel.parts[0] == "logs" and len(rel.parts) > 2):
        return True
    if rel.parts[0] == "profile" and len(rel.parts) > 2:
        from haivequeen import profile
        own = {name for spec in profile.SPECS.values() for name in spec["settings"]}
        return not (len(rel.parts) == 3 and rel.parts[2] in own)
    return False


def snapshot_real(bases=None) -> dict:
    """Return {path: (size, mtime_ns)} of the files in the real data and settings folders (or in `bases`), without what a running loop writes itself."""
    snap = {}
    for base in (REAL_FOLDERS if bases is None else bases):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            rel = p.relative_to(base)
            if not p.is_file() or is_live(rel):
                continue
            st = p.stat()
            snap[str(base / rel)] = (st.st_size, st.st_mtime_ns)
    return snap


REAL_BEFORE = snapshot_real()
os.environ.setdefault("BZZ_NO_UPDATE_CHECK", "1")   # no automatic update check asks GitHub during the tests (test_k10_updates.py removes this)
os.environ.setdefault("BZZ_NO_LOGIN_CHECK", "1")   # providers of the tests need no login in the profile (test_profile.py removes this)
ROOT = Path(tempfile.mkdtemp(prefix="hq-tests-appdata-"))
REDIRECTED = {"LOCALAPPDATA": ROOT / "local", "APPDATA": ROOT / "roaming", "XDG_DATA_HOME": ROOT / "local", "XDG_CONFIG_HOME": ROOT / "roaming"}
ORIGINAL = {_var: os.environ.get(_var) for _var in REDIRECTED}  # real values for read-only live-loop checks
for _var, _path in REDIRECTED.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_var] = str(_path)


def leaked():
    """Return the files below the redirected folders: what a test wrote without its own BZZ_* folders."""
    return sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file())


def real_folder_changes() -> list:
    """Return the files of the real data and settings folders that a test added, removed or changed since this module was imported."""
    now = snapshot_real()
    return sorted(p for p in set(now) | set(REAL_BEFORE) if now.get(p) != REAL_BEFORE.get(p))
