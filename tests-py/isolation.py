"""Keeps the whole test session away from the real data and settings folders.

Importing this module (bzztest.py and test_00_isolation.py do) points %LOCALAPPDATA%, %APPDATA% and the XDG folders to a temp folder.
Tests that set BZZ_DATA_DIR, BZZ_CONFIG_DIR or BZZ_LOG_DIR still use their own folders; a test that unsets them, or never sets them,
falls back to the temp folder instead of the real one. test_zz_isolation_check.py turns the suite red when anything was written there,
so such a test is found and given its own folders.
"""
import os
import tempfile
import time
from pathlib import Path

os.environ["BZZ_NO_DESKTOP"] = "1"   # desktop messages of the program (faults without an Apiary) are never shown during tests

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


SESSION_START_NS = time.time_ns()
LIVE_WINDOW_S = 120   # a profile settings file written this close to a live run folder write is the live loop's (see real_folder_changes)
REAL_BEFORE = snapshot_real()
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


def own_profile_setting(rel: Path) -> bool:
    """True for a settings file HaiveQueen writes into a CLI's profile folder itself (profile/<cli>/<name of profile.SPECS>)."""
    if len(rel.parts) != 3 or rel.parts[0] != "profile":
        return False
    from haivequeen import profile
    return rel.parts[2] in {name for spec in profile.SPECS.values() for name in spec["settings"]}


def run_write_times(base: Path, since_ns: int) -> list:
    """mtimes (ns) of the files below <base>/runs written since `since_ns`: the run folders a running loop wrote during the session."""
    runs = base / "runs"
    times = []
    for p in runs.rglob("*") if runs.is_dir() else ():
        try:
            if p.is_file() and p.stat().st_mtime_ns >= since_ns:
                times.append(p.stat().st_mtime_ns)
        except OSError:
            pass
    return times


def real_folder_changes(bases=None, before=None, since_ns=None) -> list:
    """Return the files of the real data and settings folders (or of `bases`) that a test added, removed or changed since this module was imported
    (or since the snapshot `before` taken at `since_ns`).

    A settings file HaiveQueen writes into a CLI profile is also rewritten by the running loop at every agent start (profile.prepare), and Codex adds
    its own entries to it while it runs. Such a change is the live loop's when a run folder of the same data folder was written during the session
    within LIVE_WINDOW_S of the file's new time; tests never write run folders of the real data folder."""
    before = REAL_BEFORE if before is None else before
    since_ns = SESSION_START_NS if since_ns is None else since_ns
    now = snapshot_real(bases)
    changed = sorted(p for p in set(now) | set(before) if now.get(p) != before.get(p))
    found = []
    for path in changed:
        base = next((b for b in (REAL_FOLDERS if bases is None else bases) if Path(path).is_relative_to(b)), None)
        if base is not None and path in now and own_profile_setting(Path(path).relative_to(base)):
            mtime = now[path][1]
            # file times on Windows can lag the clock by a few milliseconds, so a run written right after the session start still counts
            if any(abs(t - mtime) <= LIVE_WINDOW_S * 10**9 for t in run_write_times(base, since_ns - 2 * 10**9)):
                continue
        found.append(path)
    return found
