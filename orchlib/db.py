"""SQLite access: schema creation and migration, atomic writes and queries."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema.sql"
SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 10000


# Columns added in schema version 2; migrate() adds them to databases created by version 1.
V2_COLUMNS = (
    ("agents", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "extra_json", "TEXT"),
    ("decisions", "item", "TEXT"),
    ("handled", "value", "TEXT"),
    ("providers_usage", "project", "TEXT"),
    ("providers_usage", "trigger", "TEXT"),
    ("providers_usage", "context", "INTEGER"),
    ("providers_usage", "duration_s", "REAL"),
    ("providers_usage", "actions", "TEXT"),
    ("providers_usage", "error", "INTEGER"),
)


def now() -> str:
    """Return the current local time as an ISO-8601 string without microseconds."""
    return datetime.now().isoformat(timespec="seconds")


def connect(path) -> sqlite3.Connection:
    """Open the database (creating folder and schema if needed) with WAL and busy_timeout set."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION (user_version pragma)."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        for table, column, decl in V2_COLUMNS:
            have = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_archived ON agents (project, archived)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_kind ON providers_usage (kind, ts)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Run a block as one atomic write transaction (BEGIN IMMEDIATE, rollback on error)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def rows(conn, sql: str, args=()) -> list:
    """Run a query and return the rows as plain dicts."""
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def add_event(conn, project, kind, text, agent=None, data=None) -> int:
    """Insert an event row (history) inside a running transaction and return its id."""
    cur = conn.execute(
        "INSERT INTO events (ts, project, kind, agent, text, data_json) VALUES (?,?,?,?,?,?)",
        (now(), project, kind, agent, text, json.dumps(data) if data is not None else None),
    )
    return cur.lastrowid


def upsert_project(conn, name, workspace=None, colour=None, priority=0) -> None:
    """Insert a project or update its workspace, colour and priority."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO projects (name, workspace, colour, priority, registered_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET workspace=excluded.workspace, colour=excluded.colour, "
            "priority=excluded.priority",
            (name, workspace, colour, priority, now()),
        )


def add_agent(conn, project, **fields) -> int:
    """Insert an agent row (columns given as keyword arguments) and return its id."""
    fields.setdefault("started_at", now())
    cols = ["project"] + list(fields)
    with transaction(conn):
        cur = conn.execute(
            f"INSERT INTO agents ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [project] + list(fields.values()),
        )
    return cur.lastrowid


def finish_agent(conn, agent_id: int, result_text: str) -> dict:
    """Mark an agent done with its final report; returns the agent row. Raises KeyError if unknown."""
    with transaction(conn):
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown agent {agent_id}")
        last = (result_text.strip().splitlines() or [""])[-1][:300]
        conn.execute(
            "UPDATE agents SET status='done', ended_at=?, exit_code=COALESCE(exit_code,0), last_line=? WHERE id=?",
            (now(), last, agent_id),
        )
        add_event(conn, row["project"], "done", last, agent=str(agent_id), data={"result": result_text})
    return dict(row)


def add_ready(conn, project, branch, tip, plan_path, note) -> dict:
    """Register a branch as ready in the integration queue; one open row per branch (updated in place)."""
    with transaction(conn):
        row = conn.execute(
            "SELECT id, status, tip FROM queue WHERE project=? AND branch=? ORDER BY id DESC LIMIT 1",
            (project, branch),
        ).fetchone()
        if row is not None and row["status"] == "integrated" and row["tip"] == tip:
            return {"id": row["id"], "status": "integrated", "unchanged": True}
        if row is not None and row["status"] in ("ready", "blocked"):
            conn.execute(
                "UPDATE queue SET tip=?, plan_path=?, status='ready', note=?, updated_at=? WHERE id=?",
                (tip, plan_path, note, now(), row["id"]),
            )
            qid = row["id"]
        else:
            qid = conn.execute(
                "INSERT INTO queue (project, branch, tip, plan_path, status, note, updated_at) "
                "VALUES (?,?,?,?,'ready',?,?)",
                (project, branch, tip, plan_path, note, now()),
            ).lastrowid
        add_event(conn, project, "ready", f"{branch} {tip}", data={"queue_id": qid})
    return {"id": qid, "status": "ready"}


def add_question(conn, project, plan_path, item, question, recommendation) -> int:
    """Insert an open question for the owner and return its id."""
    with transaction(conn):
        qid = conn.execute(
            "INSERT INTO questions (project, plan_path, item, question, recommendation, status, created_at) "
            "VALUES (?,?,?,?,?,'open',?)",
            (project, plan_path, item, question, recommendation, now()),
        ).lastrowid
        add_event(conn, project, "question", question, data={"question_id": qid})
    return qid


def answer_question(conn, qid: int, text: str, by: str) -> dict:
    """Answer an open question and log it as a decision; raises KeyError if unknown or already answered."""
    if by not in ("owner", "coordinator", "auto"):
        raise ValueError("answered_by must be owner, coordinator or auto")
    with transaction(conn):
        row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if row is None:
            raise KeyError(f"unknown question {qid}")
        if row["status"] != "open":
            raise KeyError(f"question {qid} is already answered")
        conn.execute(
            "UPDATE questions SET status='answered', answer=?, answered_by=?, answered_at=? WHERE id=?",
            (text, by, now(), qid),
        )
        conn.execute(
            "INSERT INTO decisions (project, ts, source, text, plan_path, item) VALUES (?,?,?,?,?,?)",
            (row["project"], now(), by, f"{row['question']} -> {text}", row["plan_path"], row["item"]),
        )
        add_event(conn, row["project"], "answer", text, data={"question_id": qid, "by": by})
    return {"id": qid, "project": row["project"], "answered_by": by}


def open_questions(conn, project=None, only_open=False) -> list:
    """Return questions (optionally only open ones, optionally for one project), oldest first."""
    sql, args = "SELECT * FROM questions WHERE 1=1", []
    if only_open:
        sql += " AND status='open'"
    if project:
        sql += " AND project=?"
        args.append(project)
    return rows(conn, sql + " ORDER BY id", args)


def queue_rows(conn, project=None) -> list:
    """Return integration queue rows, optionally for one project."""
    if project:
        return rows(conn, "SELECT * FROM queue WHERE project=? ORDER BY id", (project,))
    return rows(conn, "SELECT * FROM queue ORDER BY id")


def project_status(conn, project=None) -> list:
    """Return per project the counts integrated/total and a task table (task, agent, status)."""
    names = [r["name"] for r in conn.execute("SELECT name FROM projects ORDER BY priority DESC, name")]
    for table in ("packages", "queue", "agents"):
        for r in conn.execute(f"SELECT DISTINCT project FROM {table}"):
            if r[0] not in names:
                names.append(r[0])
    if project:
        names = [n for n in names if n == project]
    result = []
    for name in names:
        tasks = []
        pkgs = rows(conn, "SELECT * FROM packages WHERE project=? ORDER BY plan_path, id", (name,))
        for p in pkgs:
            ag = conn.execute(
                "SELECT name, status FROM agents WHERE project=? AND plan_path=? AND package=? "
                "ORDER BY id DESC LIMIT 1",
                (name, p["plan_path"], p["id"]),
            ).fetchone()
            merged = conn.execute(
                "SELECT 1 FROM queue WHERE project=? AND plan_path=? AND status='integrated' LIMIT 1",
                (name, p["plan_path"]),
            ).fetchone()
            if ag is not None and ag["status"] == "done" and merged is not None:
                status = "integriert"
            elif ag is not None:
                status = ag["status"] or "running"
            elif p["locked"]:
                status = "gesperrt"
            else:
                status = "offen"
            tasks.append({"task": f"{p['id']} {p['title'] or ''}".strip(),
                          "agent": ag["name"] or "" if ag else "", "status": status})
        if not pkgs:
            for q in queue_rows(conn, name):
                tasks.append({"task": q["branch"], "agent": "",
                              "status": "integriert" if q["status"] == "integrated" else q["status"]})
        done = sum(1 for t in tasks if t["status"] == "integriert")
        total = len(tasks)
        result.append({"project": name, "integrated": done, "total": total,
                       "percent": round(100 * done / total) if total else 0, "tasks": tasks})
    return result
