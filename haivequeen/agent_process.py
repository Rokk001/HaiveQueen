"""Shared process-identity checks for agent rows."""
import json
from datetime import timedelta

from . import db, platform as osplat

ACTIVE = ("running", "starting")


# Read the birth identity from either a runtime dictionary or a database row.
def _identity(row):
    """Return the recorded process identity, if any."""
    value = dict(row)
    if value.get("processIdentity"):
        return value["processIdentity"]
    try:
        return json.loads(value.get("extra_json") or "{}").get("processIdentity")
    except (ValueError, TypeError, AttributeError):
        return None


# Detect only a PID that is provably different from the recorded agent.
def pid_reused(row):
    """Return True only when the current process cannot be the recorded agent."""
    row = dict(row)
    pid = row.get("pid")
    if not pid:
        return False
    recorded = _identity(row)
    if recorded:
        current = osplat.process_identity(pid)
        if current is not None:
            return current != recorded
    # Legacy records lack an immutable identity. Only chronology can disprove a match.
    ended = db.parse_time(row.get("ended_at"))
    started = db.parse_time(row.get("started_at")) if row.get("status") == "running" else None
    bound = ended or started
    actual = osplat.process_start_time(pid) if bound else None
    return bool(actual and actual > bound + timedelta(seconds=1))


# Check a process without counting a PID proven to have been reused.
def is_live(row, alive=None):
    """Return whether the recorded PID is alive and not proven to be another process."""
    row = dict(row)
    pid = row.get("pid")
    alive = alive or osplat.process_alive
    return bool(pid and alive(pid) and not pid_reused(row))


# Count agents still starting or running and runners that have not exited.
def counts(row, alive=None):
    """Conservatively count active rows unless their PID is proven to be reused."""
    row = dict(row)
    if row.get("status") in ACTIVE:
        return not (row.get("pid") and pid_reused(row))
    return is_live(row, alive)
