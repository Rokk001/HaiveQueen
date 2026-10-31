"""Own-operation faults of the loop, filed as open questions of the tool project (one per fault kind and day, no duplicates).

`file_fault` is the only place that creates these questions. Their plan_path is the fixed text `HQ-FAULTS` (there is no plan file); the round starts
no triage and no work agent for them, the coordinator reads them. `check_round` detects the fault kinds in the round routine of the loop:
no_start (startable packages and 0 starts for 2 rounds in a row), runner_vanished, agent_no_result, terminal_crash (Windows Terminal crash in the
Application event log, read at most every 10 minutes) and self_update_rollback and self_update_pane (filed by the self-update through `file_fault`).
"""
import json
import re
from datetime import datetime, timedelta

from . import db, runtime
from . import platform as osplat

FAULT_PLAN = "HQ-FAULTS"          # plan_path of every fault question
FAULT_PROJECT = "HaiveQueen"      # project that owns the fault questions
KINDS = ("no_start", "runner_vanished", "agent_no_result", "terminal_crash", "self_update_rollback", "self_update_pane")
IDLE_ROUNDS = 2                   # rounds with startable packages and no start before no_start is filed
EVENTLOG_INTERVAL = 600           # seconds between two reads of the event log
WEVTUTIL = ["wevtutil", "qe", "Application", "/q:*[System[Provider[@Name='Application Error']]]", "/c:20", "/rd:true", "/f:text"]
SEEN_KEY, IDLE_KEY, EVENTLOG_KEY = "faults-seen", "faults-idle-rounds", "faults-eventlog"


def file_fault(conn, project, kind, text):
    """File one fault as an open question of `project` unless this kind was already filed today; returns the question id or None for a duplicate."""
    today = db.now()[:10]
    for q in runtime.list_questions(conn, project, None, FAULT_PLAN, kind):
        if str(q["created_at"])[:10] == today:
            return None
    return db.add_question(conn, project, FAULT_PLAN, kind, "[%s] %s" % (kind, text), "")


def _project_known(conn, project) -> bool:
    """True when the fault project is registered (without it nothing is filed)."""
    return conn.execute("SELECT 1 FROM projects WHERE name=?", (project,)).fetchone() is not None


def parse_terminal_crashes(text: str, since: datetime) -> list:
    """Dates of the Application Error entries in `wevtutil ... /f:text` output that name WindowsTerminal.exe and are newer than `since`."""
    found = []
    for block in re.split(r"(?m)^Event\[\d+\]:", text or ""):
        m = re.search(r"(?m)^\s*Date:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", block)
        if m and "WindowsTerminal.exe" in block:
            when = datetime.fromisoformat(m.group(1))
            if when > since:
                found.append(when)
    return found


def read_event_log() -> str:
    """Text of the newest Application Error entries ('' when wevtutil is missing or fails)."""
    try:
        code, out, _err, _timed_out = osplat.run_with_timeout(WEVTUTIL, 30)
    except OSError:
        return ""
    return out.decode("utf-8", "replace") if code == 0 else ""


def _check_terminal_crash(conn, now: datetime, reader) -> None:
    """Read the event log at most every 10 minutes and file terminal_crash for a new WindowsTerminal.exe crash entry."""
    last = runtime.kv_get(conn, FAULT_PROJECT, EVENTLOG_KEY)
    if last and (now - datetime.fromisoformat(last)).total_seconds() < EVENTLOG_INTERVAL:
        return
    runtime.kv_set(conn, FAULT_PROJECT, EVENTLOG_KEY, now.isoformat(timespec="seconds"))
    since = datetime.fromisoformat(last) if last else now - timedelta(days=1)
    crashes = parse_terminal_crashes(reader(), since)
    if crashes:
        file_fault(conn, FAULT_PROJECT, "terminal_crash", "Windows Terminal crashed (%d new entr%s in the Application event log, newest %s)." % (
            len(crashes), "y" if len(crashes) == 1 else "ies", max(crashes).isoformat(timespec="seconds")))


# Detect non-interrupted agent faults without treating retries as failures.
def _check_agents(env) -> None:
    """File runner_vanished / agent_no_result for agent rows that ended that way since the last look (the first look only records the rows)."""
    conn = env.conn
    raw = runtime.kv_get(conn, env.name, SEEN_KEY)
    seen = set(json.loads(raw)) if raw else set()
    first = raw is None
    from . import interrupt
    for a in env.agents("all"):
        if a.get("status") not in ("done", "failed") or not a.get("name") or interrupt.is_interrupted(a):
            continue
        key = "%s|%s" % (a["name"], a.get("started_at") or "")
        if key in seen:
            continue
        seen.add(key)
        last = str(a.get("last_line") or "")
        if last.startswith("runner process vanished"):
            kind, what = "runner_vanished", "its runner process vanished"
        elif last.startswith("pane never started") or (env.run_dir(a["name"]) / "final.txt").is_file() and (env.run_dir(a["name"]) / "final.txt").stat().st_size > 0:
            continue
        else:
            kind, what = "agent_no_result", "it ended (%s) without a final result" % a["status"]
        if not first:
            file_fault(conn, FAULT_PROJECT, kind, "Agent %s of project %s: %s." % (a["name"], env.name, what))
    runtime.kv_set(conn, env.name, SEEN_KEY, json.dumps(sorted(seen)[-500:]))


def _check_idle(env, started: int, can_start: bool) -> None:
    """File no_start when startable packages exist and a round started nothing for IDLE_ROUNDS rounds in a row."""
    conn = env.conn
    stuck = False
    if can_start and started == 0 and not env.agents("active") and not runtime.list_questions(conn, env.name, "open"):
        for p in runtime.list_packages(conn, env.name):
            if p["phase"] != "done" and not p["locked"] and any(str(pl["path"]) == str(p["plan_path"]) and pl["status"] in ("READY", "IN_PROGRESS")
                                                                for pl in runtime.list_plans(conn, env.name)):
                stuck = True
                break
    n = int(runtime.kv_get(conn, env.name, IDLE_KEY) or 0) + 1 if stuck else 0
    runtime.kv_set(conn, env.name, IDLE_KEY, str(n))
    if n >= IDLE_ROUNDS:
        file_fault(conn, FAULT_PROJECT, "no_start", "Project %s: %d rounds in a row with startable packages and no agent started." % (env.name, n))


def check_round(env, started: int, can_start: bool, now: datetime = None, reader=None) -> None:
    """Fault detection of one project round (called at the end of the round routine; never raises, a broken check must not stop a round)."""
    if not _project_known(env.conn, FAULT_PROJECT):
        return
    try:
        _check_idle(env, started, can_start)
        _check_agents(env)
        _check_terminal_crash(env.conn, now or datetime.now(), reader or read_event_log)
    except Exception as exc:
        env.log("fault check failed: %s" % exc)
