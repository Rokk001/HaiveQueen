"""Machine-wide run state (D8): RUNNING or PAUSED, stored in the kv table."""
import json

from . import db, agent_process

GLOBAL_PROJECT = "*"   # kv/event project value for machine-wide state (not a real project)
KEY = "run-state"
STATES = ("RUNNING", "PAUSED")
ACTIVE = ("running", "starting")


def _read(conn) -> dict:
    """Return the stored state record; a missing record means RUNNING."""
    row = conn.execute("SELECT value FROM kv WHERE project=? AND key=?", (GLOBAL_PROJECT, KEY)).fetchone()
    if row is None:
        return {"state": "RUNNING", "since": None}
    rec = json.loads(row[0])
    if rec.get("state") not in STATES:
        rec["state"] = "PAUSED"   # DRAINING or DRAINED, stored by an older version, behaves like a pause
    return rec


def _write(conn, state: str, kind: str, text: str, source: str = "") -> None:
    """Store a new state and log one event with the source of the change when given (inside a running transaction)."""
    ts = db.now()
    conn.execute(
        "INSERT INTO kv (project, key, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(project, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (GLOBAL_PROJECT, KEY, json.dumps({"state": state, "since": ts}), ts))
    db.add_event(conn, GLOBAL_PROJECT, kind, text + (" (source: %s)" % source if source else ""),
                 data={"state": state, "source": source} if source else {"state": state})


# Count active agent processes, including runners finishing after a done report.
def running_agents(conn) -> int:
    """Count active agent processes, including runners finishing after a done report."""
    rows = conn.execute("SELECT status,phase,pid,started_at,ended_at,extra_json FROM agents WHERE archived=0").fetchall()
    return sum(1 for row in rows if row['status'] in ACTIVE and agent_process.counts(row) or
               row['phase'] in ('queen', 'bee') and agent_process.is_live(row))


def get_state(conn) -> dict:
    """Return {state, since, running_agents}."""
    rec = _read(conn)
    return {"state": rec["state"], "since": rec["since"], "running_agents": running_agents(conn)}


# Move to a new state if the current one is allowed; an unchanged state writes nothing.
def _transition(conn, allowed_from, new, kind, text, source: str = "") -> dict:
    """Move to a new state if the current one is allowed; an unchanged state writes nothing."""
    with db.transaction(conn):
        cur = _read(conn)["state"]
        if cur != new and (allowed_from is None or cur in allowed_from):
            _write(conn, new, kind, text, source)
        if new == "RUNNING":
            conn.execute("UPDATE kv SET value='armed',updated_at=? WHERE project=? AND key='hives-migration' AND value='pending'", (db.now(), GLOBAL_PROJECT))
    return get_state(conn)


def pause(conn, source: str = "") -> dict:
    """Stop new starts (from any state); running agents keep running."""
    return _transition(conn, None, "PAUSED", "pause", "paused: no new agents are started", source)


# Lift the pause and arm the one-time Hive migration.
def resume(conn, source: str = "") -> dict:
    """Lift the pause and arm the one-time Hive migration."""
    return _transition(conn, None, "RUNNING", "resume", "resumed", source)


def starts_allowed(conn) -> bool:
    """True only in RUNNING; the round calls this before every agent start."""
    return _read(conn)["state"] == "RUNNING"
