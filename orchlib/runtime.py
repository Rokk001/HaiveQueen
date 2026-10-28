"""Runtime queries and writes (used by the loop, the CLI and the agents): agents, events, kv, handled, nofollow, builds, usage, plans, queue."""
import json
import os
import platform
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, plans as plan_mod

AGENT_COLUMNS = (
    "name", "plan_path", "package", "phase", "provider", "model", "effort", "class", "status", "pid",
    "pane_id", "cwd", "started_at", "ended_at", "exit_code", "tokens_in", "tokens_out", "last_line", "bundle_json",
)
AGENT_ALIASES = {"plan": "plan_path", "pane": "pane_id"}
ACTIVE_STATUSES = ("running", "starting")
QUEUE_STATUSES = ("ready", "in_run", "integrated", "blocked")
MIRRORS = {"rounds": "ROUNDS.log", "events": "ORCH-EVENTS.log"}


class CmdError(Exception):
    """A command failed for a reason the caller should see (reported as JSON error, exit code 1)."""


def snake(key: str) -> str:
    """Convert a camelCase key to snake_case (other keys are returned unchanged)."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda m: "_" + m.group(1).lower(), key)


def host_name() -> str:
    """Return the name of this machine (ORCH_HOST overrides it, used by tests)."""
    return os.environ.get("ORCH_HOST") or platform.node() or "host"


# ---- agents ----

def _agent_out(row) -> dict:
    """Turn an agents row into a flat dict: columns plus the free fields stored in extra_json."""
    d = dict(row)
    extra = d.pop("extra_json", None)
    d["archived"] = bool(d.get("archived"))
    if extra:
        for k, v in json.loads(extra).items():
            d.setdefault(k, v)
    return d


def _split_fields(fields: dict):
    """Split a field dict into (column values, extra values); keys may be camelCase or use the aliases."""
    cols, extra = {}, {}
    for key, value in fields.items():
        k = AGENT_ALIASES.get(key) or snake(key)
        k = AGENT_ALIASES.get(k, k)
        if k in AGENT_COLUMNS:
            cols[k] = value
        elif k in ("id", "project", "archived", "extra_json"):
            continue
        else:
            extra[key] = value
    return cols, extra


def list_agents(conn, project=None, scope="active", status=None) -> list:
    """List agent rows; scope is active (not archived), archive or all; optional project and status filter."""
    sql, args = "SELECT * FROM agents WHERE 1=1", []
    if scope == "active":
        sql += " AND archived=0"
    elif scope == "archive":
        sql += " AND archived=1"
    if project:
        sql += " AND project=?"
        args.append(project)
    if status:
        sql += " AND status=?"
        args.append(status)
    return [_agent_out(r) for r in conn.execute(sql + " ORDER BY id", args)]


def get_agent(conn, project, name):
    """Return the newest row of that agent name (active rows first), or None."""
    row = conn.execute(
        "SELECT * FROM agents WHERE project=? AND name=? ORDER BY archived, id DESC LIMIT 1", (project, name)
    ).fetchone()
    return _agent_out(row) if row else None


def upsert_agent(conn, project, name, fields, create=True) -> dict:
    """Create or update the active row of an agent in one transaction; unknown keys go into extra_json."""
    cols, extra = _split_fields(fields)
    cols.pop("name", None)
    with db.transaction(conn):
        row = conn.execute(
            "SELECT * FROM agents WHERE project=? AND name=? AND archived=0 ORDER BY id DESC LIMIT 1", (project, name)
        ).fetchone()
        if row is None:
            if not create:
                raise CmdError(f"unknown agent {name}")
            cols.setdefault("started_at", db.now())
            names = ["project", "name"] + list(cols) + (["extra_json"] if extra else [])
            vals = [project, name] + list(cols.values()) + ([json.dumps(extra)] if extra else [])
            cur = conn.execute(
                f"INSERT INTO agents ({','.join(names)}) VALUES ({','.join('?' * len(names))})", vals)
            aid = cur.lastrowid
        else:
            aid = row["id"]
            merged = json.loads(row["extra_json"]) if row["extra_json"] else {}
            merged.update(extra)
            sets = [f"{c}=?" for c in cols] + ["extra_json=?"]
            conn.execute(f"UPDATE agents SET {','.join(sets)} WHERE id=?",
                         list(cols.values()) + [json.dumps(merged) if merged else None, aid])
        return _agent_out(conn.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone())


def archive_agents(conn, project, hours=12.0, match=None) -> int:
    """Mark agent rows archived: ended more than `hours` ago and not running, or (with match) plan/name matches the regex."""
    limit = datetime.now() - timedelta(hours=hours)
    count = 0
    with db.transaction(conn):
        for r in conn.execute("SELECT id, name, plan_path, status, ended_at FROM agents WHERE project=? AND archived=0",
                              (project,)).fetchall():
            if match:
                hit = re.search(match, r["plan_path"] or "") or re.search(match, r["name"] or "")
            else:
                hit = False
                if r["ended_at"] and r["status"] not in ACTIVE_STATUSES:
                    try:
                        hit = datetime.fromisoformat(r["ended_at"]) < limit
                    except ValueError:
                        hit = False
            if hit:
                conn.execute("UPDATE agents SET archived=1 WHERE id=?", (r["id"],))
                count += 1
    return count


# ---- events ----

def mirror_dir(project, override=None) -> Path:
    """Folder of the plain-text mirror logs (override, else ORCH_LOG_DIR, else <data>/logs/<project>)."""
    if override:
        return Path(override)
    env = os.environ.get("ORCH_LOG_DIR")
    if env:
        return Path(env)
    return config.data_dir() / "logs" / (project or "_")


def mirror_line(kind: str, ts: str, text: str, error=False) -> str:
    """Format the mirror line: rounds = 'YYYY-MM-DD HH:MM:SS text', events = 'HH:MM|E or O|text'."""
    when = datetime.fromisoformat(ts)
    if kind == "rounds":
        return f"{when:%Y-%m-%d %H:%M:%S} {text}"
    flat = re.sub(r"\s+", " ", text).strip()
    return f"{when:%H:%M}|{'E' if error else 'O'}|{flat}"


def append_event(conn, project, text, kind=None, agent=None, data=None, mirror="rounds", error=False, log_dir=None) -> dict:
    """Insert an event row; then append the mirror line (rounds, events or both; none = no file) to the text log."""
    mirrors = ["rounds", "events"] if mirror == "both" else ([] if mirror == "none" else [mirror])
    for m in mirrors:
        if m not in MIRRORS:
            raise CmdError(f"unknown mirror {mirror}")
    with db.transaction(conn):
        eid = db.add_event(conn, project, kind or (mirrors[0] if mirrors else "note"), text, agent=agent, data=data)
        ts = conn.execute("SELECT ts FROM events WHERE id=?", (eid,)).fetchone()[0]
    for m in mirrors:
        folder = mirror_dir(project, log_dir)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / MIRRORS[m], "a", encoding="utf-8", newline="") as f:
            f.write(mirror_line(m, ts, text, error) + "\n")
    return {"id": eid, "ts": ts}


def list_events(conn, project=None, since_id=0, kind=None, limit=None, tail=False) -> list:
    """Return events after since_id, oldest first; tail=True returns the newest `limit` ones (still oldest first)."""
    sql, args = "SELECT * FROM events WHERE id>?", [since_id]
    if project:
        sql += " AND project=?"
        args.append(project)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if tail:
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit or 20)
        return list(reversed(db.rows(conn, sql, args)))
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return db.rows(conn, sql, args)


# ---- kv, handled, nofollow ----

def kv_get(conn, project, key):
    """Return the stored value of a key, or None."""
    row = conn.execute("SELECT value FROM kv WHERE project=? AND key=?", (project, key)).fetchone()
    return row[0] if row else None


def kv_set(conn, project, key, value) -> None:
    """Store a value (text) for a key."""
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO kv (project, key, value, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(project, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (project, key, value, db.now()))


def kv_delete(conn, project, key) -> int:
    """Delete a key; returns the number of deleted rows."""
    with db.transaction(conn):
        return conn.execute("DELETE FROM kv WHERE project=? AND key=?", (project, key)).rowcount


def kv_list(conn, project, prefix="") -> list:
    """List key/value pairs of a project whose key starts with the prefix."""
    return db.rows(conn, "SELECT key, value, updated_at FROM kv WHERE project=? AND substr(key,1,?)=? ORDER BY key",
                   (project, len(prefix), prefix))


def handled_get(conn, project, key):
    """Return the handled row (key, ts, value) or None."""
    r = conn.execute("SELECT key, ts, value FROM handled WHERE project=? AND key=?", (project, key)).fetchone()
    return dict(r) if r else None


def handled_add(conn, project, key, value=None) -> None:
    """Remember a key as handled (the value, e.g. a retry counter, is replaced)."""
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO handled (project, key, ts, value) VALUES (?,?,?,?) "
            "ON CONFLICT(project, key) DO UPDATE SET ts=excluded.ts, value=excluded.value",
            (project, key, db.now(), value))


def handled_remove(conn, project, key) -> int:
    """Forget a handled key; returns the number of deleted rows."""
    with db.transaction(conn):
        return conn.execute("DELETE FROM handled WHERE project=? AND key=?", (project, key)).rowcount


def nofollow_add(conn, project, key) -> None:
    """Freeze a plan|package key (stored lower case)."""
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO nofollow (project, key) VALUES (?,?)", (project, key.lower()))


def nofollow_remove(conn, project, key) -> int:
    """Unfreeze a key; returns the number of deleted rows."""
    with db.transaction(conn):
        return conn.execute("DELETE FROM nofollow WHERE project=? AND key=?", (project, key.lower())).rowcount


# ---- builds, usage ----

def build_add(conn, project, host, jobs, free_start_mb, free_min_mb, per_job_mb, duration_s) -> None:
    """Store one build measurement."""
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO build_measurements (host, project, ts, jobs, free_start_mb, free_min_mb, per_job_mb, duration_s) "
            "VALUES (?,?,?,?,?,?,?,?)", (host, project, db.now(), jobs, free_start_mb, free_min_mb, per_job_mb, duration_s))


def build_max_per_job(conn, project, host):
    """Highest measured RAM use per job (MB) of this host and project, or None."""
    row = conn.execute("SELECT MAX(per_job_mb) FROM build_measurements WHERE project=? AND host=?",
                       (project, host)).fetchone()
    return row[0]


def usage_add(conn, **f) -> None:
    """Store one usage row (brain or agent call); missing fields stay empty."""
    cols = ["ts", "provider", "model", "tokens_in", "tokens_out", "kind", "project", "trigger", "context",
            "duration_s", "actions", "error"]
    f.setdefault("ts", db.now())
    with db.transaction(conn):
        conn.execute(f"INSERT INTO providers_usage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [f.get(c) for c in cols])


def usage_list(conn, kind=None, project=None, since=None, limit=None) -> list:
    """List usage rows oldest first (optional kind, project and 'ts >= since' filters; limit = newest N)."""
    sql, args = "SELECT * FROM providers_usage WHERE 1=1", []
    for col, val, op in (("kind", kind, "="), ("project", project, "="), ("ts", since, ">=")):
        if val:
            sql += f" AND {col}{op}?"
            args.append(val)
    if limit:
        return list(reversed(db.rows(conn, sql + " ORDER BY ts DESC, rowid DESC LIMIT ?", args + [limit])))
    return db.rows(conn, sql + " ORDER BY ts, rowid", args)


# ---- plans and packages ----

def _workspace(conn, project, override=None) -> Path:
    """Workspace folder of a project (override, else the registered one)."""
    if override:
        return Path(override)
    row = conn.execute("SELECT workspace FROM projects WHERE name=?", (project,)).fetchone()
    if row is None or not row[0]:
        raise CmdError(f"project {project} has no workspace (register it or pass --workspace)")
    return Path(row[0])


def sync_plans(conn, project, workspace=None, extra_done=()) -> dict:
    """Read the plan files of a project into plans/packages and return the plans and the startable packages (rule 11)."""
    ws = _workspace(conn, project, workspace)
    found = plan_mod.discover_plans(str(ws), config.load_project(ws)["planGlobs"])
    stamp = db.now()
    startable = []
    with db.transaction(conn):
        for plan in found:
            rel = os.path.relpath(plan.path, ws).replace("\\", "/")
            conn.execute(
                "INSERT INTO plans (project, path, title, status, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(project, path) DO UPDATE SET title=excluded.title, status=excluded.status, "
                "updated_at=excluded.updated_at", (project, rel, plan.title, plan.status, stamp))
            ids = [p.id for p in plan.packages]
            for pkg in plan.packages:
                conn.execute(
                    "INSERT INTO packages (project, plan_path, id, title, class, agent_type, capability, depends_on) "
                    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project, plan_path, id) DO UPDATE SET "
                    "title=excluded.title, class=excluded.class, agent_type=excluded.agent_type, "
                    "capability=excluded.capability, depends_on=excluded.depends_on",
                    (project, rel, pkg.id, pkg.title, pkg.cls, pkg.agent, pkg.capability,
                     json.dumps(pkg.depends_on)))
            conn.execute(f"DELETE FROM packages WHERE project=? AND plan_path=? AND id NOT IN ({','.join('?' * len(ids)) or 'NULL'})",
                         [project, rel] + ids)
            state = {r["id"]: r for r in conn.execute(
                "SELECT id, phase, locked FROM packages WHERE project=? AND plan_path=?", (project, rel))}
            done = {i for i, r in state.items() if r["phase"] == "done"} | set(extra_done)
            for _plan, pkg in plan_mod.startable_packages([plan], done):
                if not state[pkg.id]["locked"]:
                    startable.append({"plan": rel, "id": pkg.id, "title": pkg.title, "class": pkg.cls,
                                      "agent": pkg.agent, "capability": pkg.capability,
                                      "depends_on": plan_mod.package_dependencies(plan, pkg)})
    return {"plans": list_plans(conn, project), "startable": startable}


def list_plans(conn, project=None) -> list:
    """List plans, optionally for one project."""
    if project:
        return db.rows(conn, "SELECT * FROM plans WHERE project=? ORDER BY path", (project,))
    return db.rows(conn, "SELECT * FROM plans ORDER BY project, path")


def list_packages(conn, project=None, plan=None) -> list:
    """List packages (optionally of one project and plan); depends_on is returned as a list."""
    sql, args = "SELECT * FROM packages WHERE 1=1", []
    if project:
        sql += " AND project=?"
        args.append(project)
    if plan:
        sql += " AND plan_path=?"
        args.append(plan)
    out = db.rows(conn, sql + " ORDER BY project, plan_path, rowid", args)
    for r in out:
        r["depends_on"] = json.loads(r["depends_on"]) if r["depends_on"] else []
        r["locked"] = bool(r["locked"])
    return out


def set_package(conn, project, plan, pid, phase=None, locked=None, reason=None) -> dict:
    """Change phase, lock flag and lock reason of one package; raises CmdError if it is unknown."""
    sets, args = [], []
    for col, val in (("phase", phase), ("locked", None if locked is None else int(bool(locked))), ("lock_reason", reason)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    with db.transaction(conn):
        cur = conn.execute(
            f"UPDATE packages SET {','.join(sets) or 'id=id'} WHERE project=? AND plan_path=? AND id=?",
            args + [project, plan, pid])
        if cur.rowcount == 0:
            raise CmdError(f"unknown package {plan} {pid}")
    return [p for p in list_packages(conn, project, plan) if p["id"] == pid][0]


def set_plan_status(conn, project, plan, status, workspace=None) -> dict:
    """Set a plan status in the table and in the plan file (front matter 'status =' line or the v1 'Status:' line 2)."""
    status = status.strip().upper()
    if status not in plan_mod.STATUSES:
        raise CmdError(f"unknown status {status}")
    path = _workspace(conn, project, workspace) / plan
    if not path.is_file():
        raise CmdError(f"plan file not found: {plan}")
    text = path.read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    changed = False
    if lines and lines[0].strip() == "+++":
        for i in range(1, len(lines)):
            if lines[i].strip() == "+++":
                break
            if re.match(r"^\s*status\s*=", lines[i]):
                lines[i] = f'status = "{status}"'
                changed = True
                break
    elif len(lines) > 1 and re.match(r"^Status:", lines[1]):
        lines[1] = f"Status: {status}"
        changed = True
    if not changed:
        raise CmdError(f"no status line found in {plan}")
    path.write_text(nl.join(lines), encoding="utf-8", newline="")
    with db.transaction(conn):
        conn.execute("UPDATE plans SET status=?, updated_at=? WHERE project=? AND path=?",
                     (status, db.now(), project, plan))
    return {"plan": plan, "status": status}


# ---- queue, questions, decisions ----

def queue_upsert(conn, project, branch, tip=None, plan=None, status=None, note=None, run=None) -> dict:
    """Update the newest queue row of a branch (or insert one); a new tip after 'integrated' starts a new row."""
    if status is not None and status not in QUEUE_STATUSES:
        raise CmdError(f"status must be one of {', '.join(QUEUE_STATUSES)}")
    with db.transaction(conn):
        row = conn.execute("SELECT * FROM queue WHERE project=? AND branch=? ORDER BY id DESC LIMIT 1",
                           (project, branch)).fetchone()
        if row is not None and not (row["status"] == "integrated" and tip is not None and tip != row["tip"]):
            vals = {"tip": tip, "plan_path": plan, "status": status, "note": note, "run": run}
            sets = [f"{k}=?" for k, v in vals.items() if v is not None] + ["updated_at=?"]
            conn.execute(f"UPDATE queue SET {','.join(sets)} WHERE id=?",
                         [v for v in vals.values() if v is not None] + [db.now(), row["id"]])
            qid = row["id"]
        else:
            qid = conn.execute(
                "INSERT INTO queue (project, branch, tip, plan_path, status, note, run, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (project, branch, tip, plan, status or "ready", note, run, db.now())).lastrowid
        return dict(conn.execute("SELECT * FROM queue WHERE id=?", (qid,)).fetchone())


def list_questions(conn, project=None, status=None, plan=None, item=None) -> list:
    """List questions filtered by project, status (open/answered), plan and item, oldest first."""
    sql, args = "SELECT * FROM questions WHERE 1=1", []
    for col, val in (("project", project), ("status", status), ("plan_path", plan), ("item", item)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    return db.rows(conn, sql + " ORDER BY id", args)


def list_decisions(conn, project=None, plan=None, item=None) -> list:
    """List decisions filtered by project, plan and item, oldest first."""
    sql, args = "SELECT * FROM decisions WHERE 1=1", []
    for col, val in (("project", project), ("plan_path", plan), ("item", item)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    return db.rows(conn, sql + " ORDER BY id", args)


def add_decision(conn, project, source, text, plan=None, item=None) -> int:
    """Record a decision (source owner, coordinator or auto) and return its id."""
    if source not in ("owner", "coordinator", "auto"):
        raise CmdError("source must be owner, coordinator or auto")
    with db.transaction(conn):
        return conn.execute(
            "INSERT INTO decisions (project, ts, source, text, plan_path, item) VALUES (?,?,?,?,?,?)",
            (project, db.now(), source, text, plan, item)).lastrowid
