"""Round loop: one loop over all registered projects (D1, D4) with shared slots, limits, RAM and build jobs.

Ports round.ps1 and round-loop.ps1 onto the database. A round syncs the plans of every project, orders the projects (priority first, ties by
the oldest waiting package), lets the routine decide what needs no language model, asks the brain only for events that need judgement,
validates every action (rules 11 and others) and starts agents through the terminal interface. A pause or drain (D8) skips the starts,
a lock starts a triage agent (D5), and at the start running agents are re-attached by process id.
The agent process itself is `haivequeen.agent_runner` (see docs/S13-S14-INTERFACE.md).
"""
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import brain, db, events, export, faults, header, inbox, reattach, routine, runstate, runtime, triage, usage, views
from . import platform as osplat
from . import scheduler as sch
from .routine import view
from .scheduler import ACTIVE, norm_plan
from .terminal import TerminalError, get_terminal, pane_title

TOOLS_DIR = Path(__file__).resolve().parent.parent
PHASES = ("work", "verify", "release", "integrate")
TEMPORARY = re.compile(r"^(no worker slot|no free slot|no integrator slot|free RAM|STOP flag|run state)")   # a start rejected for one of these keeps its brain event
AGENT_NAMES = ["Ada", "Bea", "Cleo", "Dina", "Ella", "Fia", "Gina", "Hana", "Ida", "Juna", "Kira", "Lea", "Mia", "Nora", "Ola",
               "Pia", "Rita", "Sara", "Tara", "Uma", "Vera", "Wilma", "Yara", "Zoe"]   # short first names of the agents; the main agent is called Queen and is not in this list
AGENT_COLOURS = ["4FA8FF", "C77DFF", "FF6FB5", "3DDCDC", "FF9F43", "3DDC97", "2B6CB0", "8A3FD1", "C2306F", "1E9C9C", "C26A10", "1E9B5F"]   # 6 hues at least 25 degrees apart, each light then dark (the first 6 agents differ in hue), none of the status colours
FORWARD_ENV = ("BZZ_DATA_DIR", "BZZ_CONFIG_DIR", "BZZ_LOG_DIR", "BZZ_RUNTIME_OVERRIDE", "BZZ_HOST", "BZZ_FAKE_AGENT", "BZZ_SAMPLE_SEC")


# ---- flag files ----

def stop_flag() -> Path:
    """Path of the STOP flag file: while it exists no agent starts and the loop ends after the running agents are done."""
    return osplat.data_dir() / "STOP"


def lock_file() -> Path:
    """Path of the loop lock file (the process id of the running loop)."""
    return osplat.data_dir() / "loop.lock"


def loop_running() -> int:
    """Process id of a running loop (lock file with a living process), else 0."""
    try:
        pid = int(lock_file().read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0
    return pid if osplat.process_alive(pid) and pid != os.getpid() else 0


def pane_lock_file() -> Path:
    """Path of the pane command lock (the process id of the process that sends pane commands right now)."""
    return osplat.data_dir() / "pane.lock"


def start_pending_file() -> Path:
    """Path of the marker that a window start is under way (the loop of that window has not taken its lock yet)."""
    return osplat.data_dir() / "start.pending"


def start_pending(max_age: float = 120.0) -> bool:
    """True when a window start began less than `max_age` seconds ago."""
    try:
        return time.time() - start_pending_file().stat().st_mtime < max_age
    except OSError:
        return False


def _take_pid_lock(path: Path, own_ok: bool, wait: float = 5.0) -> int:
    """Create the lock file `path` atomically (exclusive create) holding our process id.

    Returns 0 on success, else the process id of the living holder (-1 when the file stays unreadable). The lock of a dead process is taken over:
    the file is only removed when it still names that dead process. `own_ok` accepts a lock that names this very process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    holder = -1
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                holder = -1   # just created and not written yet, or damaged
                time.sleep(0.05)
                continue
            if own_ok and holder == os.getpid():
                return 0
            if osplat.process_alive(holder):
                return holder
            try:
                if int(path.read_text(encoding="ascii").strip()) == holder:
                    path.unlink()
            except (OSError, ValueError):
                pass
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
        return 0
    return holder


@contextmanager
def pane_lock(timeout: float = 120.0, poll: float = 0.1):
    """Serialise pane commands between processes: only the holder of pane.lock opens or splits a pane (loop round, `bzz start`).

    Waits up to `timeout` seconds for another process to finish its pane command, then raises TimeoutError.
    """
    deadline = time.monotonic() + timeout
    while True:
        holder = _take_pid_lock(pane_lock_file(), own_ok=False)
        if holder == 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("pane commands are blocked by process %s" % holder)
        time.sleep(poll)
    try:
        yield
    finally:
        try:
            pane_lock_file().unlink()
        except OSError:
            pass


def hold_pane_lock_until_exit(timeout: float = 120.0, poll: float = 0.1) -> bool:
    """Take pane.lock for the calling process and keep it: it counts as released when this process has exited (as in the first version).

    A pane that closes itself thus never overlaps a split or an open in the same window. Returns False when the lock stayed busy for `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while _take_pid_lock(pane_lock_file(), own_ok=True) != 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


def first_split_target(panes) -> str:
    """Pane id that the first agent pane splits when no agent pane is open: the dashboard pane, else the Queen pane, else None."""
    for marker in ("job: Dashboard", "| QUEEN |"):
        for pane in panes:
            if marker in pane.title:
                return pane.id
    return None


def queen_pane_title(model: str, test: bool) -> str:
    """Fixed title of the pane of the main agent (the Queen)."""
    return pane_title("all projects", "QUEEN", "Queen", model, test)


def close_leftover_panes(term, window: str) -> list:
    """Close the Queen and Overview panes a previous start left in `window` (the loop ended, the dashboard never does) and return their ids.
    Without this a new start opens a second tab next to the old one. Agent panes are never touched: only these two fixed titles match."""
    closed = []
    for p in term.list_panes(window):
        if p.alive and ("| all projects | QUEEN |" in p.title or "| all projects | Overview |" in p.title):
            term.close_pane(p.id)
            closed.append(p.id)
    return closed


def open_session_panes(term, window: str, title: str, boot: list, dash=None) -> list:
    """Open the Queen pane running `boot` and, when `dash` is given, the dashboard pane below it; return the pane ids. The caller holds pane.lock.
    Old Queen and Overview panes go first. Panes of running agents that are still open keep their tab and the Queen joins it: a new tab would split the one window in two."""
    close_leftover_panes(term, window)
    stay = [p for p in term.list_panes(window) if p.alive]
    if stay:
        panes = [term.split_pane(window, title, "white", boot, "horizontal", 0.5, stay[0].id, "chief")]
    else:
        panes = [term.open_window(window, title, boot, "white", "chief")]
    if dash is not None:
        panes.append(term.split_pane(window, pane_title("all projects", "Overview", "Dashboard", "-", is_test_runtime()), "white", dash,
                                     "vertical", 0.8, panes[0], "dashboard"))
    return panes


def close_session_panes(conn, term, window: str) -> list:
    """Close the Overview pane, every agent pane whose agent has ended and, last, the Queen pane of `window`; return the ids.
    A pane of an agent that still runs is never closed."""
    closed, queen = [], []
    for p in term.list_panes(window):
        if not p.alive:
            continue
        parts = p.title.split(" | ")
        if "| all projects | Overview |" in p.title:
            term.close_pane(p.id)
            closed.append(p.id)
        elif "| all projects | QUEEN |" in p.title:
            queen.append(p)
        elif len(parts) >= 4 and parts[2].startswith("Agent ") and parts[3].startswith("job: "):
            agent = runtime.get_agent(conn, parts[1], parts[3][len("job: "):])
            if agent is None or agent["status"] not in runtime.ACTIVE_STATUSES:
                term.close_pane(p.id)
                closed.append(p.id)
    for p in queen:   # the Queen pane may be the one this process runs in: it goes last
        term.close_pane(p.id)
        closed.append(p.id)
    return closed


def close_panes_after_stop(conn) -> list:
    """Close the session panes of the configured window (see close_session_panes); a terminal error is ignored, it never blocks a stop."""
    from . import config
    try:
        settings = sch.build_settings(config.load_config(), {})
        term = get_terminal(settings.get("terminal", "auto"), str(osplat.runs_dir() / "_panes"))
        with pane_lock():   # no pane opens, splits or closes itself while these panes go (as the mutex of the first version)
            return close_session_panes(conn, term, settings.get("window", "HaiveQueen"))
    except Exception:
        return []


def is_test_runtime() -> bool:
    """True when the process runs with a test runtime (BZZ_RUNTIME_OVERRIDE set)."""
    return bool(os.environ.get("BZZ_RUNTIME_OVERRIDE"))


# ---- loop state (kv key loop-state, machine wide) ----

def write_state(conn, status: str, since: datetime, next_at) -> None:
    """Write the loop state for the dashboard and the pane header: status (brain, round, idle), since and the next round time."""
    runtime.kv_set(conn, sch.GLOBAL, "loop-state", json.dumps({
        "status": status, "since": since.isoformat(timespec="seconds"),
        "nextAt": next_at.isoformat(timespec="seconds") if next_at else None}))


def read_state(conn):
    """Read the loop state (dict) or None when missing."""
    raw = runtime.kv_get(conn, sch.GLOBAL, "loop-state")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# ---- names, tasks, identity ----

def agent_task(env, plan: str, package: str, phase: str, prompt_file=None) -> str:
    """The task in plain words (one line): work = the package row of the plan; verify, release, integrate = phase plus package; else the prompt's first line."""
    plan_short = re.sub(r"-?PLAN$", "", os.path.splitext(os.path.basename(plan))[0])
    pkg_txt = "package " + package if package and package != "ALL" and not re.match(r"^(REL|INT-)", package) else "whole plan"
    m = re.match(r"^Q-(.+)$", package)
    if phase == "work" and m:
        return "Answered question %s: apply the %s's answer" % (m.group(1), env.settings.get("owner", "Owner"))
    m = re.match(r"^FIX-(.+)$", package)
    if phase == "work" and m:
        return "Fix blocked integration (tip %s)" % m.group(1)
    if phase == "verify":
        return "Verify %s, completeness check" % pkg_txt
    if phase == "release":
        return "Release: merge plan branch, compile check, ready row (%s)" % plan_short
    if phase == "integrate":
        return "Integrator: merge ready rows, build, checks, push"
    if phase == "triage":
        return "Triage of locked package %s" % package
    row = next((p for p in env.packages(plan) if p["id"] == package), None)
    if row and row["content"]:
        return "%s: %s" % (package, row["content"])
    if prompt_file and Path(prompt_file).is_file():
        lines = [ln.lstrip("﻿") for ln in Path(prompt_file).read_text(encoding="utf-8-sig").splitlines()]
        start = lines.index("TASK") + 1 if "TASK" in lines else 0
        cand = [ln for ln in lines[start:] if ln.strip()]
        if cand:
            return "Plan %s, package %s: %s" % (plan_short, package, " ".join(cand[0].split()))
    return "Plan %s, package %s" % (plan_short, package)


def agent_identity(conn, excluded_names=()) -> dict:
    """Select an unused first name and colour, excluding the given predecessor names."""
    rows = []
    for a in runtime.list_agents(conn, None, "active"):
        recent = False
        if a.get("ended_at"):
            ended = db.parse_time(a["ended_at"])
            if ended is not None:
                recent = (datetime.now() - ended).total_seconds() <= 3600
        if a.get("status") in ACTIVE or recent:
            rows.append(a)
    used_n = [str(a.get("person")) for a in rows] + list(excluded_names)
    used_c = [str(a.get("color")) for a in rows]
    run_c = [str(a.get("color")) for a in rows if a.get("status") in ACTIVE]
    running_names = {str(a.get("person")) for a in rows if a.get("status") in ACTIVE}
    n = (next((x for x in AGENT_NAMES if x not in used_n), None)
         or next((x for x in AGENT_NAMES if x not in running_names and x not in excluded_names), None)
         or AGENT_NAMES[len(rows) % len(AGENT_NAMES)])
    # Colours of agents that ended within the last hour are given up first; a colour of a running agent repeats only with more running agents than colours (then the least used one).
    # The second shade of a hue is only taken when every hue is in use by a running agent: colours are compared by hue family (index modulo 6), not by exact value.
    run_hues = {AGENT_COLOURS.index(x) % 6 for x in run_c if x in AGENT_COLOURS}
    free_hue = [x for x in AGENT_COLOURS if AGENT_COLOURS.index(x) % 6 not in run_hues]
    c = (next((x for x in free_hue if x not in used_c), None) or (free_hue[0] if free_hue else None)
         or next((x for x in AGENT_COLOURS if x not in used_c), None) or next((x for x in AGENT_COLOURS if x not in run_c), None)
         or min(AGENT_COLOURS, key=run_c.count))
    return {"Person": n, "Color": c}


# ---- starting an agent through the terminal ----

def forward_env_args() -> list:
    """`--env NAME=VALUE` arguments for the runner: the test and folder overrides the loop was started with."""
    out = []
    for k in FORWARD_ENV:
        if os.environ.get(k):
            out += ["--env", "%s=%s" % (k, os.environ[k])]
    return out


def runner_argv(env, spec: dict) -> list:
    """Command line of the agent process. Arguments are documented in docs/S13-S14-INTERFACE.md.

    The module is started through a small bootstrap, so it works from any folder: python -c "<add tools folder to sys.path; call haivequeen.agent_runner.main(argv)>".
    The run folder is <data>/runs/<project>/<name> (--runs-dir = <data>/runs/<project>), the folder the routine reads final.txt from.
    """
    boot = "import sys;sys.path.insert(0,sys.argv[1]);from haivequeen.agent_runner import main;sys.exit(main(sys.argv[2:]))"
    return [osplat.python_executable(), "-c", boot, str(TOOLS_DIR),
            "--project", env.name, "--name", spec["name"], "--provider", spec["provider"], "--protocol", spec["protocol"],
            "--plan", spec["plan"], "--package", spec["package"], "--phase", spec["phase"], "--cwd", spec["cwd"],
            "--prompt-file", spec["prompt_file"], "--runs-dir", str(env.run_dir(spec["name"]).parent)] + forward_env_args()


def alive_agent_pane(row: dict, alive=None) -> bool:
    """True when an agent row has a pane that certainly exists now: the runner process is alive and the agent runs (a pane of an ended agent is about to close)."""
    alive = alive or osplat.process_alive
    return bool(row.get("pane_id") and not row.get("paneClosed") and row.get("status") == "running" and row.get("pid") and alive(row["pid"]))


def choose_slot(rows: list, name: str, min_cols: int = 40, min_rows: int = 24, alive=None) -> dict:
    """Where the next agent pane goes: split the widest open agent pane side by side while both halves stay at least `min_cols` wide, else the tallest
    one top/bottom, else the widest anyway. Returns {target (pane id or None), direction, cols, rows, half (the split agent row or None)}."""
    open_rows = [r for r in rows if r.get("name") != name and alive_agent_pane(r, alive)]
    if not open_rows:
        return {"target": None, "direction": "horizontal", "cols": 140, "rows": 50, "half": None}
    cols_of = lambda r: int(r.get("paneCols") or 0)   # noqa: E731
    rows_of = lambda r: int(r.get("paneRows") or 0)   # noqa: E731
    wide = sorted([r for r in open_rows if cols_of(r) >= 2 * min_cols + 1], key=cols_of, reverse=True)
    tall = sorted([r for r in open_rows if rows_of(r) >= min_rows], key=rows_of, reverse=True)
    if wide:
        half, direction = wide[0], "horizontal"
    elif tall:
        half, direction = tall[0], "vertical"
    else:
        half, direction = sorted(open_rows, key=cols_of, reverse=True)[0], "horizontal"   # nothing fits: the widest is split anyway
    cols, rows_n = cols_of(half), rows_of(half)
    if direction == "horizontal":
        cols //= 2
    else:
        rows_n //= 2
    return {"target": half["pane_id"], "direction": direction, "cols": cols, "rows": rows_n, "half": half}


def launch_agent(env, terminal, window: str, spec: dict, settle: float = 3.0, poll: float = 2.0, tries: int = 20) -> str:
    """Open the pane of one agent in the orchestrator window and wait until its runner has set the pid in the agent row.

    The pane goes beside or below the widest open agent pane (choose_slot); without a live pane the window is opened. When the runner does not
    show up within poll x tries seconds the pane is launched once more. Returns the pane id.
    """
    conn = env.conn
    rows = runtime.list_agents(conn, None, "active")
    try:
        live = [p for p in terminal.list_panes(window) if p.alive]
    except TerminalError:
        live = []
    live_ids = set(p.id for p in live)
    slot = choose_slot([r for r in rows if r.get("pane_id") in live_ids], spec["name"])   # a pane the register no longer knows is no split target
    title = pane_title(env.name, "Agent " + spec["person"].upper(), spec["name"], spec["model_long"], is_test_runtime())
    command = runner_argv(env, spec)
    pane_name = "%s__%s" % (env.name, spec["name"])

    def open_pane(label: str) -> str:
        """Open the pane (split a live one, or open the window when none is alive); one pane command at a time across all processes."""
        with pane_lock():
            if not live:
                env.log("pane of %s: no live pane to split, opens the window%s" % (spec["name"], label))
                return terminal.open_window(window, title, command, env.colour, pane_name)
            target = slot["target"] or first_split_target(live)   # no agent pane yet: beside the dashboard (else the chief) pane
            env.log("pane of %s: splits %s (%s)%s" % (spec["name"], target or "newest", slot["direction"], label))
            return terminal.split_pane(window, title, env.colour, command, slot["direction"], 0.5, target, pane_name)

    pane_id = open_pane("")
    cols, rows_n = slot["cols"], slot["rows"]
    if slot["half"]:
        env.update_agent(slot["half"]["name"], {"paneCols": cols, "paneRows": rows_n})   # estimate until the pane reports its real size
    env.update_agent(spec["name"], {"pane_id": pane_id, "paneCols": cols, "paneRows": rows_n, "paneClosed": False})
    for attempt in (1, 2):
        for _ in range(tries):
            time.sleep(poll)
            row = runtime.get_agent(conn, env.name, spec["name"])
            if row and row.get("pid"):
                time.sleep(settle)   # settle time: the split that created the pane is finished
                return pane_id
        if attempt == 1:
            try:
                alive_any = any(p.alive for p in terminal.list_panes(window))
            except TerminalError:
                alive_any = False
            if not alive_any:
                env.log("pane of %s: the terminal is not running, no second launch" % spec["name"])
                return pane_id
            env.log("pane of %s did not start within %d s, launching it again" % (spec["name"], int(poll * tries)))
            live = [p for p in terminal.list_panes(window) if p.alive]
            pane_id = open_pane(" (second launch)")
    return pane_id


# ---- round ----

class Pool:
    """Slots shared by all projects of a round: free slots per provider name and free integrator slots."""

    def __init__(self, free: dict, int_free: int):
        """Remember the counters."""
        self.free = free
        self.int_free = int_free

    def workers(self) -> int:
        """Free worker slots in total."""
        return sum(self.free.values())


class ProjectRound:
    """State of one project in one round: report lines, started count, names and busy packages."""

    def __init__(self, env):
        """Start empty."""
        self.env = env
        self.report = []
        self.started = 0
        self.names = []
        self.busy = []
        self.qs = []


def ram_gb() -> float:
    """Free RAM in GB."""
    return osplat.free_ram_mb() / 1024.0


def _build_names(env) -> list:
    """Names of running compiler or build processes of this project (settings buildProcessNames)."""
    return sorted({n for _pid, n in osplat.list_processes(env.settings.get("buildProcessNames") or [])})


def _state_summary(env, quiet=False) -> dict:
    """Plan rows, startable plans and queue data of one project (the first part of a round)."""
    prows = routine.plan_rows(env)
    plans = []
    for pr in prows:
        f = env.workspace / pr["path"]
        st = f.stat()
        plans.append({"path": pr["path"], "status": pr["status"], "sizeKB": int(st.st_size / 1024),
                      "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    plans.sort(key=lambda p: p["modified"], reverse=True)
    return {"all_plans": prows, "plans": plans}


# Serialize Hive scheduling between the loop and owner commands.
@contextmanager
def hive_schedule_lock(timeout: float = 30.0):
    """Serialize Hive scheduling between the loop and owner commands."""
    path = osplat.data_dir() / "hive-schedule.lock"
    deadline = time.monotonic() + timeout
    while _take_pid_lock(path, own_ok=False) != 0:
        if time.monotonic() >= deadline:
            raise TimeoutError("Hive scheduling is busy")
        time.sleep(0.1)
    try:
        yield
    finally:
        if path.exists() and path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()


# Schedule one round of Hives across every registered project.
def run_round(conn, machine: dict, envs=None, terminal=None, brain_answer=None, brain_runner=None, dry_run=False, plan_files=None,
              out_file=None, settle: float = 3.0, poll: float = 2.0, tries: int = 20, free_ram=None, alive=None) -> dict:
    """Schedule one round of Hives across every registered project."""
    from . import hives
    envs = envs if envs is not None else sch.load_projects(conn, machine)
    with hive_schedule_lock():
        if dry_run:
            import sqlite3
            snapshot = sqlite3.connect(":memory:", isolation_level=None)
            snapshot.row_factory = sqlite3.Row
            try:
                conn.backup(snapshot)
                copied = [sch.ProjectEnv(snapshot, e.name, e.workspace, e.settings, e.colour, e.priority, e.plan_globs, e.plan_exclude) for e in envs]
                result = hives.schedule_round(snapshot, machine, copied, terminal=terminal, dry_run=True, free_ram=free_ram, alive=alive,
                                              settle=settle, poll=poll, tries=tries, plan_files=plan_files)
            finally:
                snapshot.close()
            if out_file:
                Path(out_file).parent.mkdir(parents=True, exist_ok=True)
                Path(out_file).write_text(json.dumps(result, indent=2), encoding="utf-8")
                if envs:
                    e = envs[-1]
                    snapshot_view = {"project": e.name, "repo": str(e.code_repo), "workspace": str(e.workspace), "dryRun": True}
                    Path(out_file).with_suffix(".snapshot.json").write_text(json.dumps(snapshot_view, indent=2), encoding="utf-8")
            return result
        return hives.schedule_round(conn, machine, envs, terminal=terminal, dry_run=False, free_ram=free_ram, alive=alive,
                                    settle=settle, poll=poll, tries=tries, plan_files=plan_files)


def _min_ram(env) -> float:
    """Minimum free RAM in GB for starting agents (pilot mode has its own value)."""
    s = env.settings
    return float(s.get("pilotMinFreeRamGB", 2)) if s.get("pilot") else float(s.get("minFreeRamGB", 2))


def _make_pool(conn, any_env) -> Pool:
    """Free slots per provider across all projects (workers of every project count) and free integrator slots."""
    active = [view(r) for r in runtime.list_agents(conn, None, "active") if r.get("status") in ACTIVE]
    providers = any_env.providers if any_env else []
    used_int = sum(1 for a in active if a.get("phase") == "integrate")
    return Pool(sch.provider_free(providers, active), max(0, int((any_env.settings if any_env else {}).get("integratorSlots", 1)) - used_int))


def run_hook(env, summary: dict) -> None:
    """Call the onEvent command of the project (see events.py) with this round's data."""
    qrows = list(routine.queue_rows(env).values())
    events.invoke_hook(env, summary["all_plans"], qrows, lambda n: routine.verify_zero(env, n), routine.question_rows(env))


def routine_release_locks(env) -> None:
    """Release locks that were resolved: a decision or an answer (owner, coordinator or auto) for item LOCK-<package>, a decision that closes the
    package, or (with triage) a package whose open points are all decided. The package is unlocked and the failures counted so far are forgiven,
    so it gets a fresh set of attempts."""
    conn = env.conn
    triage_on = env.settings.get("triage", True)
    for kv in runtime.kv_list(conn, env.name, "lock:"):
        plan, _, pkg = kv["key"][5:].rpartition("|")
        row = next((p for p in runtime.list_packages(conn, env.name, plan) if p["id"] == pkg), None)
        resolved = routine.lock_resolution(env, plan, pkg)
        if row is None:
            if not resolved:
                continue
            row = {"locked": False}   # the whole-plan check (ALL) has no package row
        elif row["locked"] and (resolved or (triage_on and not triage._undecided_points(conn, env.name, plan, pkg))):
            runtime.set_package(conn, env.name, plan, pkg, locked=False, reason="")
            row["locked"] = False
        if not row["locked"] and (resolved or triage_on):
            agents = routine.all_agents(env)
            fails = routine.package_failures(env, agents, plan, pkg)
            runtime.kv_set(conn, env.name, "lockrel:%s|%s" % (plan, pkg), str(fails))
            runtime.kv_set(conn, env.name, "lockrelv:%s|%s" % (plan, pkg), str(routine.done_verifies(agents, plan, pkg)))   # verifies up to now no longer count towards a new lock
            runtime.kv_delete(conn, env.name, kv["key"])
            env.log("package %s of %s unlocked, %d earlier failure(s) forgiven" % (pkg, plan, fails))


def _project_round(env, pool, summary, terminal, brain_answer, brain_runner, dry_run, plan_files, out_file, settle, poll, tries,
                   free_ram, alive, machine) -> dict:
    """The round of one project: snapshot, routine, brain, validation and execution of the actions, triage, event hook."""
    s = env.settings
    conn = env.conn
    pr = ProjectRound(env)
    pilot = bool(s.get("pilot"))
    if pilot:
        plan_files = [s.get("pilotPlan", "")]
    prows = summary["all_plans"]
    by_path = {norm_plan(p["path"]): p for p in prows}
    plan_status = {norm_plan(p["path"]): str(p["status"]) for p in prows}
    use_rows = [p for p in prows if not plan_files or any(norm_plan(f) == norm_plan(p["path"]) or norm_plan(p["path"]).endswith(norm_plan(f)) for f in plan_files)]
    startable = [{"path": p["path"], "status": p["status"]} for p in use_rows if p["status"] in ("READY", "IN_PROGRESS")]
    others = [{"path": p["path"], "status": p["status"]} for p in use_rows if p["status"] not in ("READY", "IN_PROGRESS")]
    if not dry_run:
        routine.return_interrupted_rows(env)   # rows taken by an integrator that ended interrupted go back to waiting
    qmap = routine.queue_rows(env)
    all_rows = [dict(r) for r in qmap.values()]
    queue = []
    for r in qmap.values():
        if re.match(r"^(ready|in Lauf|blockiert)", r["status"]):
            q = {k: v for k, v in r.items() if k != "note"}
            if r["status"] == "blockiert" and r["note"]:
                q["status"] = "blockiert (" + r["note"] + ")"
            q["status"] = q["status"][:140]
            queue.append(q)
    any_ready = any(q["status"].startswith("ready") for q in queue)
    any_in_run = any(q["status"].startswith("in Lauf") for q in queue)
    agents = [view(r) for r in env.agents("active")]
    build_names = _build_names(env)
    build_running = bool(build_names)
    qs = routine.question_rows(env)
    blocked = routine.blocked_tasks(agents)
    can_start = (not pilot and not plan_files and any_ready and not any_in_run and pool.int_free > 0 and not build_running)
    slots = {"freeByProvider": dict(pool.free), "integratorFree": pool.int_free, "workersMax": sum(p["slots"] for p in env.providers)}
    snapshot = {"now": datetime.now().strftime("%Y-%m-%d %H:%M"), "project": env.name, "repo": str(env.code_repo), "workspace": str(env.workspace), "dryRun": dry_run,
                "plans": startable, "notStartablePlans": others, "blockedTasks": [{"plan": b["Plan"], "package": b["Package"], "phase": b["Phase"], "error": b["Error"]} for b in blocked],
                "agents": [{"name": a["name"], "type": a.get("type"), "plan": a["plan"], "package": a.get("package"), "phase": a.get("phase"), "status": a.get("status"),
                            "finalFile": str(env.run_dir(a["name"]) / "final.txt").replace("\\", "/")} for a in agents],
                "slots": slots, "freeRamGB": round(free_ram(), 2), "minFreeRamGB": _min_ram(env), "fullBuildRunning": build_running,
                "questions": {"open": [q for q in qs if q["status"] == "OPEN"], "answered": [q for q in qs if q["status"] == "ANSWERED"]}}
    if pilot:
        snapshot["pilotMode"] = "true: only the packages of the pilot plan may be started; there is no integrator in this mode"
    else:
        snapshot["integration"] = {"rows": queue, "canStart": can_start}
    build_block = pilot and build_running
    if not dry_run and build_block:
        env.log("round skipped (build process running: %s)" % ", ".join(build_names))
        run_hook(env, summary)
        return {"report": pr.report, "started": 0, "actions": 0}
    # ---- routine (no language model) ----
    free_copy = dict(pool.free)
    free_copy.update({"workers": pool.workers(), "int": pool.int_free})
    ctx = {"plans": startable, "queue": queue, "all_rows": all_rows, "questions": qs, "free": free_copy,
           "can_start_integrator": bool(snapshot.get("integration", {}).get("canStart"))}
    rt = routine.get_routine(env, ctx)
    if not dry_run:
        for fl in routine.set_handoff_flags(env, agents):
            env.event("%s: context %s reached, HANDOFF requested" % (fl["Name"], sch.format_tokens(fl["Ctx"])))
            env.log("handoff flag written for %s (context %s)" % (fl["Name"], fl["Ctx"]))
    # ---- brain: only when an event needs judgement ----
    answer = None
    bres = None
    trigger = ""
    if rt["events"]:
        trigger = ",".join(dict.fromkeys(e["type"] for e in rt["events"]))
        if brain_answer is not None:
            answer = brain_answer
            bres = dict(brain.NO_ANSWER)
        else:
            delta = {"now": datetime.now().strftime("%Y-%m-%d %H:%M"), "project": env.name, "slots": slots, "freeRamGB": round(free_ram(), 2),
                     "events": [{k: e[k] for k in ("type", "plan", "package", "agent", "text")} for e in rt["events"]]}
            write_state(conn, "brain", datetime.now(), None)
            bres = brain.ask(env, delta, any(e["type"] == "new_plan" for e in rt["events"]), runner=brain_runner)
            answer = bres["answer"]
            if bres["fail"]:
                fail = bres["fail"]
                env.log(fail)
                env.event("Brain (%s): error - %s" % (trigger, fail[:120]), True)
                if not dry_run or is_test_runtime():
                    brain.record_call(env, trigger, bres, "Error: " + fail, True)
                handled = routine.handled_map(env)
                for ev in rt["events"]:
                    n = int(handled.get("try:" + ev["key"], 0) or 0) + 1
                    routine.set_handled(env, "try:" + ev["key"], str(n))
                    if n >= 2:
                        routine.set_handled(env, ev["key"])
                answer = None
        if answer is not None:
            for ev in rt["events"]:
                routine.set_handled(env, ev["key"])
    acts = list(rt["actions"]) + (list(answer.get("actions") or []) if answer else [])
    # ---- validate + enforce + execute ----
    state = {"names": [str(a["name"]) for a in agents], "busy": [norm_plan(a["plan"]) + "|" + str(a.get("package")) for a in agents if a.get("status") in ACTIVE],
             "qs": qs, "plan_status": plan_status, "by_path": by_path, "startable": startable, "blocked": blocked}
    ctxs = {"env": env, "pool": pool, "dry": dry_run, "terminal": terminal, "settle": settle, "poll": poll, "tries": tries, "free_ram": free_ram,
            "pilot": pilot, "plan_files": plan_files, "snapshot": snapshot, "rt_events": rt["events"], "state": state, "alive": alive,
            "min_ram": _min_ram(env), "report": pr.report, "machine": machine}
    can_start_any = (not pilot and runstate.starts_allowed(conn) and not stop_flag().exists() and pool.workers() > 0
                     and free_ram() >= _min_ram(env) and not build_running)   # nothing held back this round: an idle round is then a fault
    started = 0
    for a in acts:
        kind = str(a.get("action"))
        if kind == "note":
            pr.report.append("NOTE: %s" % a.get("text"))
            if not dry_run:
                env.log("NOTE %s" % a.get("text"))
        elif kind == "ask_owner":
            _do_ask_owner(ctxs, a)
        elif kind == "start_agent":
            started += _do_start(ctxs, a)
        elif kind == "lock_package":
            _do_lock(ctxs, a)
        elif kind in ("stall_package", "stall_plan"):
            _do_stall(ctxs, a)
        elif kind == "plan_done":
            _do_plan_done(ctxs, a)
        else:
            pr.report.append("REJECT unknown action '%s'" % kind)
    if not dry_run and s.get("triage", True):
        started += _start_triage(ctxs)
    if not dry_run:
        env.log("round done: %d action(s), %d agent(s) started" % (len(acts), started))
        faults.check_round(env, started, can_start_any)
    run_hook(env, summary)
    if rt["events"] and answer is not None and (not dry_run or is_test_runtime()):
        _brain_summary(ctxs, answer, trigger, bres, pr.report)
    if dry_run and out_file:
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        Path(out_file).write_text(json.dumps({"time": datetime.now().isoformat(), "snapshotSlots": slots, "freeRamGB": snapshot["freeRamGB"],
                                              "integrationCanStart": snapshot.get("integration", {}).get("canStart"),
                                              "brainAnswer": answer, "enforcement": pr.report}, indent=2), encoding="utf-8")
        Path(out_file).with_suffix(".snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    if not dry_run:
        try:
            views.write_views(conn, env.name)
        except Exception as exc:   # a broken view path must not stop a round
            env.log("views not written: %s" % exc)
    return {"report": pr.report, "started": started, "actions": len(acts)}


def _brain_summary(c, answer, trigger, bres, report) -> None:
    """Record the brain call with a one-line summary of what the answer led to and show it as an event."""
    env = c["env"]
    parts = []
    for x in answer.get("actions") or []:
        k = x.get("action")
        if k == "start_agent":
            hit = any(ln.startswith("START " + str(x.get("name"))) for ln in report)
            parts.append("%s %s" % (x.get("name"), "started" if hit else "rejected"))
        elif k == "ask_owner":
            parts.append("Question for %s" % env.settings.get("owner", "Owner"))
        elif k == "plan_done":
            parts.append("Plan done")
    note = next((x for x in (answer.get("actions") or []) if x.get("action") == "note"), None)
    if parts:
        summ = "; ".join(parts)
    elif note:
        summ = "no action: " + " ".join(str(note.get("text")).split())[:110]
    else:
        summ = "no action"
    if bres is not None:
        brain.record_call(env, trigger, bres, summ, False)
    env.event("Brain (%s): %s" % (trigger, summ))


def _do_ask_owner(c, a) -> None:
    """Action ask_owner: a question for the owner unless the item is already known or decided."""
    env, st, report, dry = c["env"], c["state"], c["report"], c["dry"]
    if not a.get("question") or not a.get("plan"):
        report.append("REJECT ask_owner: missing plan/question")
        return
    if any(q["plan"] == a["plan"] and q["item"] == a.get("item") and q["status"] in ("OPEN", "ANSWERED", "TRIAGE") for q in st["qs"]):
        report.append("SKIP ask_owner (already known): %s / %s" % (a["plan"], a.get("item")))
        if not dry:
            env.event("Question for %s not asked: %s / %s is already settled by a question of this item" % (env.settings.get("owner", "Owner"), a["plan"], a.get("item")))
        return
    src = routine.item_decided(routine.decided_items(env, st["qs"]), str(a["plan"]), str(a.get("item") or ""))
    if src:
        report.append("SKIP ask_owner (already decided in %s): %s / %s" % (src, a["plan"], a.get("item")))
        if not dry:
            env.log("skip ask_owner %s / %s: already decided in %s" % (a["plan"], a.get("item"), src))
            env.event("Question for %s not asked: %s / %s is already settled by %s" % (env.settings.get("owner", "Owner"), a["plan"], a.get("item"), src))
        return
    report.append("ASK_OWNER: %s / %s: %s" % (a["plan"], a.get("item"), a["question"]))
    if not dry:
        qid = db.add_question(env.conn, env.name, str(a["plan"]), str(a.get("item") or ""), str(a["question"]), str(a.get("recommendation") or ""))
        st["qs"].append({"id": "Q-%d" % qid, "plan": str(a["plan"]), "item": str(a.get("item") or ""), "status": "OPEN", "answer": ""})
        env.log("ask_owner Q-%d %s / %s" % (qid, a["plan"], a.get("item")))
        env.event("Question for %s: Q-%d (%s)" % (env.settings.get("owner", "Owner"), qid, a.get("item")))


def _do_lock(c, a) -> None:
    """Action lock_package: record the open points of a locked package once (kv key lock:<plan>|<package>, package row locked)."""
    env, report = c["env"], c["report"]
    tag = "LOCK-" + str(a["package"])
    key = "lock:%s|%s" % (a["plan"], a["package"])
    if not c["dry"] and runtime.kv_get(env.conn, env.name, key) is None:
        q = routine.prompt(env, "lockQuestion", {"marker": env.settings.get("openMarker", "Open for Owner"), "tag": tag, "pkg": str(a["package"]),
                                                  "agent": str(a.get("agent")), "text": str(a.get("text")),
                                                  "plan": routine.plan_ref(env, str(a["plan"]))})
        runtime.kv_set(env.conn, env.name, key, q)
        reason = routine.lock_reason(str(a.get("text") or "")) if env.settings.get("triage", True) else q[:300]
        try:
            runtime.set_package(env.conn, env.name, str(a["plan"]), str(a["package"]), locked=True, reason=reason or q[:300])
        except runtime.CmdError:
            pass   # the whole-plan check (ALL) has no package row
        env.log("package %s of %s locked, open question recorded" % (a["package"], a["plan"]))
        env.event("Package %s locked (%s)" % (a["package"], env.settings.get("openMarker", "Open for Owner")), True)
    report.append("LOCK %s %s" % (a["plan"], a["package"]))


def _do_stall(c, a) -> None:
    """Action stall_package / stall_plan (progress guard): stop the package or plan and ask the owner once, with the last verify report.

    The question goes straight to the owner (status open, never to the triage). The stop is the kv key stall:<plan>|<package> or
    planstall:<plan> with the question id; routine_release_stalls removes it only when the owner answered.
    """
    env, report = c["env"], c["report"]
    plan, pkg = str(a["plan"]), str(a.get("package") or "")
    if pkg:
        key, item = "stall:%s|%s" % (plan, pkg), "STALL-" + pkg
        why = ("the last %d work runs ended without a new commit" if a.get("idle") else "the number of open requirements did not fall over the last %d verifies")
        what = ("Package %s is stopped: %s, so no new agent is started for it. Last report (%s):\n%s"
                % (pkg, why % int(env.settings.get("progressVerifies", 3)), a.get("agent"), a.get("text")))
    else:
        key, item = "planstall:" + plan, "STALL-PLAN"
        what = ("Plan %s is stopped: agents ran for it during the last %s hours but no package of it was integrated, so no new agent is started."
                % (plan, env.settings.get("planStallHours", 6)))
    if not c["dry"] and runtime.kv_get(env.conn, env.name, key) is None:
        qid = db.add_question(env.conn, env.name, plan, item, what + "\n\nHow should it continue (other approach, change the requirement, release the stop)?",
                              "%s decides after seeing the report." % env.settings.get("owner", "Owner"))
        runtime.kv_set(env.conn, env.name, key, str(qid))
        env.log("%s of %s stopped by the progress guard, question Q-%d" % (pkg or "plan", plan, qid))
        env.event("%s stopped (%s)" % ("Package " + pkg if pkg else "Plan " + os.path.basename(plan), env.settings.get("openMarker", "Open for Owner")), True)
    report.append("STALL %s %s" % (plan, pkg or "(plan)"))


def routine_release_stalls(env) -> None:
    """Lift a progress-guard stop only when the owner answered its question (an answer of the Queen or the coordinator does not count, and a
    restart of the Queen changes nothing). A released package starts counting its verifies anew, a released plan its hours."""
    conn = env.conn
    for prefix in ("stall:", "planstall:"):
        for kv in runtime.kv_list(conn, env.name, prefix):
            q = next((x for x in runtime.list_questions(conn, env.name, "answered") if str(x["id"]) == str(kv["value"])), None)
            if q is None or q["answered_by"] != "owner":
                continue
            if prefix == "stall:":
                plan, _, pkg = kv["key"][len(prefix):].rpartition("|")
                agents = routine.all_agents(env)
                runtime.kv_set(conn, env.name, "stallrel:%s|%s" % (plan, pkg), str(routine.done_verifies(agents, plan, pkg)))
                runtime.kv_set(conn, env.name, "stallrelw:%s|%s" % (plan, pkg), str(len(routine.package_works(agents, plan, pkg))))
            else:
                runtime.kv_set(conn, env.name, "planstallrel:" + kv["key"][len(prefix):], db.now())
            runtime.kv_delete(conn, env.name, kv["key"])
            env.log("progress-guard stop %s lifted by the owner" % kv["key"])


def _do_plan_done(c, a) -> None:
    """Action plan_done (rule 11): IN_PROGRESS -> DONE only after every branch of the plan is integrated and the whole-plan verify ended with 'Offene Anforderungen: 0'."""
    env, st, report = c["env"], c["state"], c["report"]
    pk = norm_plan(str(a["plan"]))
    why = None
    if pk not in st["plan_status"]:
        why = "plan file missing"
    elif st["plan_status"][pk] != "IN_PROGRESS":
        why = "plan is not IN_PROGRESS"
    else:
        agents = [view(r) for r in env.agents("active")]
        open_rows = [r for r in routine.queue_rows(env).values() if re.match(r"^(ready|in Lauf|blockiert)", r["status"])
                     and str(r["plan"]).lower().endswith(str(a["plan"]).replace("\\", "/").lower())]
        ver = [x for x in agents if x["plan"] == a["plan"] and x.get("package") == "ALL" and x.get("phase") == "verify" and x.get("status") == "done"]
        if open_rows:
            why = "branches of this plan are still ready / in Lauf / blockiert in the integration queue"
        elif not ver:
            why = "no finished whole-plan verify agent (package ALL, phase verify)"
        elif routine.verify_open_count(env, ver[-1]["name"]) != 0:
            why = 'whole-plan verify did not end with "Offene Anforderungen: 0"'
    if why:
        report.append("REJECT plan_done %s: %s" % (a["plan"], why))
        return
    report.append("PLAN_DONE %s: IN_PROGRESS -> DONE" % a["plan"])
    if not c["dry"]:
        try:
            runtime.set_plan_status(env.conn, env.name, str(st["by_path"][pk]["path"]), "DONE", str(env.workspace))
            st["plan_status"][pk] = "DONE"
        except runtime.CmdError as exc:
            env.log("plan %s: status change failed: %s" % (a["plan"], exc))
            return
        env.log("plan %s: IN_PROGRESS -> DONE" % a["plan"])
        env.event("Plan %s done (DONE)" % os.path.basename(str(a["plan"])))


def _reject_reason(c, a):
    """Validation of a start_agent action in the order of the rules; returns (reason or None, picked provider or None)."""
    env, st, pool, dry = c["env"], c["state"], c["pool"], c["dry"]
    providers = env.providers
    name, plan, pkg, phase = a.get("name"), str(a.get("plan") or ""), str(a.get("package") or ""), a.get("phase")
    key = "%s|%s|%s" % (plan, pkg, phase)
    bundle = [str(b) for b in a.get("bundle") or []]
    why = None
    if not (a.get("name") and a.get("prompt") and a.get("cwd") and a.get("type") and a.get("phase")):
        why = "missing field"
    elif not sch.get_provider(providers, str(a["type"])):
        why = "unknown provider %s" % a["type"]
    elif phase not in PHASES:
        why = "bad phase %s" % phase
    elif not os.path.isdir(str(a["cwd"])):
        why = "cwd missing: %s" % a["cwd"]
    elif stop_flag().exists() and not dry:
        why = "STOP flag"
    elif not dry and not runstate.starts_allowed(env.conn):
        why = "run state %s: no new agents" % runstate.get_state(env.conn)["state"]
    elif any(b["Key"] == key for b in st["blocked"]):
        bk = next(b for b in st["blocked"] if b["Key"] == key)
        why = "blockiert: " + bk["Error"]
        if not dry:
            env.log("TECHNICAL NOTE: task %s failed 2 times with the same error and is not started again: %s" % (name, bk["Error"]))
    elif (norm_plan(plan) + "|" + pkg) in st["busy"] or any((norm_plan(plan) + "|" + b) in st["busy"] for b in bundle):
        why = "an agent already works on this package"
    elif phase in ("work", "verify") and (routine.stall_open(env, plan, pkg) or routine.plan_stall_open(env, plan)):
        why = "stopped by the progress guard: waiting for the answer of the owner"
    elif phase == "work" and routine.package_locked(env, plan, pkg):
        why = "package is locked: verifications reported open items too often (open question)"
    elif routine.is_frozen(env, plan, pkg):
        why = "package is frozen (nofollow list): no follow-up task"
    elif (phase != "integrate" and not (phase == "work" and pkg.startswith("FIX-") and norm_plan(plan) == norm_plan(routine.QUEUE_PLAN_ID))
          and not any(p["path"] == plan.replace("\\", "/") or p["path"] == plan for p in st["startable"])):
        why = "rule 11: plan is not READY or IN_PROGRESS"
    if not why and c["pilot"] and phase == "integrate":
        why = "pilot mode: no integrator"
    if not why and c["pilot"] and not plan.replace("/", "\\").endswith(str(env.settings.get("pilotPlan", "")).replace("/", "\\")):
        why = "pilot mode: only packages of the pilot plan"
    if not why and phase == "integrate" and c["plan_files"]:
        why = "pilot/test mode (plan files): no integrator"
    pick = None
    if not why and phase == "integrate":
        if not c["snapshot"].get("integration", {}).get("canStart"):
            why = "integrator.canStart is false"
        elif pool.int_free <= 0:
            why = "no integrator slot"
        else:
            pick = next((p for p in providers if p["protocol"] == "claude" and p["slots"] > 0 and (p["classes"].get("medium") or {}).get("model")), None)   # the integrator is a Claude Code agent and uses no worker slot
            if not pick:
                why = "no free slot: no Claude Code provider is on (slots 0)"
    elif not why:
        if pool.workers() <= 0:
            why = "no worker slot"
        else:
            # The package table decides what a package needs, not the "graphics" flag the brain happened to send; the flag still counts as "images".
            need = routine.package_needs_for(env, plan, pkg) if phase == "work" else []
            if a.get("graphics") is True and "images" not in need:
                need = need + ["images"]
            if need and not sch.capability_available(providers, need):
                why = "no provider with the capability %s has slots above 0" % ", ".join(need)
            else:
                cls0 = routine.start_class(env, phase, plan, pkg, str(a.get("class") or ""))
                pick = sch.select_provider(providers, cls0, need, pool.free, prefer=str(a["type"]), conn=env.conn)   # a busy slot never waits
                if not pick:
                    why = "no free slot" + (" for the capability %s" % ", ".join(need) if need else " in a fitting provider (class %s)" % cls0)
    if not why and not dry and c["free_ram"]() < c["min_ram"]:
        why = "free RAM %.2f GB is below %s GB" % (c["free_ram"](), c["min_ram"])
    if not why and not dry and c["pilot"] and _build_names(env):
        why = "a build process runs (pilot waits): " + ", ".join(_build_names(env))
    return why, pick


# Action start_agent: validate, write the prompt and the agent row, open the pane. Returns 1 when an agent was started.
def _do_start(c, a, forced_pick=None) -> int:
    """Action start_agent: validate, write the prompt and the agent row, open the pane. Returns 1 when an agent was started.

    `forced_pick` is a provider chosen by the caller (triage agents): the validation is skipped.
    """
    env, st, pool, report, dry = c["env"], c["state"], c["pool"], c["report"], c["dry"]
    why, pick = (None, forced_pick) if forced_pick is not None else _reject_reason(c, a)
    if why:
        report.append("REJECT start_agent %s: %s" % (a.get("name"), why))
        if not dry:
            env.log("reject %s: %s" % (a.get("name"), why))
        # A start that failed only for a temporary reason (slot, RAM, STOP) must not use up the brain event: it is asked again next round.
        if TEMPORARY.match(why) and not dry:
            for ev in c["rt_events"]:
                if norm_plan(ev["plan"]) == norm_plan(str(a.get("plan"))):
                    routine.remove_handled(env, ev["key"])
        return 0
    protocol, prov = pick["protocol"], pick["name"]
    nm, k = str(a["name"]), 1
    while nm in st["names"]:
        k += 1
        nm = "%s-%d" % (a["name"], k)
    st["names"].append(nm)
    plan, pkg, phase = str(a["plan"]), str(a.get("package") or ""), a["phase"]
    if phase == "integrate":
        pool.int_free -= 1
    else:
        pool.free[prov] = pool.free.get(prov, 0) - 1
    st["busy"].append(norm_plan(plan) + "|" + pkg)
    st["busy"] += [norm_plan(plan) + "|" + str(b) for b in a.get("bundle") or []]
    report.append("START %s [%s/%s] plan=%s package=%s cwd=%s" % (nm, prov, phase, plan, pkg, a["cwd"]))
    if dry:
        return 0
    run_dir = env.run_dir(nm)
    run_dir.mkdir(parents=True, exist_ok=True)
    pf = run_dir / "prompt.md"
    hf = run_dir / "HANDOFF"
    if phase == "bee":
        hdr = "You are a Bee. Follow only the task and rules provided below.\n\nTASK\n\n"
    elif phase == "queen":
        rules_doc = (env.workspace / "AGENTS.md") if env.settings.get("codeRepo") else "CLAUDE.md"
        hdr = ("You are the Queen of this Hive. Work the plan, preserve its state, and use the project work rules (%s). "
               "Before integration follow the project's integration rules.\n\nTASK\n\n" % rules_doc)
    elif protocol == "claude":
        flow_doc = env.settings.get("buildFlowDoc", "")
        hdr = ("You are the agent %s (one task, then you end). The rules for your phase (section 1 of AGENT-RULES.md and the rule sections you need) are "
               "already in your system prompt: do not read AGENT-RULES.md or CLAUDE.md again; read other files they name%s only when your task "
               "needs them. Update the plan's state. End when your task is finished.\n\nTASK\n\n" % (nm, " (e.g. %s)" % flow_doc if flow_doc else ""))
    else:
        rules_doc = (env.workspace / "AGENTS.md") if env.settings.get("codeRepo") else "CLAUDE.md"   # a project with a separate code repo keeps its rules in the workspace
        hdr = ("You are the agent %s (one task, then you end). Follow all rules in section 1 of %s and every rule of the repository (%s and the "
               "files it names). Update the plan's state. End when your task is finished.\n\nTASK\n\n" % (nm, TOOLS_DIR / "docs" / "AGENT-RULES.md", rules_doc))
    handoff = ("Prüfe nach jedem abgeschlossenen Arbeitsschritt, ob die Datei %s existiert. Wenn ja: schreib den Stand (erledigt, offen, nächster Schritt, "
               "Branch, Worktree) in die State-Datei des Plans unter 'Handoff %s' und beende dich." % (hf, nm))
    if phase == "queen":
        handoff = ("After each completed step check %s. When it exists, do not start new Bees, "
                   "wait for running Bees, finish your current step, record the handoff in the plan state and end." % hf)
    pf.write_text(hdr + str(a["prompt"]) + "\n\n" + handoff, encoding="utf-8")
    predecessors = [a for a in runtime.list_agents(env.conn, env.name, 'all') if a.get('phase') == 'queen' and a.get('plan_path') == plan and a.get('person')]
    ident = agent_identity(env.conn, [predecessors[-1]['person']] if phase == 'queen' and predecessors else ())
    if phase == "bee":
        ident["Person"] = str(a["name"])
    cls = routine.start_class(env, phase, plan, pkg, str(a.get("class") or ""))
    cm = sch.class_model(env.providers, prov, cls)
    bundle = [str(b) for b in a.get("bundle") or []]
    env.update_agent(nm, {"type": protocol, "provider": prov, "plan_path": plan, "package": pkg, "phase": phase, "cwd": str(a["cwd"]),
                          "status": "starting", "started_at": db.now(), "tokens_in": None, "tokens_out": None, "last_line": "queued",
                          "person": ident["Person"], "color": ident["Color"], "model": cm["Model"], "effort": cm["Effort"],
                          "modelShort": sch.model_short(protocol, cm["Model"], cm["Effort"]), "class": cls,
                          "bundle_json": json.dumps(bundle) if bundle else None, "task": agent_task(env, plan, pkg, phase, pf)})
    if phase in ("work", "verify"):
        routine.record_progress(env, nm, plan, pkg, str(a["cwd"]))   # state of the branch at the start, to see later whether the agent committed
    if os.environ.get("BZZ_TEST_NOSTART"):   # tests only: no pane, the fake agent is marked running (this process is always alive)
        env.update_agent(nm, {"status": "running", "pid": os.getpid()})
    else:
        term = c["terminal"] or get_terminal(env.settings.get("terminal", "auto"), str(osplat.runs_dir() / "_panes"))
        spec = {"name": nm, "provider": prov, "protocol": protocol, "plan": plan, "package": pkg, "phase": phase, "cwd": str(a["cwd"]),
                "prompt_file": str(pf), "person": ident["Person"], "model_long": sch.model_long(protocol, cm["Model"], cm["Effort"])}
        launch_agent(env, term, env.settings.get("window", "HaiveQueen"), spec, c["settle"], c["poll"], c["tries"])
    env.log("start %s (%s, %s)" % (nm, prov, phase))
    env.event("%s started (%s)" % (nm, {"codex": "Codex", "claude": "Claude"}.get(prov, prov)))
    if phase != "integrate":
        pk0 = norm_plan(plan)
        if st["plan_status"].get(pk0) == "READY":
            try:
                runtime.set_plan_status(env.conn, env.name, str(st["by_path"][pk0]["path"]), "IN_PROGRESS", str(env.workspace))
                st["plan_status"][pk0] = "IN_PROGRESS"
                env.log("plan %s: READY -> IN_PROGRESS" % plan)
            except runtime.CmdError as exc:
                env.log("plan %s: status change failed: %s" % (plan, exc))
    runtime.kv_delete(env.conn, env.name, "wait:%s|%s" % (plan, pkg))
    if not os.environ.get("BZZ_TEST_NOSTART"):
        time.sleep(c["settle"])
    return 1


def _start_triage(c) -> int:
    """D5: every locked package with open points and no running triage agent gets a triage agent (class medium). Returns the number started."""
    env, pool, st = c["env"], c["pool"], c["state"]
    n = 0
    for item in triage.locked_packages(env.conn, env.name):
        if not runstate.starts_allowed(env.conn) or stop_flag().exists() or pool.workers() <= 0:
            break
        if routine.waiting_retry(env, [view(r) for r in env.agents("all")], item["plan"], item["id"], triage.TRIAGE_PHASE):
            continue   # the last triage agent of this package was interrupted: its retry waits for its time
        pick = sch.select_provider(env.providers, triage.TRIAGE_CLASS, [], pool.free, conn=env.conn)
        if not pick:
            continue
        nm = routine.new_agent_name("%s-%s-triage" % (routine.plan_tag(env, item["plan"]), item["id"]), [view(r) for r in env.agents("all")], st["names"])
        text = triage.triage_prompt(env.name, item["plan"], item["id"], item["points"], osplat.python_executable())
        text = text.replace(" -I haivequeen/cli.py", ' -I "%s"' % (TOOLS_DIR / "haivequeen" / "cli.py"))
        a = {"action": "start_agent", "name": nm, "type": pick["name"], "phase": "triage", "plan": item["plan"], "package": item["id"],
             "cwd": str(env.workspace), "prompt": text, "class": triage.TRIAGE_CLASS, "bundle": []}
        if (norm_plan(a["plan"]) + "|" + a["package"]) not in st["busy"]:
            n += _do_start(c, a, pick)
    for q in triage.questions_to_triage(env.conn, env.name):   # new questions of agents (bzz ask): triage before they reach the owner
        if not runstate.starts_allowed(env.conn) or stop_flag().exists() or pool.workers() <= 0:
            break
        pick = sch.select_provider(env.providers, triage.TRIAGE_CLASS, [], pool.free, conn=env.conn)
        if not pick:
            continue
        plan, pkg = str(q["plan_path"] or routine.QUEUE_PLAN_ID), triage.QUESTION_PKG % q["id"]
        nm = routine.new_agent_name("%s-%s-triage" % (routine.plan_tag(env, plan), pkg), [view(r) for r in env.agents("all")], st["names"])
        text = triage.question_prompt(env.conn, env.name, q, osplat.python_executable())
        text = text.replace(" -I haivequeen/cli.py", ' -I "%s"' % (TOOLS_DIR / "haivequeen" / "cli.py"))
        a = {"action": "start_agent", "name": nm, "type": pick["name"], "phase": "triage", "plan": plan, "package": pkg,
             "cwd": str(env.workspace), "prompt": text, "class": triage.TRIAGE_CLASS, "bundle": []}
        if (norm_plan(a["plan"]) + "|" + a["package"]) not in st["busy"]:
            n += _do_start(c, a, pick)
    return n


# ---- orchestrator pane (QUEEN): fixed header and one line per event ----

ESC = "\x1b"
SGR = {"Cyan": "1;36", "Gray": "90", "White": "97", "Green": "32", "Red": "31", "Yellow": "33"}


def wrap_text(s: str, w: int) -> list:
    """Word-wrap a text to the width w (long words are split); returns the lines."""
    out = []
    for para in s.splitlines() or [""]:
        cur = ""
        for word in para.split(" "):
            while len(word) > w:
                if cur:
                    out.append(cur)
                    cur = ""
                out.append(word[:w])
                word = word[w:]
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= w:
                cur += " " + word
            else:
                out.append(cur)
                cur = word
        out.append(cur)
    return out


def wrap_parts(parts: list, w: int) -> list:
    """Wrap a list of text parts to the width w; a break only happens between two parts."""
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > w:
            out.append(cur)
            cur = ""
        if len(p) > w:
            out += wrap_text(p.strip(), w)
            continue
        cur += p
    if cur:
        out.append(cur)
    return out


def head_lines(width: int, tot: dict, ui: dict) -> list:
    """The fixed header of the orchestrator pane as (text, colour) pairs.

    `tot` is brain.totals(); `ui` holds stop (bool), run_state, brain (bool), busy (bool), since and next_at (datetimes or None), model (text) and projects (names).
    """
    res = []
    if ui.get("stop"):
        state = "‖ STOP set, no new agents"
    elif ui.get("run_state", "RUNNING") != "RUNNING":
        state = ui["run_state"]
    elif ui.get("brain"):
        state = "brain call running"
    elif ui.get("busy"):
        state = "round running"
    elif ui.get("next_at"):
        state = "idle since %s, ◔ next round %s" % (ui["since"].strftime("%H:%M"), ui["next_at"].strftime("%H:%M"))
    else:
        state = "starting"
    for ln in wrap_text("◆ QUEEN | Claude %s | %s" % (ui.get("model", ""), state), width):
        res.append((ln, "White"))
    res.append(("Projects: " + (", ".join(ui.get("projects") or []) or "none") + ". Decides only on events, the routine runs without a brain", "Gray"))
    lc = tot.get("LastCall")
    last = (["Last call: context %s | " % sch.format_tokens(lc["context"]), "Total input %s / Output %s" % (sch.format_tokens(lc["tokens_in"]), sch.format_tokens(lc["tokens_out"]))]
            if lc else ["Last call: none yet"])
    res += [(ln.rstrip(" |"), "Gray") for ln in wrap_parts(last, width)]
    res += [(ln.rstrip(" |"), "Gray") for ln in wrap_parts(
        ["Today: Total input %s / Output %s | " % (sch.format_tokens(tot["Input"]), sch.format_tokens(tot["Output"])), "Brain calls today %d" % tot["Calls"]], width)]
    return res


class Pane:
    """Draws the header of the orchestrator pane and prints new event lines of all projects (ANSI terminal)."""

    def __init__(self, conn, envs, out=None):
        """Remember the connection and projects; positions in the event mirror files start at their current end."""
        self.conn, self.envs, self.out = conn, envs, out or sys.stdout
        if self.out.isatty():
            header.enable_vt()   # the cursor moves of the fixed header need escape processing (pwsh switched it on by itself)
        self.rows = 0
        self.head_at = 0.0
        self.pos = {}
        for e in envs:
            self.pos[e.name] = self._count(e)

    def _file(self, env) -> Path:
        """Events mirror file of a project."""
        return runtime.mirror_dir(env.name) / runtime.MIRRORS["events"]

    def _count(self, env) -> int:
        """Number of lines in the events mirror file."""
        try:
            return len(self._file(env).read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def width(self) -> int:
        """Pane width in columns."""
        return max(30, os.get_terminal_size().columns if self.out.isatty() else 100)

    def height(self) -> int:
        """Pane height in rows."""
        return max(10, os.get_terminal_size().lines if self.out.isatty() else 30)

    def draw_head(self, ui: dict) -> int:
        """Redraw the header in the top rows and keep the scroll region below it; the height is reserved for the worst case, so it never grows."""
        w, h = self.width(), self.height()
        if self.out.isatty():
            w = max(10, os.get_terminal_size().columns)   # the real width: 30 columns drawn into a narrower pane wrapped into the next rows
        hl = head_lines(w, brain.totals(self.conn), ui)
        if not self.rows:
            big = {"Calls": 99, "Input": 99999999, "Output": 9999999,
                   "LastCall": {"context": 999999, "tokens_in": 99999999, "tokens_out": 9999999}}
            self.rows = len(head_lines(w, big, dict(ui, next_at=datetime.now(), since=datetime.now(), stop=False, brain=False, busy=False)))
        while len(hl) < self.rows:
            hl.append(("", "White"))
        buf = [ESC + "7", ESC + "[?7l", "%s[%d;%dr" % (ESC, len(hl) + 1, h)]   # ?7l: a header line longer than the pane is cut, never wrapped into the next row
        for i, (t, c) in enumerate(hl):
            buf.append("%s[%d;1H%s[2K%s[%sm%s%s[0m" % (ESC, i + 1, ESC, ESC, SGR.get(c, "0"), t, ESC))
        buf.append(ESC + "[?7h" + ESC + "8")
        self.out.write("".join(buf))
        self.out.flush()
        self.head_at = time.time()
        return len(hl)

    def maybe_head(self, ui: dict) -> None:
        """Redraw the header at most every 5 seconds."""
        if time.time() - self.head_at >= 5:
            self.draw_head(ui)

    def show_new_events(self) -> None:
        """Print the new event lines (time, project, text), wrapped instead of cut, errors red."""
        for env in self.envs:
            try:
                lines = self._file(env).read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for ln in lines[self.pos.get(env.name, 0):]:
                p = ln.split("|", 2)
                if len(p) < 3:
                    continue
                first = True
                for part in wrap_text("[%s] %s" % (env.name, p[2]), self.width() - 7):
                    self.out.write("%s[%sm%s%s%s[0m\n" % (ESC, "31" if p[1] == "E" else "97", (p[0] + "  ") if first else "       ", part, ESC))
                    first = False
            self.pos[env.name] = len(lines)
        self.out.flush()


def active_count(conn) -> int:
    """Number of agents (any project) that are starting or running."""
    return runstate.running_agents(conn)


def run_loop(conn, machine: dict, rounds: int = 0, round_minutes: float = 0, ui: bool = False, out=None, sleep=time.sleep,
             wait_seconds: float = 2.0, round_kwargs: dict = None) -> int:
    """Run rounds until the STOP flag exists (or `rounds` rounds are done), then wait for the running agents and end.

    Only one loop runs at a time: with a living lock the call ends at once. At the start running agents are re-attached by process id (D8): living
    ones stay running, vanished ones fail by the existing rules. `round_kwargs` go to run_round (tests). Returns the exit code.
    """
    other = _take_pid_lock(lock_file(), own_ok=True)   # atomic: of two loops started at the same moment exactly one runs
    if other:
        print("Another orchestrator loop runs (pid %d): this one ends." % other)
        return 0
    try:
        start_pending_file().unlink()
    except OSError:
        pass
    try:
        return _run_loop(conn, machine, rounds, round_minutes, ui, out, sleep, wait_seconds, round_kwargs or {})
    finally:
        try:
            lock_file().unlink()
        except OSError:
            pass
        if stop_flag().exists():   # `bzz stop`: the loop has ended, so the Overview pane and the panes of ended agents go; the Queen pane last
            close_panes_after_stop(conn)


def daily_export(envs) -> None:
    """Export the database when the daily export (03:30, C4) is due; a failure is logged and never stops the loop."""
    try:
        if export.export_due(datetime.now()):
            res = export.run_export()
            for env in envs[:1]:
                env.log("database exported to %s" % res["file"])
    except Exception as exc:
        for env in envs[:1]:
            env.log("EXPORT ERROR %s" % exc)


def _run_loop(conn, machine, rounds, round_minutes, ui, out, sleep, wait_seconds, round_kwargs) -> int:
    """The loop body (see run_loop)."""
    envs = sch.load_projects(conn, machine)
    minutes = round_minutes if round_minutes else float(machine.get("roundMinutes", 5))
    state = {"since": datetime.now(), "next_at": None, "busy": False, "brain": False, "model": "", "projects": [e.name for e in envs]}
    if envs:
        state["model"] = sch.model_short("claude", str((envs[0].settings.get("brain") or {}).get("model", "sonnet")), "medium")
    pane = Pane(conn, envs, out) if ui else None

    def refresh() -> None:
        """Update the header values from the stored state and redraw."""
        rs = runstate.get_state(conn)["state"]
        ls = read_state(conn) or {}
        state.update(stop=stop_flag().exists(), run_state=rs, brain=ls.get("status") == "brain")
        if pane:
            pane.show_new_events()
            pane.maybe_head(state)

    for env in envs:
        res = reattach.reattach(conn, env.name, osplat.process_alive)
        if res["kept"] or res["failed"]:
            env.log("restart: re-attached %d running agent(s), %d gone" % (len(res["kept"]), len(res["failed"])))
        env.log("loop started (roundMinutes=%s)" % minutes)
        env.event("Queen started (round every %s minutes)" % minutes)
    if pane:
        pane.out.write("%s[2J%s[3J%s[H" % (ESC, ESC, ESC))
        pane.draw_head(state)
        pane.out.write("%s[%d;1H" % (ESC, pane.rows + 1))
    done = 0
    while not stop_flag().exists() and (not rounds or done < rounds):
        state.update(busy=True)
        write_state(conn, "round", datetime.now(), None)
        if pane:
            pane.draw_head(state)
        try:
            run_round(conn, machine, envs, **round_kwargs)
        except Exception as exc:   # a broken round must never stop the loop
            for env in envs:
                env.log("ROUND ERROR %s" % exc)
                env.event("Round error: %s" % exc, True)
        daily_export(envs)
        done += 1
        nxt = datetime.fromtimestamp(time.time() + minutes * 60)
        state.update(busy=False, since=datetime.now(), next_at=nxt)
        write_state(conn, "idle", state["since"], nxt)
        refresh()
        if rounds and done >= rounds:
            break
        from . import hives
        wake = hives.wake_marker(conn, envs)
        end = time.time() + minutes * 60
        while time.time() < end and not stop_flag().exists():
            sleep(wait_seconds)
            runstate.settle(conn)
            refresh()
            if hives.wake_marker(conn, envs) != wake:
                break
    stopped = stop_flag().exists()
    for env in envs:
        env.log("STOP found, loop ending" if stopped else "loop ending")
        env.event("STOP file found: no new agents, waiting for running agents" if stopped else "Loop ending")
    state.update(next_at=None)
    write_state(conn, "idle", datetime.now(), None)
    if pane:
        pane.show_new_events()
    while active_count(conn) > 0:
        sleep(wait_seconds)
        for env in envs:   # a row whose runner process is gone must not hold the stopped loop open
            routine.repair_stale_agents(env)
        runstate.settle(conn)
        refresh()
    for env in envs:
        env.log("loop ended")
        env.event("Loop ended: all agents are done")
    if pane:
        pane.show_new_events()
        pane.draw_head(state)
    return 0


# ---- command line: bzz start / bzz stop ----

def start_boot_command(args) -> list:
    """Command line of the Queen pane: `start --foreground` with the terminal, --rounds and --round-minutes of this call (so a limited run ends after its rounds)."""
    cmd = [osplat.python_executable(), str(TOOLS_DIR / "haivequeen" / "cli.py"), "start", "--foreground"]
    if args.terminal:
        cmd += ["--terminal", args.terminal]
    if args.rounds:
        cmd += ["--rounds", str(args.rounds)]
    if args.round_minutes:
        cmd += ["--round-minutes", str(args.round_minutes)]
    if getattr(args, "keep_pause", False):
        cmd += ["--keep-pause"]
    return cmd


def cmd_start(args, conn) -> int:
    """`bzz start`: run the loop in this process (--foreground) or open the orchestrator window whose first pane runs it."""
    from . import config
    machine = config.load_config()
    pid = loop_running()
    if pid:
        print(json.dumps({"ok": True, "running": True, "pid": pid, "note": "The orchestrator is already running."}))
        return 0
    try:
        if "(self-update)" in stop_flag().read_text(encoding="utf-8", errors="replace"):   # an older self-update restarts without --keep-pause
            args.keep_pause = True
    except OSError:
        pass
    try:
        stop_flag().unlink()
    except OSError:
        pass
    if not getattr(args, "keep_pause", False):   # the self-update restart keeps a pause
        runstate.resume(conn, "bzz start")   # a pause or drain of the run before must not keep the new run from starting agents
    if args.foreground:
        return run_loop(conn, machine, args.rounds, args.round_minutes, ui=sys.stdout.isatty())
    settings = sch.build_settings(machine, {})
    term = get_terminal(args.terminal or settings.get("terminal", "auto"), str(osplat.runs_dir() / "_panes"))
    window = settings.get("window", "HaiveQueen")
    boot = start_boot_command(args)
    title = queen_pane_title(sch.model_long("claude", "claude-sonnet-5-5", "medium"), is_test_runtime())
    with pane_lock():   # a second `bzz start` waits here and then finds the loop (or the pending start) of the first
        pid = loop_running()
        if pid:
            print(json.dumps({"ok": True, "running": True, "pid": pid, "note": "The orchestrator is already running."}))
            return 0
        if start_pending():
            print(json.dumps({"ok": True, "running": False, "starting": True, "note": "The orchestrator window is just starting."}))
            return 0
        start_pending_file().parent.mkdir(parents=True, exist_ok=True)
        start_pending_file().write_text(str(os.getpid()), encoding="ascii")
        try:
            import importlib.util
            dash = None
            if importlib.util.find_spec("haivequeen.dashboard") is not None:
                dash = [osplat.python_executable(), "-c", "import sys;sys.path.insert(0,sys.argv[1]);from haivequeen.dashboard import main;sys.exit(main(sys.argv[2:]))", str(TOOLS_DIR)]
            panes = open_session_panes(term, window, title, boot, dash)
        except Exception:
            start_pending_file().unlink()
            raise
    print(json.dumps({"ok": True, "window": window, "panes": panes}))
    return 0


def cmd_stop(args, conn) -> int:
    """`bzz stop`: create the STOP flag; running agents finish their task, no new agent starts, the loop ends."""
    stop_flag().parent.mkdir(parents=True, exist_ok=True)
    stop_flag().write_text("stop requested %s\n" % datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    closed = [] if loop_running() else close_panes_after_stop(conn)   # a running loop closes the panes itself when it ends
    print(json.dumps({"ok": True, "running_agents": active_count(conn), "closed_panes": closed}))
    return 0


def register(sub) -> None:
    """Add the commands start and stop to the sub-parser collection of haivequeen/cli.py."""
    p = sub.add_parser("start")
    p.add_argument("--foreground", action="store_true", help="run the loop in this process")
    p.add_argument("--terminal", choices=["auto", "wt", "tmux", "headless"])
    p.add_argument("--rounds", type=int, default=0, help="end after this many rounds (0 = until stopped)")
    p.add_argument("--round-minutes", type=float, default=0)
    p.add_argument("--keep-pause", action="store_true", help="do not lift a pause or drain of the run before (used by the self-update)")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("stop")
    p.set_defaults(fn=cmd_stop)
