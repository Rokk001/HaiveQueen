"""Own-operation faults of the loop, checked before opening questions (one per fault kind and day, no duplicates).

`file_fault` is the only place that creates these questions. Their plan_path is the fixed text `HQ-FAULTS` (there is no plan file); the round starts
no triage and no work agent for them. Confirmed or unresolved faults are opened for the coordinator. `check_round` detects the fault kinds:
no_start (startable packages and 0 starts for 2 rounds in a row), runner_vanished, agent_no_result, loop_vanished (unexpected loop end), self_update_rollback and self_update_pane.
"""
import json
import re
from datetime import datetime, timedelta

from . import db, runtime, scheduler

FAULT_PLAN = "HQ-FAULTS"          # plan_path of every fault question
FAULT_PROJECT = "HaiveQueen"      # project that owns the fault questions
KINDS = ("no_start", "runner_vanished", "agent_no_result", "self_update_rollback", "self_update_pane", "loop_vanished")
IDLE_ROUNDS = 2                   # rounds with startable packages and no start before no_start is filed
SEEN_KEY, IDLE_KEY = "faults-seen", "faults-idle-rounds"


# File one fault for automatic checking or immediate review when confirmed.
def file_fault(conn, project, kind, text):
    """File one fault for automatic checking, or immediately open a confirmed self-update fault; deduplicate by kind and day."""
    today = db.now()[:10]
    for q in db.rows(conn, "SELECT * FROM questions WHERE project=? AND plan_path=? AND item=?", (project, FAULT_PLAN, kind)):
        if str(q["created_at"])[:10] == today:
            return None
    return db.add_question(conn, project, FAULT_PLAN, kind, "[%s] %s" % (kind, text), "", status="open" if kind in ("self_update_rollback", "self_update_pane") else "fault_check")


def _project_known(conn, project) -> bool:
    """True when the fault project is registered (without it nothing is filed)."""
    return conn.execute("SELECT 1 FROM projects WHERE name=?", (project,)).fetchone() is not None


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
            file_fault(conn, env.name, kind, "Agent %s of Apiary %s: %s." % (a["name"], env.name, what))
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
        file_fault(conn, env.name, "no_start", "Apiary %s: %d rounds in a row with startable packages and no agent started." % (env.name, n))


# Resolve open fault reports only when subsequent observations establish recovery.
def _resolve_open(env, started: int, now: datetime) -> None:
    """Check open reports against later rounds, agent results and loop evidence."""
    conn = env.conn
    key = "faults-start-rounds"
    history = json.loads(runtime.kv_get(conn, env.name, key) or "[]")
    history = (history + [[now.isoformat(timespec="seconds"), started > 0]])[-2:]
    runtime.kv_set(conn, env.name, key, json.dumps(history))
    projects = {env.name, FAULT_PROJECT}  # Include legacy reports filed centrally.
    questions = [q for project in projects for status in ("open", "fault_check")
                 for q in runtime.list_questions(conn, project, status, FAULT_PLAN)]
    for q in questions:
        created = db.parse_time(q["created_at"])
        if created is None or created > now:
            continue
        kind, answer, evidence, confirmed = q["item"], None, None, False
        text = q["question"] or ""
        if kind == "no_start" and any(("%s %s:" % (label, env.name)) in text for label in ("Apiary", "Project")):
            evidence = "Last rounds of Apiary %s: %s" % (env.name, history)
            if len(history) == 2 and all(ok and db.parse_time(t) > created for t, ok in history):
                answer = "The last two rounds of Apiary %s both started agents (%s and %s)." % (env.name, history[0][0], history[1][0])
        elif kind in ("runner_vanished", "agent_no_result"):
            match = re.search(r"Agent (.+?) of (?:Apiary|project) (.+?):", text)
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
        if not answer and not evidence and q["status"] == "fault_check" and kind in KINDS + ("loop_vanished",) and now - created >= timedelta(minutes=60):
            owner = (re.search(r"Agent .+? of (?:Apiary|project) (.+?):", text) if kind in ("runner_vanished", "agent_no_result")
                     else re.search(r"(?:Apiary|Project) (.+?):", text) if kind == "no_start" else None)
            project = owner.group(1) if owner else None
            if not project or project == env.name or not _project_known(conn, project):
                evidence = "Could not match the report to a registered project and recovery check: %s" % text
        if answer:
            db.answer_question(conn, q["id"], answer, "coordinator")
        elif evidence and (confirmed or now - created >= timedelta(minutes=60)):
            evidence_key = "fault-evidence-%s" % q["id"]
            if not runtime.kv_get(conn, q["project"], evidence_key):
                runtime.append_event(conn, q["project"], "Fault question %s remains open (%s): %s" % (q["id"], kind, evidence), kind="note", data={"question_id": q["id"]}, mirror="none")
                runtime.kv_set(conn, q["project"], evidence_key, "1")
            if q["status"] == "fault_check":
                with db.transaction(conn):
                    conn.execute("UPDATE questions SET status='open' WHERE id=? AND status='fault_check'", (q["id"],))
                    db.add_event(conn, q["project"], "question", q["question"], data={"question_id": q["id"]})


# Check and resolve fault reports for one project round.
def check_round(env, started: int, can_start: bool, now: datetime = None) -> None:
    """Fault detection of one project round (called at the end of the round routine; never raises, a broken check must not stop a round)."""
    try:
        _resolve_open(env, started, now or datetime.now())
        _check_idle(env, started, can_start)
        _check_agents(env)
    except Exception as exc:
        env.log("fault check failed: %s" % exc)
