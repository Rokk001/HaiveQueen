"""Own-operation faults of the loop, filed as open questions of the tool project (one per fault kind and day, no duplicates).

`file_fault` is the only place that creates these questions. Their plan_path is the fixed text `HQ-FAULTS` (there is no plan file); the round starts
no triage and no work agent for them, the coordinator reads them. `check_round` detects the fault kinds in the round routine of the loop:
no_start (startable packages and 0 starts for 2 rounds in a row), runner_vanished, agent_no_result, terminal_crash (Windows Terminal crash in the
Application event log, read at most every 10 minutes) and self_update_rollback and self_update_pane (filed by the self-update through `file_fault`).
"""
import json
import re
from datetime import datetime, timedelta

from . import db, runtime, scheduler
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


# Read recent Application Error entries from the Windows event log.
def read_event_log() -> str | None:
    """Text of the newest Application Error entries, or None when the read fails."""
    try:
        code, out, _err, _timed_out = osplat.run_with_timeout(WEVTUTIL, 30)
    except OSError:
        return None
    return out.decode("utf-8", "replace") if code == 0 else None


# File new Windows Terminal crashes from the event log.
def _check_terminal_crash(conn, now: datetime, reader) -> None:
    """Read the event log at most every 10 minutes and file terminal_crash for a new WindowsTerminal.exe crash entry."""
    last = runtime.kv_get(conn, FAULT_PROJECT, EVENTLOG_KEY)
    if last and (now - datetime.fromisoformat(last)).total_seconds() < EVENTLOG_INTERVAL:
        return
    runtime.kv_set(conn, FAULT_PROJECT, EVENTLOG_KEY, now.isoformat(timespec="seconds"))
    since = datetime.fromisoformat(last) if last else now - timedelta(days=1)
    crashes = parse_terminal_crashes(reader() or "", since)
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
            file_fault(conn, FAULT_PROJECT, kind, "Agent %s of Apiary %s: %s." % (a["name"], env.name, what))
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
        file_fault(conn, FAULT_PROJECT, "no_start", "Apiary %s: %d rounds in a row with startable packages and no agent started." % (env.name, n))


# Resolve open fault reports only when subsequent observations establish recovery.
def _resolve_open(env, started: int, now: datetime, reader) -> None:
    """Check open reports against later rounds, agent results and loop or event-log evidence."""
    conn = env.conn
    key = "faults-start-rounds"
    history = json.loads(runtime.kv_get(conn, env.name, key) or "[]")
    history = (history + [[now.isoformat(timespec="seconds"), started > 0]])[-2:]
    runtime.kv_set(conn, env.name, key, json.dumps(history))
    questions = runtime.list_questions(conn, FAULT_PROJECT, "open", FAULT_PLAN)
    terminal = [q for q in questions if q["item"] == "terminal_crash"]
    log = reader() if terminal else ""
    for q in questions:
        created = db.parse_time(q["created_at"])
        if created is None or created > now:
            continue
        kind, answer, evidence = q["item"], None, None
        text = q["question"] or ""
        if kind == "terminal_crash":
            crashes = [t for t in parse_terminal_crashes(log or "", created) if t <= created + timedelta(minutes=60)]
            confirmed_key = "fault-confirmed-%s" % q["id"]
            if crashes and not runtime.kv_get(conn, FAULT_PROJECT, confirmed_key):
                crash_time = max(crashes).isoformat()
                runtime.kv_set(conn, FAULT_PROJECT, confirmed_key, crash_time)
                runtime.append_event(conn, FAULT_PROJECT, "Fault question %s confirmed by Windows Terminal crash at %s." % (q["id"], crash_time), kind="note", data={"question_id": q["id"]}, mirror="none")
            evidence = "Application log: %s" % ("read failed" if log is None else "confirmed crash at " + runtime.kv_get(conn, FAULT_PROJECT, confirmed_key) if runtime.kv_get(conn, FAULT_PROJECT, confirmed_key) else "no later crash found")
            if now - created >= timedelta(minutes=60) and log is not None and not crashes and not runtime.kv_get(conn, FAULT_PROJECT, confirmed_key):
                answer = "No later Windows Terminal crash in the Application log during the 60 minutes after the question."
        elif kind == "no_start" and ("Apiary %s:" % env.name) in text:
            evidence = "Last rounds of Apiary %s: %s" % (env.name, history)
            if len(history) == 2 and all(ok and db.parse_time(t) > created for t, ok in history):
                answer = "The last two rounds of Apiary %s both started agents (%s and %s)." % (env.name, history[0][0], history[1][0])
        elif kind in ("runner_vanished", "agent_no_result"):
            match = re.search(r"Agent (.+?) of Apiary (.+?):", text)
            if match and match.group(2) == env.name:
                agents = env.agents("all")
                evidence = "No later done run found for agent %s of Apiary %s." % (match.group(1), env.name)
                original = next((a for a in agents if a.get("name") == match.group(1)), None)
                for a in agents:
                    same_work = a.get("name") == match.group(1) or (original and original.get("package") and a.get("plan_path") == original.get("plan_path") and a.get("package") == original.get("package") and a.get("phase") == original.get("phase"))
                    ended = db.parse_time(a.get("ended_at"))
                    if same_work and a.get("status") == "done" and ended and ended > created:
                        answer = "Later run %s finished with status done at %s." % (a["name"], a["ended_at"])
                        break
        elif kind == "loop_vanished":
            start = db.parse_time(runtime.kv_get(conn, scheduler.GLOBAL, "loop-started-at"))
            end = db.parse_time(runtime.kv_get(conn, scheduler.GLOBAL, "loop-unexpected-end-at"))
            evidence = "Loop start: %s; latest unexpected end: %s." % (start, end)
            if start and end and start >= end and start >= created and now - start >= timedelta(minutes=60):
                answer = "Loop restarted at %s after unexpected end %s and has run for at least 60 minutes." % (start.isoformat(), end.isoformat())
        elif kind in ("self_update_rollback", "self_update_pane"):
            evidence = "Self-update fault requires review: %s" % text
        if answer:
            db.answer_question(conn, q["id"], answer, "coordinator")
        elif evidence and now - created >= timedelta(minutes=60):
            evidence_key = "fault-evidence-%s" % q["id"]
            if not runtime.kv_get(conn, FAULT_PROJECT, evidence_key):
                runtime.append_event(conn, FAULT_PROJECT, "Fault question %s remains open (%s): %s" % (q["id"], kind, evidence), kind="note", data={"question_id": q["id"]}, mirror="none")
                runtime.kv_set(conn, FAULT_PROJECT, evidence_key, "1")


# Check and resolve fault reports for one project round.
def check_round(env, started: int, can_start: bool, now: datetime = None, reader=None) -> None:
    """Fault detection of one project round (called at the end of the round routine; never raises, a broken check must not stop a round)."""
    if not _project_known(env.conn, FAULT_PROJECT):
        return
    try:
        _resolve_open(env, started, now or datetime.now(), reader or read_event_log)
        _check_idle(env, started, can_start)
        _check_agents(env)
        _check_terminal_crash(env.conn, now or datetime.now(), reader or read_event_log)
    except Exception as exc:
        env.log("fault check failed: %s" % exc)
