"""Machine-wide run state (D8): RUNNING, PAUSED, DRAINING, DRAINED, stored in the kv table."""
import json

from . import db

GLOBAL_PROJECT = "*"   # kv/event project value for machine-wide state (not a real project)
KEY = "run-state"
STATES = ("RUNNING", "PAUSED", "DRAINING", "DRAINED")
ACTIVE = ("running", "starting")


def _read(conn) -> dict:
    """Return the stored state record; a missing record means RUNNING."""
    row = conn.execute("SELECT value FROM kv WHERE project=? AND key=?", (GLOBAL_PROJECT, KEY)).fetchone()
    if row is None:
        return {"state": "RUNNING", "since": None}
    return json.loads(row[0])


def _write(conn, state: str, kind: str, text: str, source: str = "") -> None:
    """Store a new state and log one event with the source of the change when given (inside a running transaction)."""
    ts = db.now()
    conn.execute(
        "INSERT INTO kv (project, key, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(project, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (GLOBAL_PROJECT, KEY, json.dumps({"state": state, "since": ts}), ts))
    db.add_event(conn, GLOBAL_PROJECT, kind, text + (" (source: %s)" % source if source else ""),
                 data={"state": state, "source": source} if source else {"state": state})


def running_agents(conn) -> int:
    """Count agents (all projects, not archived) that are starting or running."""
    marks = ",".join("?" * len(ACTIVE))
    return conn.execute(
        f"SELECT COUNT(*) FROM agents WHERE archived=0 AND status IN ({marks})", ACTIVE).fetchone()[0]


def get_state(conn) -> dict:
    """Return {state, since, running_agents} after settling a finished drain."""
    settle(conn)
    rec = _read(conn)
    return {"state": rec["state"], "since": rec["since"], "running_agents": running_agents(conn)}


def _transition(conn, allowed_from, new, kind, text, source: str = "") -> dict:
    """Move to a new state if the current one is allowed; an unchanged state writes nothing."""
    with db.transaction(conn):
        cur = _read(conn)["state"]
        if cur != new and (allowed_from is None or cur in allowed_from):
            _write(conn, new, kind, text, source)
    return settle(conn) if new == "DRAINING" else get_state(conn)


def pause(conn, source: str = "") -> dict:
    """Stop new starts (from any state); running agents keep running."""
    return _transition(conn, None, "PAUSED", "pause", "paused: no new agents are started", source)


def resume(conn, source: str = "") -> dict:
    """Lift pause or drain (from any state)."""
    return _transition(conn, None, "RUNNING", "resume", "resumed", source)


def drain(conn, source: str = "") -> dict:
    """Like pause, and report `drained` once no agent runs (from RUNNING or PAUSED)."""
    return _transition(conn, ("RUNNING", "PAUSED"), "DRAINING", "drain", "draining: waiting for running agents", source)


def settle(conn) -> dict:
    """Switch DRAINING to DRAINED (one `drained` event) when no agent runs; returns the state."""
    with db.transaction(conn):
        if _read(conn)["state"] == "DRAINING" and running_agents(conn) == 0:
            _write(conn, "DRAINED", "drained", "drained: no agent is running, safe to shut down")
    rec = _read(conn)
    return {"state": rec["state"], "since": rec["since"], "running_agents": running_agents(conn)}


def starts_allowed(conn) -> bool:
    """True only in RUNNING; the round calls this before every agent start."""
    return settle(conn)["state"] == "RUNNING"
