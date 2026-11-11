"""Keeps the whole test session away from the real data and settings folders.

Importing this module (bzztest.py and test_00_isolation.py do) points %LOCALAPPDATA%, %APPDATA% and the XDG folders to a temp folder.
Tests that set BZZ_DATA_DIR, BZZ_CONFIG_DIR or BZZ_LOG_DIR still use their own folders; a test that unsets them, or never sets them,
falls back to the temp folder instead of the real one. test_zz_isolation_check.py turns the suite red when anything was written there,
so such a test is found and given its own folders.

The real folders are also guarded directly: an audit hook records every write, removal, rename or database connection of the test process
inside them, and every child process whose environment would make it use them (guard_hits). This does not depend on file times, so a live
loop that writes the same files at the same moment neither hides a test's write nor is taken for one.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)
if REPO not in sys.path:   # haivequeen of this checkout, not an installed copy, even when this module is imported first (test_00_isolation.py)
    sys.path.insert(0, REPO)
os.environ["BZZ_NO_DESKTOP"] = "1"   # desktop messages of the program (faults without an Apiary) are never shown during tests

REAL_FOLDERS = [Path(os.environ[_v]) / "HaiveQueen" for _v in ("LOCALAPPDATA", "APPDATA") if os.environ.get(_v)]
LIVE_TOP = ("haivequeen.db", "loop.lock", "exports", "runs", "STOP", "self-update.lock", "hive-schedule.lock", "compile-check.lock", "start.lock", "start.pending", "gui.pid", "worktree-cleanup.lock", "server-tests.json", "update-check.json", "inbox-writes.lock", "inbox-transactions")  # written by a running loop (STOP: set by a self-update; server-tests.json: its periodic test of the own LLM servers) or an open graphical dashboard (gui.pid, update-check.json: its update check), not by tests; the audit guard still finds a test that writes them


def own_profile_setting(rel: Path) -> bool:
    """True for a settings file HaiveQueen writes into a CLI's profile folder itself (profile/<cli>/<name of profile.SPECS>)."""
    if len(rel.parts) != 3 or rel.parts[0] != "profile":
        return False
    from haivequeen import profile
    return rel.parts[2] in {name for spec in profile.SPECS.values() for name in spec["settings"]}


def is_live(rel: Path) -> bool:
    """True when the file (path relative to a data folder) is written by a running loop: a top-level live file, anything below logs/<project>,
    or a file a CLI writes at run time below profile/<cli> (session logs, caches, state). The settings files HaiveQueen writes into the profile
    itself (profile.SPECS) and anything directly in profile/ are not live, so a test that writes them is still found."""
    if rel.parts[0].startswith("compile-check.lock"):
        return len(rel.parts) == 1 and rel.name in ("compile-check.lock", "compile-check.lock.guard")  # only the machine-wide locks
    if rel.parts[0] == "removal-locks":   # lock files of the live loop's worktree and branch removals
        return len(rel.parts) == 2 and rel.name.endswith(".lock.guard")
    if rel.parts[0] == "exempt-processes":   # marker files of live agent processes
        return True
    if rel.parts[0].startswith(LIVE_TOP) or (rel.parts[0] == "logs" and len(rel.parts) > 2):
        return True
    if rel.parts[0] == "profile" and len(rel.parts) > 2:
        return not own_profile_setting(rel)
    return False


def snapshot_real(bases=None) -> dict:
    """Return {path: (size, mtime_ns)} of the files in the real data and settings folders (or in `bases`), without what a running loop writes itself.

    The profile settings files are left out as well: the running loop rewrites them at every agent start (profile.prepare) and Codex adds its own
    entries while it runs, so their file times cannot tell a test's write from the loop's. The audit guard covers them instead."""
    snap = {}
    for base in (REAL_FOLDERS if bases is None else bases):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            rel = p.relative_to(base)
            if not p.is_file() or is_live(rel) or own_profile_setting(rel):
                continue
            st = p.stat()
            snap[str(base / rel)] = (st.st_size, st.st_mtime_ns)
    return snap


# The audit guard: writes of this process and child processes that would use the real folders.
GUARDED = [os.path.normcase(os.path.abspath(str(_b))) for _b in REAL_FOLDERS]
GUARD_HITS = []
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
PATH_EVENTS = {"os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.rename": (0, 1), "os.truncate": (0,), "os.utime": (0,), "os.chmod": (0,),
               "os.link": (0, 1), "os.symlink": (1,), "shutil.rmtree": (0,), "shutil.copyfile": (1,), "shutil.copytree": (1,), "shutil.move": (0, 1),
               "shutil.make_archive": (0,), "sqlite3.connect": (0,)}


def guarded_path(path, guarded=None):
    """The normalised path when `path` lies in a guarded folder (the real ones, or `guarded`), else None. File descriptors and odd values are skipped."""
    if isinstance(path, int) or path is None:
        return None
    try:
        text = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return None
    for base in (GUARDED if guarded is None else guarded):
        if text == base or text.startswith(base.rstrip("\\/") + os.sep):
            return text
    return None


def child_folders(env, home=None):
    """The data, settings and log folders a HaiveQueen child process with the environment `env` would use (same order as haivequeen.platform)."""
    windows = os.name == "nt"
    root = Path(home) if home else Path.home()

    def folder(override, var_win, var_xdg, win_fallback, posix_fallback):
        if env.get(override):
            return Path(env[override])
        base = env.get(var_win if windows else var_xdg)
        if base:
            return Path(base) / "HaiveQueen"
        return root.joinpath(*(win_fallback if windows else posix_fallback)) / "HaiveQueen"
    data = folder("BZZ_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME", ("AppData", "Local"), (".local", "share"))
    found = [data, folder("BZZ_CONFIG_DIR", "APPDATA", "XDG_CONFIG_HOME", ("AppData", "Roaming"), (".config",))]
    found.append(Path(env["BZZ_LOG_DIR"]) if env.get("BZZ_LOG_DIR") else data / "logs")
    return found


def guard_event(event, args, guarded=None, home=None):
    """The guarded paths the audit event `event` would write to: a file opened for writing, a removal, rename, copy target or database connection,
    or a child process whose working folder or BZZ_*/AppData folders lie in a guarded folder. Empty for everything else."""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        writing = any(c in mode for c in "wax+") if isinstance(mode, str) else isinstance(flags, int) and bool(flags & WRITE_FLAGS)
        hit = guarded_path(path, guarded) if writing else None
        return [hit] if hit else []
    if event in PATH_EVENTS:
        return [h for h in (guarded_path(args[i], guarded) for i in PATH_EVENTS[event] if i < len(args)) if h]
    if event == "subprocess.Popen":
        _exe, _argv, cwd, env = (tuple(args) + (None, None, None, None))[:4]
        env = os.environ if env is None else env
        try:
            env = {os.fsdecode(k).upper() if os.name == "nt" else os.fsdecode(k): os.fsdecode(v) for k, v in dict(env).items()}
        except (TypeError, ValueError):
            return []
        hits = [h for h in (guarded_path(str(f), guarded) for f in child_folders(env, home)) if h]
        hit = guarded_path(cwd, guarded) if cwd is not None else None
        return hits + ([hit] if hit else [])
    return []


def _audit(event, args):
    """Audit hook of the test process: record what guard_event finds (never raises, so the code under test runs unchanged)."""
    if event != "open" and event not in PATH_EVENTS and event != "subprocess.Popen":
        return
    try:
        for hit in guard_event(event, args):
            GUARD_HITS.append("%s %s" % (event, hit))
    except Exception:   # a guard error must never break the test that triggered it
        pass


def guard_hits():
    """Every recorded write or child process of this session that reached a real folder, without repeats."""
    return sorted(set(GUARD_HITS))


REAL_BEFORE = snapshot_real()
os.environ.setdefault("BZZ_NO_UPDATE_CHECK", "1")   # no automatic update check asks GitHub during the tests (test_k10_updates.py removes this)
os.environ.setdefault("BZZ_NO_LOGIN_CHECK", "1")   # providers of the tests need no login in the profile (test_profile.py removes this)
ROOT = Path(tempfile.mkdtemp(prefix="hq-tests-appdata-"))
REDIRECTED = {"LOCALAPPDATA": ROOT / "local", "APPDATA": ROOT / "roaming", "XDG_DATA_HOME": ROOT / "local", "XDG_CONFIG_HOME": ROOT / "roaming"}
ORIGINAL = {_var: os.environ.get(_var) for _var in REDIRECTED}  # real values for read-only live-loop checks
for _var, _path in REDIRECTED.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_var] = str(_path)
# Even read-only settings/plan calls now coordinate with inbox recovery. Give
# those calls their coordination inode explicitly; it contains no settings or
# runtime data. Only this exact, unchanged fixture is excluded below. The real
# folder audit still records every write, including writes to a lock file.
COORDINATION_FIXTURE = REDIRECTED["APPDATA"] / "HaiveQueen" / "inbox-writes.lock"
COORDINATION_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
COORDINATION_FIXTURE.write_bytes(b"0")
sys.addaudithook(_audit)   # after the redirection: from here on nothing of this session may write into the real folders


def leaked():
    """Return the files below the redirected folders: what a test wrote without its own BZZ_* folders."""
    return sorted(str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file()
                  and not (p == COORDINATION_FIXTURE and p.read_bytes() == b"0"))


def real_folder_changes(bases=None, before=None) -> list:
    """Return the files of the real data and settings folders (or of `bases`) that were added, removed or changed since this module was imported
    (or since the snapshot `before`), without the files the running loop writes itself (see snapshot_real; the audit guard covers those)."""
    before = REAL_BEFORE if before is None else before
    now = snapshot_real(bases)
    return sorted(p for p in set(now) | set(before) if now.get(p) != before.get(p))
