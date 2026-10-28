-- agent-orchestrator database schema, version 2 (v1 plus runtime tables and fields; orchlib/db.py migrates older files).
-- journal_mode WAL is set per connection by orchlib/db.py (together with busy_timeout 10000 ms).

CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    workspace TEXT,
    colour TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    project TEXT NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    status TEXT CHECK (status IN ('DRAFT','READY','IN_PROGRESS','DONE','PAUSED')),
    updated_at TEXT,
    PRIMARY KEY (project, path)
);

CREATE TABLE IF NOT EXISTS packages (
    project TEXT NOT NULL,
    plan_path TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT,
    class TEXT,
    agent_type TEXT,
    depends_on TEXT,
    phase TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    lock_reason TEXT,
    PRIMARY KEY (project, plan_path, id)
);

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    name TEXT,
    plan_path TEXT,
    package TEXT,
    phase TEXT,
    provider TEXT,
    model TEXT,
    effort TEXT,
    class TEXT,
    status TEXT,
    pid INTEGER,
    pane_id TEXT,
    cwd TEXT,
    started_at TEXT,
    ended_at TEXT,
    exit_code INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    last_line TEXT,
    bundle_json TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    extra_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    project TEXT,
    kind TEXT NOT NULL,
    agent TEXT,
    text TEXT,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    plan_path TEXT,
    item TEXT,
    question TEXT NOT NULL,
    recommendation TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    answer TEXT,
    answered_by TEXT CHECK (answered_by IS NULL OR answered_by IN ('owner','coordinator','auto')),
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    ts TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('owner','coordinator','auto')),
    text TEXT NOT NULL,
    plan_path TEXT,
    item TEXT
);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    branch TEXT NOT NULL,
    tip TEXT,
    plan_path TEXT,
    status TEXT NOT NULL CHECK (status IN ('ready','in_run','integrated','blocked')),
    note TEXT,
    run TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS build_measurements (
    host TEXT,
    project TEXT,
    ts TEXT,
    jobs INTEGER,
    free_start_mb REAL,
    free_min_mb REAL,
    per_job_mb REAL,
    duration_s REAL
);

CREATE INDEX IF NOT EXISTS idx_build_key ON build_measurements (project, host, ts);

CREATE TABLE IF NOT EXISTS providers_usage (
    ts TEXT,
    provider TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    kind TEXT,
    project TEXT,
    trigger TEXT,
    context INTEGER,
    duration_s REAL,
    actions TEXT,
    error INTEGER
);

CREATE TABLE IF NOT EXISTS handled (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    ts TEXT,
    value TEXT,
    PRIMARY KEY (project, key)
);

CREATE TABLE IF NOT EXISTS nofollow (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (project, key)
);

CREATE TABLE IF NOT EXISTS kv (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT,
    PRIMARY KEY (project, key)
);

CREATE INDEX IF NOT EXISTS idx_agents_project ON agents (project, status);
CREATE INDEX IF NOT EXISTS idx_events_project ON events (project, id);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions (project, status);
CREATE INDEX IF NOT EXISTS idx_queue_project ON queue (project, status);
