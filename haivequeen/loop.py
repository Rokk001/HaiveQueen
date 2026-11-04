"""Round loop: one loop over all registered projects (D1, D4) with shared slots, limits, RAM and build jobs.

Ports round.ps1 and round-loop.ps1 onto the database. A round syncs the plans of every project, orders the projects (priority first, ties by
the oldest waiting package), lets the routine decide what needs no language model, asks the brain only for events that need judgement,
validates every action (rules 11 and others) and starts agents as hidden background processes. A pause (D8) skips the starts,
a lock starts a triage agent (D5), and at the start running agents are re-attached by process id.
The agent process itself is `haivequeen.agent_runner` (see docs/S13-S14-INTERFACE.md).
"""
import faulthandler
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, events, export, faults, inbox, profile, reattach, roles, routine, runstate, runtime, usage, views
from . import platform as osplat
from . import scheduler as sch
from .routine import view
from .scheduler import ACTIVE, norm_plan

TOOLS_DIR = Path(__file__).resolve().parent.parent
PHASES = ("work", "verify", "release", "integrate")
TEMPORARY = re.compile(r"^(no worker slot|no free slot|no integrator slot|free RAM|STOP flag|run state)")   # a start rejected for one of these is tried again next round
AGENT_NAMES = ["Ada", "Bea", "Cleo", "Dina", "Ella", "Fia", "Gina", "Hana", "Ida", "Juna", "Kira", "Lea", "Mia", "Nora", "Ola",
               "Pia", "Rita", "Sara", "Tara", "Uma", "Vera", "Wilma", "Yara", "Zoe",
               "Anna", "Bella", "Clara", "Dora", "Eva", "Frida", "Greta", "Heidi", "Ines", "Jana", "Katja", "Lena", "Maja", "Nina", "Olga", "Paula",
               "Rosa", "Sofia", "Tessa", "Ulla", "Vivi", "Wanda", "Xenia", "Zora"]   # short first names of the agents; the main agent is called Queen and is not in this list
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


def start_pending_file() -> Path:
    """Path of the marker that a window start is under way (the loop of that window has not taken its lock yet)."""
    return osplat.data_dir() / "start.pending"


def start_pending(max_age: float = 120.0) -> bool:
    """True when a window start began less than `max_age` seconds ago."""
    try:
        return time.time() - start_pending_file().stat().st_mtime < max_age
    except OSError:
        return False


def start_lock_file() -> Path:
    """Path of the start lock (the process id of the `bzz start` that is starting the loop right now)."""
    return osplat.data_dir() / "start.lock"


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


# ---- loop state (kv key loop-state, machine wide) ----

# Write the loop state for the dashboard.
def write_state(conn, status: str, since: datetime, next_at) -> None:
    """Write the loop state for the dashboard."""
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
    from . import bees
    embedded_names = bees.open_embedded_names(conn)   # a Bee that runs as a subagent holds its name too
    used_n = [str(a.get("person")) for a in rows] + list(excluded_names) + list(embedded_names)
    used_c = [str(a.get("color")) for a in rows]
    run_c = [str(a.get("color")) for a in rows if a.get("status") in ACTIVE]
    running_names = {str(a.get("person")) for a in rows if a.get("status") in ACTIVE} | embedded_names
    n = (next((x for x in AGENT_NAMES if x not in used_n), None)
         or next((x for x in AGENT_NAMES if x not in running_names and x not in excluded_names), None)
         or min(AGENT_NAMES, key=lambda x: sum(1 for a in rows if str(a.get("person")) == x and a.get("status") in ACTIVE)))   # all taken: the least used one
    # Colours of agents that ended within the last hour are given up first; a colour of a running agent repeats only with more running agents than colours (then the least used one).
    # The second shade of a hue is only taken when every hue is in use by a running agent: colours are compared by hue family (index modulo 6), not by exact value.
    run_hues = {AGENT_COLOURS.index(x) % 6 for x in run_c if x in AGENT_COLOURS}
    free_hue = [x for x in AGENT_COLOURS if AGENT_COLOURS.index(x) % 6 not in run_hues]
    c = (next((x for x in free_hue if x not in used_c), None) or (free_hue[0] if free_hue else None)
         or next((x for x in AGENT_COLOURS if x not in used_c), None) or next((x for x in AGENT_COLOURS if x not in run_c), None)
         or min(AGENT_COLOURS, key=run_c.count))
    return {"Person": n, "Color": c}


# ---- starting an agent as a hidden background process ----

def forward_env_args() -> list:
    """`--env NAME=VALUE` arguments for the runner: the test and folder overrides the loop was started with."""
    out = []
    for k in FORWARD_ENV:
        if os.environ.get(k):
            out += ["--env", "%s=%s" % (k, os.environ[k])]
    return out


# Command line of the agent process. Arguments are documented in docs/S13-S14-INTERFACE.md.
def runner_argv(env, spec: dict) -> list:
    """Command line of the agent process. Arguments are documented in docs/S13-S14-INTERFACE.md.

    The module is started through a small bootstrap, so it works from any folder: python -c "<add tools folder to sys.path; call haivequeen.agent_runner.main(argv)>".
    The run folder is <data>/runs/<project>/<name> (--runs-dir = <data>/runs/<project>), the folder the routine reads final.txt from.
    """
    boot = "import sys;sys.path.insert(0,sys.argv[1]);from haivequeen.agent_runner import main;sys.exit(main(sys.argv[2:]))"
    options = ["--project", env.name, "--name", spec["name"], "--provider", spec["provider"], "--protocol", spec["protocol"],
               "--plan", spec["plan"], "--package", spec["package"], "--phase", spec["phase"], "--cwd", spec["cwd"],
               "--prompt-file", spec["prompt_file"], "--runs-dir", str(env.run_dir(spec["name"]).parent)]
    # An empty argument (a Queen has no package) is left out; the runner defaults it.
    pairs = [options[i:i + 2] for i in range(0, len(options), 2)]
    return [osplat.python_executable(), "-c", boot, str(TOOLS_DIR)] + [a for pair in pairs if pair[1] != "" for a in pair] + forward_env_args()


# Start the runner of any agent (Queen, Bee, verify, release, integrate, triage) as a hidden background process.
def launch_headless(env, spec: dict) -> None:
    """Start the runner of an agent as its own hidden process; its stdout and stderr go to runner.log in the run folder and the runner sets the pid in the agent row.

    The live text of the agent (what the graphical dashboard shows) is written by the runner itself to milestones.txt in the same folder."""
    log = open(env.run_dir(spec["name"]) / "runner.log", "ab")
    flags = {"creationflags": 0x08000000 | 0x00000200} if osplat.is_windows() else {"start_new_session": True}   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (hidden console: its children open no windows)
    try:
        subprocess.Popen(runner_argv(env, spec), stdin=subprocess.DEVNULL, stdout=log, stderr=log, **flags)
    finally:
        log.close()


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


# Track failed releases and serialize threads before recovering a lock owned by this process.
_hive_schedule_guard = threading.Lock()
_abandoned_hive_locks = set()


# Serialize Hive scheduling between the loop and owner commands.
@contextmanager
def hive_schedule_lock(timeout: float = 30.0):
    """Serialize Hive scheduling and recover an owned lock after a failed release."""
    path = osplat.data_dir() / "hive-schedule.lock"
    deadline = time.monotonic() + timeout
    if not _hive_schedule_guard.acquire(timeout=max(0.0, timeout)):
        raise TimeoutError("Hive scheduling is busy")
    try:
        while True:
            if path in _abandoned_hive_locks:
                try:
                    if path.read_text(encoding="ascii").strip() == str(os.getpid()):
                        path.unlink()
                    _abandoned_hive_locks.discard(path)
                except FileNotFoundError:
                    _abandoned_hive_locks.discard(path)
                except OSError:
                    pass
            if _take_pid_lock(path, own_ok=False) == 0:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Hive scheduling is busy")
            time.sleep(0.1)
        try:
            yield
        finally:
            try:
                if path.exists() and path.read_text(encoding="ascii").strip() == str(os.getpid()):
                    path.unlink()
                _abandoned_hive_locks.discard(path)
            except OSError:
                _abandoned_hive_locks.add(path)
                raise
    finally:
        _hive_schedule_guard.release()


# Clean integrated worktrees and schedule one round of Hives across every registered project.
# Refresh provider metadata outside the lock before scheduling real work.
def run_round(conn, machine: dict, envs=None, dry_run=False, plan_files=None,
              out_file=None, settle: float = 3.0, free_ram=None, alive=None) -> dict:
    """Clean integrated worktrees and schedule one round across registered projects."""
    from . import hives
    envs = envs if envs is not None else sch.load_projects(conn, machine)
    if not dry_run:
        # Fetch account metadata before taking the Hive scheduling lock.
        usage.refresh_claude_usage(conn, sch.get_providers(machine))
        profile.notify_missing(conn, sch.get_providers(machine))
        from . import worktrees
        for env in envs:
            try:
                env.log('Worktree cleanup: ' + json.dumps(worktrees.prune(env), ensure_ascii=False))
            except Exception as exc:
                env.log('Worktree cleanup failed: ' + str(exc))
    with hive_schedule_lock():
        if dry_run:
            import sqlite3
            snapshot = sqlite3.connect(":memory:", isolation_level=None)
            snapshot.row_factory = sqlite3.Row
            try:
                conn.backup(snapshot)
                copied = [sch.ProjectEnv(snapshot, e.name, e.workspace, e.settings, e.colour, e.priority, e.plan_globs, e.plan_exclude) for e in envs]
                result = hives.schedule_round(snapshot, machine, copied, dry_run=True, free_ram=free_ram, alive=alive,
                                              settle=settle, plan_files=plan_files)
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
        return hives.schedule_round(conn, machine, envs, dry_run=False, free_ram=free_ram, alive=alive,
                                    settle=settle, plan_files=plan_files)


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


# Action start_agent: validate, write the prompt and the agent row, start the runner. Returns 1 when an agent was started.
def _do_start(c, a, forced_pick=None) -> int:
    """Action start_agent: validate, write the prompt and the agent row, start the runner. Returns 1 when an agent was started.

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
        hdr = roles.role_text("bee-start", {"language": roles.language(env.settings)}) + "\n\n"
    elif phase == "queen":
        hdr = roles.role_text("queen-start", {"language": roles.language(env.settings)}) + "\n\n"
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
    predecessors = [x for x in runtime.list_agents(env.conn, env.name, 'all') if x.get('phase') == 'queen' and x.get('plan_path') == plan and x.get('person')]
    loaded = ""
    if phase in ("bee", "queen"):   # what a Queen and a Bee load is defined in profile.py; the project rules are complete, never a selection
        if protocol != "claude":   # Claude gets the rules in its system prompt file (rules.md); every other provider reads them from its prompt
            loaded = profile.rules_text(profile.project_rules(env.settings, env.code_repo, protocol, a["cwd"]))
        if phase == "queen":
            loaded += "\n" + profile.queen_load(env.conn, env.name, env.workspace, plan, predecessors[-1]["person"] if predecessors else None)
    pf.write_text(hdr + str(a["prompt"]) + "\n\n" + handoff + ("\n\n" + loaded.strip() + "\n" if loaded.strip() else ""), encoding="utf-8")
    ident = agent_identity(env.conn, [predecessors[-1]['person']] if phase == 'queen' and predecessors else ())
    if phase in ("bee", "queen"):   # a Bee and a Queen are named by their short female name (A2); the number of a repeated row key is not part of it
        base = re.sub(r"-\d+$", "", nm)
        ident["Person"] = base if base in AGENT_NAMES else nm
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
    if os.environ.get("BZZ_TEST_NOSTART"):   # tests only: no runner process, the fake agent is marked running (this process is always alive)
        env.update_agent(nm, {"status": "running", "pid": os.getpid()})
    else:
        spec = {"name": nm, "provider": prov, "protocol": protocol, "plan": plan, "package": pkg, "phase": phase, "cwd": str(a["cwd"]),
                "prompt_file": str(pf)}
        launch_headless(env, spec)   # every agent (Queen, Bee, verify, release, integrate, triage) starts the same way
    env.log("start %s (%s, %s)" % (nm, prov, phase))
    env.event("%s started (%s)" % (ident["Person"] if phase in ("bee", "queen") else nm, {"codex": "Codex", "claude": "Claude"}.get(prov, prov)))
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


def active_count(conn) -> int:
    """Number of agents (any project) that are starting or running."""
    return runstate.running_agents(conn)


# Return the Windows boot time to distinguish a restart from a loop failure.
def system_boot_time():
    """Return the system boot time on Windows, or None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return datetime.now() - timedelta(milliseconds=ctypes.windll.kernel32.GetTickCount64())


# Log an unexpected loop end and file its deduplicated fault.
def report_loop_failure(conn, text: str) -> None:
    """Log an unexpected loop end, emit an error event and file its deduplicated fault."""
    runtime.append_event(conn, "HaiveQueen", text, mirror="rounds", error=True)
    runtime.append_event(conn, "HaiveQueen", text, mirror="events", error=True)
    runtime.kv_set(conn, sch.GLOBAL, "loop-unexpected-end-at", datetime.now().isoformat(timespec="seconds"))
    faults.file_fault(conn, "HaiveQueen", "loop_vanished", text)


# Run rounds until the STOP flag exists (or `rounds` rounds are done), then wait for the running agents and end.
def run_loop(conn, machine: dict, rounds: int = 0, round_minutes: float = 0, sleep=time.sleep,
             wait_seconds: float = 2.0, round_kwargs: dict = None) -> int:
    """Run rounds until the STOP flag exists (or `rounds` rounds are done), then wait for the running agents and end.

    Only one loop runs at a time: with a living lock the call ends at once. At the start running agents are re-attached by process id (D8): living
    ones stay running, vanished ones fail by the existing rules. `round_kwargs` go to run_round (tests). Returns the exit code.
    """
    vanished = None
    try:
        pid = int(lock_file().read_text(encoding="ascii").strip())
        last = read_state(conn) or {}
        heartbeat = db.parse_time(runtime.kv_get(conn, sch.GLOBAL, "loop-heartbeat") or last.get("since") or "")
        boot = system_boot_time()
        restarted = boot is not None and heartbeat is not None and boot > heartbeat
        if not restarted and not osplat.process_alive(pid) and not stop_flag().exists() and last.get("status") != "ended":
            vanished = "Loop ended unexpectedly (pid %d, last state %s since %s)" % (pid, last.get("status"), last.get("since"))
    except (OSError, ValueError):
        pass
    other = _take_pid_lock(lock_file(), own_ok=True)   # atomic: of two loops started at the same moment exactly one runs
    if other:
        print("Another orchestrator loop runs (pid %d): this one ends." % other)
        return 0
    try:
        start_pending_file().unlink()
    except OSError:
        pass
    crash = None
    handler_enabled = faulthandler.is_enabled()
    try:
        crash_path = osplat.data_dir() / ("crash-%s.txt" % datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        crash = crash_path.open("a", encoding="utf-8")
        faulthandler.enable(file=crash)
        if vanished:
            crash.write(vanished + "\n")
            crash.flush()
            report_loop_failure(conn, vanished)
        runtime.kv_set(conn, sch.GLOBAL, "loop-started-at", datetime.now().isoformat(timespec="seconds"))
        try:
            result = _run_loop(conn, machine, rounds, round_minutes, sleep, wait_seconds, round_kwargs or {})
        except Exception:
            text = traceback.format_exc()
            crash.write(text)
            crash.flush()
            report_loop_failure(conn, "Loop ended unexpectedly\n" + text)
            raise
        write_state(conn, "ended", datetime.now(), None)
        return result
    finally:
        if crash:
            faulthandler.disable()
            if handler_enabled:
                faulthandler.enable()
            crash.close()
        try:
            lock_file().unlink()
        except OSError:
            pass


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


# Track only machine/project TOML and registered project changes.
def configuration_stamp(conn):
    """Return exact TOML contents and registry values for change detection."""
    projects = tuple(tuple(row) for row in conn.execute(
        "SELECT name,workspace,colour,priority FROM projects ORDER BY name"))
    paths = [config.config_dir() / "config.toml"]
    paths += [Path(row[1]) / ".haivequeen" / "project.toml" for row in projects if row[1]]
    files = []
    for path in paths:
        try:
            value = path.read_bytes()
        except FileNotFoundError:
            value = None
        except OSError:
            value = ("unreadable",)
        files.append((str(path), value))
    return projects, tuple(files)


# The loop body (see run_loop).
# Keep provider quotas current during idle waits as well as scheduling rounds.
# Apply valid TOML changes at safe scheduling boundaries without restarting agents.
def _run_loop(conn, machine, rounds, round_minutes, sleep, wait_seconds, round_kwargs) -> int:
    """The loop body (see run_loop)."""
    envs = sch.load_projects(conn, machine)
    minutes = round_minutes if round_minutes else float(machine.get("roundMinutes", 5))
    state = {"since": datetime.now(), "next_at": None, "busy": False, "projects": [e.name for e in envs]}

    applied_stamp = configuration_stamp(conn)
    seen_stamp = None

    # Validate the entire changed snapshot before replacing active settings.
    def reload_settings() -> bool:
        """Keep the last good snapshot on invalid edits; report each edit once."""
        nonlocal machine, envs, minutes, applied_stamp, seen_stamp
        stamp = configuration_stamp(conn)
        if stamp == seen_stamp:
            return False
        seen_stamp = stamp
        try:
            old_files = dict(applied_stamp[1])
            for path, metadata in stamp[1]:
                if metadata == ("unreadable",) or metadata is None and old_files.get(path) is not None:
                    raise OSError("TOML file unavailable")
            candidate = config.load_config() if stamp[1][0][1] is not None else machine
            sch.get_providers(candidate)
            int(candidate.get("hives", 2))
            candidate_minutes = round_minutes if round_minutes else float(candidate.get("roundMinutes", 5))
            if not math.isfinite(candidate_minutes) or candidate_minutes <= 0:
                raise ValueError("invalid round interval")
            candidates = sch.load_projects(conn, candidate)
            for env in candidates:
                if not math.isfinite(_min_ram(env)):
                    raise ValueError("invalid RAM limit")
                for provider in env.providers:
                    for cls in sch.CLASS_NAMES:
                        sch.context_limit(env.settings, env.providers, provider["name"], cls)
        except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
            for env in envs:
                env.event("TOML configuration reload failed (%s); keeping last valid settings." % type(exc).__name__, True)
            return False
        if configuration_stamp(conn) != stamp:
            seen_stamp = None
            return False
        fields = ("name", "workspace", "settings", "colour", "priority", "plan_globs", "plan_exclude")
        previous = [tuple(getattr(env, key) for key in fields) for env in envs]
        updated = [tuple(getattr(env, key) for key in fields) for env in candidates]
        applied_stamp = stamp
        if candidate == machine and previous == updated:
            return False
        machine, envs, minutes = candidate, candidates, candidate_minutes
        state["projects"] = [env.name for env in envs]
        for env in envs:
            env.log("TOML configuration reloaded; running agents retained.")
        return True

    # Update the loop state values from the stored state.
    def refresh() -> None:
        """Update the loop state values from the stored state."""
        runtime.kv_set(conn, sch.GLOBAL, "loop-heartbeat", datetime.now().isoformat(timespec="seconds"))
        rs = runstate.get_state(conn)["state"]
        state.update(stop=stop_flag().exists(), run_state=rs)

    # Refresh during waits without letting a transient failure stop the loop.
    # Wake scheduling when a valid configuration change is accepted.
    def wait_refresh() -> bool:
        """Reload settings and refresh; return whether accepted settings changed."""
        changed = reload_settings()
        for action in (lambda: usage.refresh_claude_usage(conn, sch.get_providers(machine)), refresh):
            try:
                action()
            except Exception as exc:
                for env in envs:
                    env.log("ROUND ERROR %s" % exc)
                    env.event("Round error: %s" % exc, True)
        return changed

    for env in envs:
        res = reattach.reattach(conn, env.name, osplat.process_alive)
        from . import hives
        hives.repair_technical_ends(conn, env.name)
        conn.execute("UPDATE hives SET state='waiting',claimed_at=NULL WHERE project=? AND state='starting' AND NOT EXISTS (SELECT 1 FROM agents WHERE agents.project=hives.project AND agents.plan_path=hives.plan_path AND agents.phase='queen' AND agents.archived=0 AND agents.status IN ('running','starting'))", (env.name,))
        if res["kept"] or res["failed"]:
            env.log("restart: re-attached %d running agent(s), %d gone" % (len(res["kept"]), len(res["failed"])))
        env.log("loop started (roundMinutes=%s)" % minutes)
        env.event("Beekeeper started (round every %s minutes)" % minutes)
    done = 0
    while not stop_flag().exists() and (not rounds or done < rounds):
        reload_settings()
        state.update(busy=True)
        write_state(conn, "round", datetime.now(), None)
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
            changed = wait_refresh()
            if changed or hives.wake_marker(conn, envs) != wake:
                break
    stopped = stop_flag().exists()
    for env in envs:
        env.log("STOP found, loop ending" if stopped else "loop ending")
        env.event("STOP file found: no new agents, waiting for running agents" if stopped else "Loop ending")
    state.update(next_at=None)
    write_state(conn, "idle", datetime.now(), None)
    while active_count(conn) > 0:
        sleep(wait_seconds)
        for env in envs:   # a row whose runner process is gone must not hold the stopped loop open
            routine.repair_stale_agents(env)
        wait_refresh()
    for env in envs:
        env.log("loop ended")
        env.event("Loop ended: all agents are done")
    return 0


# ---- command line: bzz start / bzz stop ----

def spawn_detached(argv: list, log: Path, env: dict = None) -> None:
    """Start `argv` as a process of its own without a visible window; its output is appended to the file `log`. `env` adds environment variables.

    On Windows the process gets a hidden console (CREATE_NO_WINDOW), not none: every program it starts (git, claude, taskkill) inherits that console,
    while a process without any console makes Windows open a new console window for each of them."""
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = {"creationflags": 0x08000000 | 0x00000200} if osplat.is_windows() else {"start_new_session": True}   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as out:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env={**os.environ, **env} if env else None, **flags)


# Print the command result and retain it for optional desktop feedback.
def command_result(args, payload) -> int:
    """Emit the existing JSON protocol and expose its result to the launcher."""
    args.desktop_result = payload
    print(json.dumps(payload))
    return 0


# Serialise `bzz start` between processes.
@contextmanager
def start_lock(timeout: float = 120.0, poll: float = 0.1):
    """Serialise `bzz start` between processes: only the holder of start.lock starts the loop and the window.

    Waits up to `timeout` seconds for another start to finish, then raises TimeoutError.
    """
    deadline = time.monotonic() + timeout
    while True:
        holder = _take_pid_lock(start_lock_file(), own_ok=False)
        if holder == 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("another start is running (process %s)" % holder)
        time.sleep(poll)
    try:
        yield
    finally:
        try:
            start_lock_file().unlink()
        except OSError:
            pass


def start_boot_command(args) -> list:
    """Command line of the loop process: `start --foreground` with --rounds and --round-minutes of this call (so a limited run ends after its rounds)."""
    cmd = [osplat.python_executable(), str(TOOLS_DIR / "haivequeen" / "cli.py"), "start", "--foreground"]
    if args.rounds:
        cmd += ["--rounds", str(args.rounds)]
    if args.round_minutes:
        cmd += ["--round-minutes", str(args.round_minutes)]
    if getattr(args, "keep_pause", False):
        cmd += ["--keep-pause"]
    return cmd


# Start a session: the loop as a hidden process and the graphical window.
def cmd_start(args, conn) -> int:
    """`bzz start`: run the loop in this process (--foreground), or start it as a hidden background process (no terminal window) and open the graphical dashboard once."""
    from . import config, gui
    if args.foreground and getattr(args, "desktop", False):
        raise ValueError("Die Desktop-Rückmeldung unterstützt nur den Hintergrundstart.")
    machine = config.load_config()
    pid = loop_running()
    if pid:
        if stop_flag().exists():
            return command_result(args, {"ok": True, "running": True, "stopping": True, "pid": pid,
                                         "running_agents": active_count(conn), "note": "Waiting for the previous stop to finish."})
        return command_result(args, {"ok": True, "running": True, "pid": pid, **({} if args.foreground else gui.launch_detached()),
                                     "note": "The orchestrator is already running."})
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
        runstate.resume(conn, "bzz start")   # a pause of the run before must not keep the new run from starting agents
    if args.foreground:
        return run_loop(conn, machine, args.rounds, args.round_minutes)
    boot = start_boot_command(args)
    with start_lock():   # a second `bzz start` waits here and then finds the loop (or the pending start) of the first
        pid = loop_running()
        if pid:
            return command_result(args, {"ok": True, "running": True, "pid": pid, **gui.launch_detached(), "note": "The orchestrator is already running."})
        if start_pending():
            return command_result(args, {"ok": True, "running": False, "starting": True, "note": "The orchestrator is just starting."})
        start_pending_file().parent.mkdir(parents=True, exist_ok=True)
        start_pending_file().write_text(str(os.getpid()), encoding="ascii")
        try:
            spawn_detached(boot, osplat.runs_dir() / "beekeeper.log")   # the loop has no window: its output goes to this file, round errors show in the dashboard
            return command_result(args, {"ok": True, **gui.launch_detached()})
        except Exception:
            start_pending_file().unlink()
            raise


# Open the graphical dashboard.
def cmd_gui(args, conn) -> int:
    """`bzz gui`: show the graphical dashboard window (blocks until it is closed); --detach opens it as a process of its own and returns."""
    from . import gui
    if args.detach:
        return command_result(args, {"ok": True, **gui.launch_detached()})
    return gui.main(["--project", args.project] if args.project else [])


# Request a graceful stop and report how many agents may finish first.
def cmd_stop(args, conn) -> int:
    """`bzz stop`: create the STOP flag; running agents finish their task, no new agent starts, the loop ends."""
    stop_flag().parent.mkdir(parents=True, exist_ok=True)
    stop_flag().write_text("stop requested %s\n" % datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return command_result(args, {"ok": True, "running_agents": active_count(conn)})


# Register session commands and their optional desktop feedback.
def register(sub) -> None:
    """Add the commands start, gui and stop to the sub-parser collection of haivequeen/cli.py."""
    p = sub.add_parser("start")
    p.add_argument("--foreground", action="store_true", help="run the loop in this process")
    p.add_argument("--rounds", type=int, default=0, help="end after this many rounds (0 = until stopped)")
    p.add_argument("--round-minutes", type=float, default=0)
    p.add_argument("--keep-pause", action="store_true", help="do not lift a pause of the run before (used by the self-update)")
    p.add_argument("--desktop", action="store_true", help="show the result in a desktop dialog")
    p.set_defaults(fn=cmd_start)
    p = sub.add_parser("gui")
    p.add_argument("--detach", action="store_true", help="open the window as a process of its own and return (default: run it in this process)")
    p.add_argument("--project", help="show one Apiary only")
    p.set_defaults(fn=cmd_gui)
    p = sub.add_parser("stop")
    p.add_argument("--desktop", action="store_true", help="show the result in a desktop dialog")
    p.set_defaults(fn=cmd_stop)
