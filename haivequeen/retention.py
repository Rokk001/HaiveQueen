"""Periodic removal of expired logs, completed runs, database history and inbox files."""
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, platform as osplat, runtime


DEFAULTS = {"logsDays": 30, "runsDays": 30, "databaseDays": 90, "inboxDays": 30}


def _cutoff(now, days):
    """Return the expiry time for a positive retention period, or None when disabled."""
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = 0
    return now - timedelta(days=days) if days > 0 else None


def _expired_file(path, cutoff):
    """Whether a file predates its cutoff; filesystem errors leave it untouched."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) < cutoff
    except OSError:
        return False


def _remove_file(path):
    """Remove a regular file and report whether it was removed."""
    try:
        path.unlink()
        return 1
    except OSError:
        return 0


def _remove_tree(path):
    """Remove an expired run directory without following links."""
    try:
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
        return 1
    except OSError:
        return 0


def cleanup(conn, machine=None, *, now=None, data_dir=None, runs_dir=None, projects=None):
    """Apply configured retention periods and return removed counts by artifact type."""
    machine = machine if machine is not None else config.load_config()
    periods = {**DEFAULTS, **(machine.get("retention") or {})}
    now = now or datetime.now()
    data_dir = Path(data_dir) if data_dir is not None else config.data_dir()
    runs_dir = Path(runs_dir) if runs_dir is not None else osplat.runs_dir()
    projects = projects if projects is not None else db.rows(conn, "SELECT name,workspace FROM projects")
    counts = {"logs": 0, "runs": 0, "events": 0, "usage": 0, "start_contexts": 0, "inbox": 0}

    cutoff = _cutoff(now, periods["logsDays"])
    if cutoff:
        log_dirs = {data_dir / "logs" / (p.get("name") or "_") for p in projects}
        log_dirs.update(runtime.mirror_dir(p.get("name")) for p in projects if p.get("name"))
        log_dirs.add(data_dir / "logs")
        try:
            log_dirs.update(p for p in (data_dir / "logs").iterdir() if p.is_dir())
        except OSError:
            pass
        for folder in log_dirs:
            if folder.is_dir():
                try:
                    for path in folder.glob("*"):
                        if path.is_file() and path.name in ("ROUNDS.log", "ORCH-EVENTS.log") and _expired_file(path, cutoff):
                            counts["logs"] += _remove_file(path)
                except OSError:
                    pass
        folder = runs_dir
        if folder.is_dir():
            path = folder / "beekeeper.log"
            if path.is_file() and _expired_file(path, cutoff):
                counts["logs"] += _remove_file(path)
        try:
            for path in data_dir.glob("crash-*.txt"):
                if path.is_file() and _expired_file(path, cutoff):
                    counts["logs"] += _remove_file(path)
        except OSError:
            pass

    cutoff = _cutoff(now, periods["runsDays"])
    if cutoff:
        for row in conn.execute(
                "SELECT a.id,a.project,a.name,a.plan_path,a.ended_at,a.status,p.status AS plan_status "
                "FROM agents a LEFT JOIN plans p ON p.project=a.project AND p.path=a.plan_path"):
            ended = db.parse_time(row["ended_at"])
            if (not row["name"] or not ended or ended >= cutoff or row["status"] in ("running", "starting") or
                    row["plan_path"] and row["plan_status"] not in ("DONE", "CANCELLED")):
                continue
            siblings = conn.execute(
                "SELECT a.status,a.ended_at,a.plan_path,p.status AS plan_status FROM agents a "
                "LEFT JOIN plans p ON p.project=a.project AND p.path=a.plan_path "
                "WHERE a.id<>? AND a.project=? AND a.name=?",
                                    (row["id"], row["project"], row["name"])).fetchall()
            if any(r["status"] in ("running", "starting") or not db.parse_time(r["ended_at"]) or
                   db.parse_time(r["ended_at"]) >= cutoff or
                   r["plan_path"] and r["plan_status"] not in ("DONE", "CANCELLED") for r in siblings):
                continue
            folder = runs_dir / str(row["project"]) / str(row["name"])
            try:
                if folder.resolve().is_relative_to(runs_dir.resolve()) and folder.is_dir():
                    counts["runs"] += _remove_tree(folder)
            except OSError:
                pass

    cutoff = _cutoff(now, periods["databaseDays"])
    if cutoff:
        stamp = cutoff.isoformat(timespec="seconds")
        with db.transaction(conn):
            counts["events"] = conn.execute("DELETE FROM events WHERE julianday(ts) < julianday(?)", (stamp,)).rowcount
            counts["usage"] = conn.execute("DELETE FROM providers_usage WHERE julianday(ts) < julianday(?)", (stamp,)).rowcount
            counts["start_contexts"] = conn.execute("DELETE FROM start_contexts WHERE julianday(started_at) < julianday(?)", (stamp,)).rowcount

    cutoff = _cutoff(now, periods["inboxDays"])
    if cutoff:
        for project in projects:
            workspace = project.get("workspace") or ""
            if not workspace:
                continue
            for state in ("done", "failed"):
                folder = Path(workspace) / ".haivequeen" / "inbox" / state
                if not folder.is_dir():
                    continue
                try:
                    for path in folder.iterdir():
                        if path.is_file() and _expired_file(path, cutoff):
                            counts["inbox"] += _remove_file(path)
                except OSError:
                    pass
    return counts
