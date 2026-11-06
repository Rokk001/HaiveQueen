"""Update function for every user (H-42): HaiveQueen asks GitHub for a newer release once per interval (config.toml `update.checkHours`,
default 24 h, 0 switches the daily check off) and when the owner presses "Check now"; the GUI then shows "New version available" with an update button.

On confirmation the detached self-update worker installs the release: it stops the loop, waits until no agent of any Apiary runs (no Queen or Bee
starts meanwhile, H-46 i), fast-forwards the install folder to the release tag, installs the package again when pyproject.toml changed and starts
HaiveQueen again. The result of the last check is kept in update-check.json of the data folder, so a restart neither forgets an offer nor asks early.
The installed version is the release tag of the install folder (pyproject.toml without one).
"""
import json
import os
import re
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from . import platform as osplat
from .i18n import N_

STATE_FILE = "update-check.json"
DEFAULT_HOURS = 24
DEFAULT_REPOSITORY = "Rokk001/HaiveQueen"   # the public repository; an install from a fork reads its own origin

TEXT_CHECK_NOW = N_("Check now")
TEXT_CHECKING = N_("checking for updates…")
TEXT_AVAILABLE = N_("New version available: %s – Update")
TEXT_CONFIRM = N_("Install version %s now? HaiveQueen starts no new Queen or Bee, waits until every running agent has ended, installs the new version and restarts.")
TEXT_INSTALLING = N_("installing version %s – waiting for running agents")
TEXT_FAILED = N_("update to %s failed, see STATUS")
TEXT_CURRENT = N_("up to date")
TEXT_CHECK_FAILED = N_("update check failed")
TEXT_NO_RELEASE = N_("no release published yet")


# ---- persisted state ----

def state_path() -> Path:
    """The file that keeps the result of the last check and a started install."""
    return osplat.data_dir() / STATE_FILE


def load_state() -> dict:
    """{last_check, available, error, no_release, installing}; an unreadable or missing file gives an empty state."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    """Write the state atomically."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(temp, path)


# ---- settings ----

def check_hours(machine) -> float:
    """Hours between two automatic checks (config.toml `update.checkHours`, default 24; 0 = no automatic check)."""
    value = ((machine or {}).get("update") or {}).get("checkHours", DEFAULT_HOURS)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_HOURS
    return value if value >= 0 else DEFAULT_HOURS


def repository(machine, live=None, run=None) -> str:
    """owner/name of the GitHub repository whose releases are offered: `update.repository`, else the GitHub origin of the install folder, else the public one."""
    from . import releases, selfupdate
    configured = str(((machine or {}).get("update") or {}).get("repository") or "").strip()
    if configured:
        return configured
    run = run or selfupdate.run_command
    live = live or selfupdate.live_folder()
    code, out = run(["git", "-C", str(live), "remote", "get-url", "origin"], live)
    if code == 0:
        try:
            return releases.repository_from_remote(out.strip())
        except ValueError:
            pass
    return DEFAULT_REPOSITORY


def installed_version(live=None, run=None) -> str:
    """The installed version: the nearest release tag vX.Y.Z of the commit of the install folder, else the version of its pyproject.toml, else '–'."""
    from . import selfupdate
    run = run or selfupdate.run_command
    live = Path(live or selfupdate.live_folder())
    code, out = run(["git", "-C", str(live), "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "HEAD"], live)
    tag = out.strip()
    if code == 0 and re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        return tag[1:]
    try:
        with open(live / "pyproject.toml", "rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    try:
        from importlib.metadata import version
        return version("haivequeen")
    except Exception:
        return "–"


# ---- checking ----

def due(machine, state=None, now=None) -> bool:
    """Whether the automatic check is due (interval passed since the last attempt; never with checkHours 0 or the variable BZZ_NO_UPDATE_CHECK)."""
    hours = check_hours(machine)
    if hours <= 0 or os.environ.get("BZZ_NO_UPDATE_CHECK"):
        return False
    state = load_state() if state is None else state
    try:
        last = datetime.fromisoformat(state.get("last_check") or "")
    except ValueError:
        return True
    return (now or datetime.now()) - last >= timedelta(hours=hours)


def check(machine, now=None, manual=False, lookup=None, current=None):
    """Ask GitHub for a newer release when due (or always with `manual`) and return the new state; None when it was not due.

    An error is stored with the time of the attempt, so the next automatic check waits a whole interval (Check now may retry at once).
    GitHub's HTTP 404 (no release published yet) is no error: it sets no_release, which the header shows instead of "update check failed"."""
    from urllib.error import HTTPError
    from . import releases
    now = now or datetime.now()
    state = load_state()
    if not manual and not due(machine, state, now):
        return None
    lookup = lookup or releases.check_latest
    state["last_check"] = now.isoformat(timespec="seconds")
    try:
        found = lookup(repository(machine), current or installed_version())
        state.update(available=dict(found) if found else None, error="", no_release=False)
    except Exception as exc:   # offline, rate limit, unknown repository: shown, never fatal
        if isinstance(exc, releases.NoRelease) or (isinstance(exc, HTTPError) and exc.code == 404):
            state.update(available=None, error="", no_release=True)
        else:
            state.update(available=None, error=str(exc)[:300] or exc.__class__.__name__, no_release=False)
    save_state(state)
    return state


def offered(state=None):
    """The release the GUI offers ({version, url}) or None."""
    state = load_state() if state is None else state
    found = state.get("available")
    return found if isinstance(found, dict) and found.get("version") else None


def scheduled_check(machine):
    """The automatic check of a loop round or the GUI; never raises."""
    try:
        return check(machine)
    except Exception:
        return None


# ---- installing ----

def request_install(version, spawn=None) -> dict:
    """Start the install of the offered release `version` after the owner confirmed it; another version raises ValueError."""
    from . import db, selfupdate
    found = offered()
    if not found or found["version"] != str(version):
        raise ValueError("version %s is not the offered update" % version)
    after = 0
    try:
        conn = db.connect(selfupdate_db_path())
        try:
            row = db.last_self_update(conn)
            after = int(row["id"]) if row else 0
        finally:
            conn.close()
    except Exception:
        pass
    spawn = spawn or (lambda v: selfupdate.spawn_worker(version=v))
    result = spawn(found["version"])
    state = load_state()
    state["installing"] = {"version": found["version"], "after_row": after, "since": datetime.now().isoformat(timespec="seconds")}
    save_state(state)
    return result or {}


def selfupdate_db_path():
    """The database the self-update writes its rows into."""
    from . import config
    return config.db_path()


def install_finished(state, last_row):
    """'ok' or 'failed' once the self-update row of a started install has ended; None while it runs or when no install was started."""
    started = (state or {}).get("installing")
    if not started or not last_row or int(last_row.get("id") or 0) <= int(started.get("after_row") or 0) or not last_row.get("ended"):
        return None
    return "ok" if last_row.get("result") == "ok" else "failed"


def settle_install(last_row) -> str:
    """Close a started install once its self-update row ended: 'ok' (the GUI restarts itself) or 'failed' (shown until the next check); None otherwise."""
    state = load_state()
    done = install_finished(state, last_row)
    if done:
        started = state.pop("installing")
        if done == "failed":
            state["failed"] = started.get("version")
        else:
            state.pop("failed", None)
            state["available"] = None
        save_state(state)
    return done


def header_line(state, checking=False) -> tuple:
    """(text, level, action) of the update part of the GUI header: action 'install' offers the update button, 'check' is Check now, None nothing."""
    from .i18n import tr
    state = state or {}
    if state.get("installing"):
        return tr(TEXT_INSTALLING, state["installing"].get("version", "")), "warn", None
    if checking:
        return tr(TEXT_CHECKING), "dim", None
    found = offered(state)
    if found:
        return tr(TEXT_AVAILABLE, found["version"]), "warn", "install"
    if state.get("failed"):
        return tr(TEXT_FAILED, state["failed"]) + "  ·  " + tr(TEXT_CHECK_NOW), "bad", "check"
    if state.get("error"):
        return tr(TEXT_CHECK_FAILED) + "  ·  " + tr(TEXT_CHECK_NOW), "bad", "check"
    if state.get("no_release"):
        return tr(TEXT_NO_RELEASE) + "  ·  " + tr(TEXT_CHECK_NOW), "dim", "check"
    if state.get("last_check"):
        return tr(TEXT_CURRENT) + "  ·  " + tr(TEXT_CHECK_NOW), "dim", "check"
    return tr(TEXT_CHECK_NOW), "dim", "check"


def draining(alive=None) -> bool:
    """True while a living self-update worker holds its lock: it waits for running agents, so no Queen or Bee may start (H-46 i)."""
    from . import selfupdate
    alive = alive or osplat.process_alive
    try:
        pid = int(selfupdate.lock_path().read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        return False
    return pid > 0 and bool(alive(pid))


def started_by_update_worker(parent=None) -> bool:
    """True when the process that started this one holds the self-update lock: the restart of the worker, also of an older version that sets no BZZ_UPDATE_RESTART."""
    from . import selfupdate
    try:
        pid = int(selfupdate.lock_path().read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        return False
    return pid > 0 and pid == (parent or os.getppid())


# ---- command line ----

def cmd_update(args, conn) -> int:
    """`bzz update`: check now (default) or, with --install X, install the offered release X (the same as the update button)."""
    from . import config
    if args.install:
        try:
            res = request_install(args.install)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        print(json.dumps({"ok": True, "installing": args.install, **res}))
        return 0
    state = check(config.load_config(), manual=True)
    print(json.dumps({"ok": not state.get("error"), "installed": installed_version(), **state}))
    return 0 if not state.get("error") else 1


def register(sub) -> None:
    """Add the command update to the sub-parser collection of haivequeen/cli.py."""
    p = sub.add_parser("update")
    p.add_argument("--install", metavar="VERSION", help="install the offered release (waits for running agents, then restarts)")
    p.set_defaults(fn=cmd_update)
