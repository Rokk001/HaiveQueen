"""Import of v1 state (runtime folder, questions, answers, queue table, plans) into the database; idempotent."""
import glob
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import db, plans as plan_mod, runtime
from .runtime import CmdError

KV_FILES = (("ORCH-STATE.json", "loop-state"), ("ORCH-SYNC.json", "event-sync"), ("usage-claude.json", "usage-claude"))
QUEUE_STATUS = (("ready", "ready"), ("in lauf", "in_run"), ("integriert", "integrated"), ("blockiert", "blocked"),
                ("zurückgezogen", "withdrawn"), ("zurueckgezogen", "withdrawn"), ("superseded", "withdrawn"),
                ("ersetzt", "withdrawn"), ("verworfen", "withdrawn"), ("zurückgestellt", "withdrawn"),
                ("zurueckgestellt", "withdrawn"))


class Counter:
    """Counts imported / skipped (already present) / error (unusable) rows of one source."""

    def __init__(self):
        """Start at zero."""
        self.imported = 0
        self.skipped = 0
        self.errors = 0
        self.samples = []

    def error(self, message):
        """Count one unusable row and keep the first few messages."""
        self.errors += 1
        if len(self.samples) < 5:
            self.samples.append(str(message)[:200])

    def result(self) -> dict:
        """Return the counters as a JSON-able dict."""
        out = {"imported": self.imported, "skipped": self.skipped, "errors": self.errors}
        if self.samples:
            out["error_samples"] = self.samples
        return out


def read_text(path) -> str:
    """Read a text file as UTF-8 (BOM dropped, undecodable bytes replaced); CRLF and LF both work via splitlines."""
    return Path(path).read_bytes().decode("utf-8-sig", errors="replace")


def read_json(path):
    """Parse a JSON file (BOM tolerant)."""
    return json.loads(read_text(path))


def digest(*parts) -> str:
    """Stable short hash of text parts."""
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:20]


def slash(path):
    """Use forward slashes in a path text (v1 used backslashes); empty stays empty."""
    return (path or "").replace("\\", "/")


def guarded(conn, counter, label, fn) -> None:
    """Run one row insert in a savepoint; a failure rolls back only that row and is counted."""
    conn.execute("SAVEPOINT import_row")
    try:
        fn()
    except Exception as exc:  # one bad row must not stop the rest
        conn.execute("ROLLBACK TO import_row")
        counter.error(f"{label}: {exc}")
    finally:
        conn.execute("RELEASE import_row")


def mtime_iso(path) -> str:
    """File modification time as local ISO text without microseconds."""
    return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")


def local_naive(text):
    """Turn an ISO time text with a UTC offset (v1 wrote '+01:00') into local time without offset, as the database keeps all times; other text stays as it is."""
    try:
        when = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return text
    if when.tzinfo is None:
        return text
    return when.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


# ---- runtime folder ----

def int_or_none(v):
    """Convert a value to int, or None when it is empty or not numeric."""
    try:
        return None if v in (None, "") else int(v)
    except (TypeError, ValueError):
        return None


def import_agents(conn, project, path, archived, counter) -> None:
    """Import AGENTS.json / AGENTS-ARCHIVE.json rows; the key is (name, startedAt)."""
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array")
    for i, item in enumerate(data):
        def one(item=item, i=i):
            """Insert one agent row unless it exists."""
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                raise ValueError("agent without name")
            fields = dict(item)
            bundle = fields.pop("bundle", None)
            if bundle:
                fields["bundle_json"] = json.dumps(bundle, ensure_ascii=False)
            cols, extra = runtime._split_fields(fields)
            name = str(cols.pop("name", item["name"]))
            if cols.get("plan_path"):
                cols["plan_path"] = slash(cols["plan_path"])
            started = cols.get("started_at")
            for k, v in list(cols.items()):
                if k.endswith("_at") and v:
                    cols[k] = local_naive(v)
            if conn.execute("SELECT 1 FROM agents WHERE project=? AND name=? AND (started_at IS ? OR started_at IS ?)",
                            (project, name, started, cols.get("started_at"))).fetchone():
                counter.skipped += 1
                return
            for k in ("pid", "exit_code", "tokens_in", "tokens_out"):
                if k in cols:
                    cols[k] = int_or_none(cols[k])
            for k, v in list(cols.items()):
                if isinstance(v, (dict, list)):
                    cols[k] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, bool):
                    cols[k] = int(v)
            names = ["project", "name", "archived"] + list(cols) + (["extra_json"] if extra else [])
            vals = [project, name, int(archived)] + list(cols.values()) + ([json.dumps(extra, ensure_ascii=False)] if extra else [])
            conn.execute(f"INSERT INTO agents ({','.join(names)}) VALUES ({','.join('?' * len(names))})", vals)
            counter.imported += 1
        guarded(conn, counter, f"agent {i}", one)


ROUNDS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)$")
EVENTS_RE = re.compile(r"^(\d{2}):(\d{2})\|([EO])\|(.*)$")


def legacy_event_keys(conn, project) -> set:
    """Keys of events already imported from legacy logs."""
    keys = set()
    for r in conn.execute("SELECT data_json FROM events WHERE project=? AND kind IN ('rounds','events') "
                          "AND data_json LIKE '%legacy_key%'", (project,)):
        try:
            keys.add(json.loads(r[0]).get("legacy_key"))
        except (ValueError, AttributeError):
            pass
    return keys


def import_rounds(conn, project, path, counter) -> None:
    """Import ROUNDS.log lines ('YYYY-MM-DD HH:MM:SS text') as events of kind rounds."""
    seen = legacy_event_keys(conn, project)
    occurrence = {}
    for n, line in enumerate(read_text(path).splitlines(), 1):
        if not line.strip():
            continue

        def one(line=line, n=n):
            """Insert one rounds event unless it exists."""
            m = ROUNDS_RE.match(line)
            if not m:
                raise ValueError("no timestamp")
            occurrence[line] = occurrence.get(line, 0) + 1
            key = digest("rounds", line, occurrence[line])
            if key in seen:
                counter.skipped += 1
                return
            conn.execute("INSERT INTO events (ts, project, kind, text, data_json) VALUES (?,?,?,?,?)",
                         (f"{m.group(1)}T{m.group(2)}", project, "rounds", m.group(3),
                          json.dumps({"legacy_key": key, "source": "ROUNDS.log"})))
            seen.add(key)
            counter.imported += 1
        guarded(conn, counter, f"ROUNDS.log line {n}", one)


def import_events_log(conn, project, path, counter) -> None:
    """Import ORCH-EVENTS.log lines ('HH:MM|O or E|text', no date): dates are derived backwards from the file time."""
    seen = legacy_event_keys(conn, project)
    stamp = datetime.fromtimestamp(os.path.getmtime(path))
    lines = [(n, l) for n, l in enumerate(read_text(path).splitlines(), 1) if l.strip()]
    occurrence = {}
    keys = {}
    for n, line in lines:
        occurrence[line] = occurrence.get(line, 0) + 1
        keys[n] = digest("events", line, occurrence[line])
    day = stamp.date()
    later = (stamp.hour, stamp.minute)
    stamps = {}
    for n, line in reversed(lines):
        m = EVENTS_RE.match(line)
        if not m:
            continue
        clock = (int(m.group(1)), int(m.group(2)))
        if clock > later:
            day = day - timedelta(days=1)
        later = clock
        stamps[n] = f"{day.isoformat()}T{clock[0]:02d}:{clock[1]:02d}:00"
    for n, line in lines:
        def one(line=line, n=n):
            """Insert one events-log event unless it exists."""
            m = EVENTS_RE.match(line)
            if not m or n not in stamps:
                raise ValueError("not an HH:MM|O|text line")
            if keys[n] in seen:
                counter.skipped += 1
                return
            conn.execute("INSERT INTO events (ts, project, kind, text, data_json) VALUES (?,?,?,?,?)",
                         (stamps[n], project, "events", m.group(4),
                          json.dumps({"legacy_key": keys[n], "source": "ORCH-EVENTS.log", "error": m.group(3) == "E"})))
            seen.add(keys[n])
            counter.imported += 1
        guarded(conn, counter, f"ORCH-EVENTS.log line {n}", one)


def import_builds(conn, project, path, counter) -> None:
    """Import BUILD-JOBS.json ({'host|project': perJobMb}) as build measurements of this project."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    ts = mtime_iso(path)
    for key, value in data.items():
        def one(key=key, value=value):
            """Insert one build measurement unless it exists."""
            per_job = float(value)
            host = str(key).split("|")[0]
            if conn.execute("SELECT 1 FROM build_measurements WHERE project=? AND host=? AND per_job_mb=?",
                            (project, host, per_job)).fetchone():
                counter.skipped += 1
                return
            conn.execute("INSERT INTO build_measurements (host, project, ts, per_job_mb) VALUES (?,?,?,?)",
                         (host, project, ts, per_job))
            counter.imported += 1
        guarded(conn, counter, f"build {key}", one)


def import_usage(conn, project, path, counter) -> None:
    """Import BRAIN-USAGE.jsonl lines as providers_usage rows of kind brain."""
    for n, line in enumerate(read_text(path).splitlines(), 1):
        if not line.strip():
            continue

        def one(line=line):
            """Insert one usage row unless it exists."""
            o = json.loads(line)
            if not isinstance(o, dict) or not o.get("ts"):
                raise ValueError("no ts")
            row = (o["ts"], o.get("model"), int_or_none(o.get("input")), int_or_none(o.get("output")), "brain", project,
                   o.get("trigger"), int_or_none(o.get("context")), o.get("durationSec"), o.get("actions"),
                   int(bool(o.get("error"))))
            if conn.execute("SELECT 1 FROM providers_usage WHERE kind='brain' AND project=? AND ts=? AND "
                            "tokens_in IS ? AND tokens_out IS ? AND trigger IS ?",
                            (project, row[0], row[2], row[3], row[6])).fetchone():
                counter.skipped += 1
                return
            conn.execute("INSERT INTO providers_usage (ts, model, tokens_in, tokens_out, kind, project, trigger, "
                         "context, duration_s, actions, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
            counter.imported += 1
        guarded(conn, counter, f"BRAIN-USAGE.jsonl line {n}", one)


def import_handled(conn, project, path, counter) -> None:
    """Import ORCH-HANDLED.json ({key: 'yyyy-MM-dd HH:mm'}) into handled."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    for key, ts in data.items():
        def one(key=key, ts=ts):
            """Insert one handled key unless it exists."""
            cur = conn.execute("INSERT OR IGNORE INTO handled (project, key, ts) VALUES (?,?,?)",
                               (project, str(key), None if ts is None else str(ts)))
            if cur.rowcount:
                counter.imported += 1
            else:
                counter.skipped += 1
        guarded(conn, counter, f"handled {key}", one)


def import_nofollow(conn, project, path, counter) -> None:
    """Import NOFOLLOW.json ({'keys': [...]}) into nofollow (keys lower case)."""
    data = read_json(path)
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        raise ValueError("expected {'keys': [...]}")
    for key in keys:
        def one(key=key):
            """Insert one frozen key unless it exists."""
            if not isinstance(key, str) or not key.strip():
                raise ValueError("empty key")
            cur = conn.execute("INSERT OR IGNORE INTO nofollow (project, key) VALUES (?,?)", (project, key.strip().lower()))
            if cur.rowcount:
                counter.imported += 1
            else:
                counter.skipped += 1
        guarded(conn, counter, f"nofollow {key}", one)


def import_kv(conn, project, path, key, counter) -> None:
    """Store a JSON state file as kv text under the given key; an existing key is kept."""
    value = json.dumps(read_json(path), ensure_ascii=False, separators=(",", ":"))
    if conn.execute("SELECT 1 FROM kv WHERE project=? AND key=?", (project, key)).fetchone():
        counter.skipped += 1
        return
    conn.execute("INSERT INTO kv (project, key, value, updated_at) VALUES (?,?,?,?)", (project, key, value, db.now()))
    counter.imported += 1


# ---- questions, answers, queue ----

def parse_questions(text) -> list:
    """Parse v1 question blocks ('## Q-id' followed by '- Plan:', '- Item:', '- Question:', ...) into dicts."""
    blocks, cur, last = [], None, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(Q-\S+)", line)
        if m:
            cur = {"id": m.group(1)}
            blocks.append(cur)
            last = None
            continue
        if re.match(r"^#{1,2}\s", line):
            cur, last = None, None
            continue
        if cur is None:
            continue
        m = re.match(r"^-\s*(Plan|Item|Question|Recommendation|Status|Answer[^:]*):\s*(.*)$", line)
        if m:
            last = m.group(1).lower() if not m.group(1).startswith("Answer") else "answer"
            cur[last] = m.group(2).strip()
        elif re.match(r"^-\s*[A-Za-z][^:]*:", line):
            last = None
        elif last and line.strip():
            cur[last] = (cur[last] + " " + line.strip()).strip()
    return blocks


def import_questions(conn, project, path, counter) -> None:
    """Import v1 question blocks; the key is (plan, item, question); ANSWERED blocks become answered by owner."""
    for b in parse_questions(read_text(path)):
        def one(b=b):
            """Insert one question unless it exists."""
            question = b.get("question", "")
            if not question:
                raise ValueError(f"{b['id']}: no question text")
            status = b.get("status", "").upper()
            if status not in ("OPEN", "ANSWERED"):
                raise ValueError(f"{b['id']}: unknown status {status!r}")
            plan_path = slash(b.get("plan"))
            item = b.get("item", "")
            if conn.execute("SELECT 1 FROM questions WHERE project=? AND plan_path IS ? AND item IS ? AND question=?",
                            (project, plan_path, item, question)).fetchone():
                counter.skipped += 1
                return
            dm = re.match(r"Q-(\d{4})(\d{2})(\d{2})-", b["id"])
            created = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}T00:00:00" if dm else db.now()
            answered = status == "ANSWERED"
            conn.execute(
                "INSERT INTO questions (project, plan_path, item, question, recommendation, status, answer, answered_by, "
                "created_at, answered_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, plan_path, item, question, b.get("recommendation", ""), "answered" if answered else "open",
                 b.get("answer") if answered else None, "owner" if answered else None, created,
                 created if answered else None))
            counter.imported += 1
        guarded(conn, counter, b["id"], one)


def parse_answers(text) -> list:
    """Split a v1 answers file at '## ' headings into (heading, body) pairs."""
    sections, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            cur = [m.group(1), []]
            sections.append(cur)
        elif re.match(r"^#\s", line):
            cur = None
        elif cur is not None:
            cur[1].append(line.rstrip())
    return [(h, "\n".join(b).strip()) for h, b in sections]


def import_answers(conn, project, path, counter) -> None:
    """Import v1 answer sections as owner decisions; the key is the full section text."""
    for heading, body in parse_answers(read_text(path)):
        def one(heading=heading, body=body):
            """Insert one decision unless it exists."""
            if not heading:
                raise ValueError("empty heading")
            text = heading + ("\n" + body if body else "")
            if conn.execute("SELECT 1 FROM decisions WHERE project=? AND source='owner' AND text=?",
                            (project, text)).fetchone():
                counter.skipped += 1
                return
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", heading)
            ts = f"{dm.group(1)}T00:00:00" if dm else mtime_iso(path)
            conn.execute("INSERT INTO decisions (project, ts, source, text) VALUES (?,?,'owner',?)", (project, ts, text))
            counter.imported += 1
        guarded(conn, counter, heading, one)


def queue_status(cell):
    """Map a v1 status cell ('ready', 'in Lauf', 'integriert (...)', 'blockiert (...)') to a queue status, or None."""
    low = cell.strip().lower()
    for prefix, status in QUEUE_STATUS:
        if low == prefix or low.startswith(prefix + " ") or low.startswith(prefix + "("):
            return status
    return None


def parse_queue(text) -> list:
    """Parse the v1 queue table into dicts (branch, tip, plan, status_cell, order, date); header cells pick the columns.

    Rows are read once a header whose first cell is 'Branch' was seen. A table with another header (for example a run history) is skipped up to the next heading;
    rows without a header of their own below a heading still count when they have at least as many cells as the 'Branch' header minus the last one (a row may lack its date), shorter rows are skipped.
    """
    rows, cols, in_table, skip, width = [], None, False, False, 0
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if re.match(r"^\s*#", line):
            skip = False
        if not line.lstrip().startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        starts_table = not in_table
        in_table = True
        if starts_table and not (cells and cells[0].lower() == "branch") and n + 1 < len(lines) \
                and re.match(r"^\s*\|[-:| ]+\|?\s*$", lines[n + 1]):
            skip = True   # a table with another header (for example a history table) is not the queue
        if cells and cells[0].lower() == "branch":
            skip = False
            width = len(cells)
            cols = {"branch": 0, "tip": 1, "plan": 2, "status": 3, "order": 4, "date": 5}
            for i, c in enumerate(cells):
                low = c.lower()
                for key, pat in (("tip", "tip"), ("plan", "plan"), ("status", "status"), ("order", "reihenfolge"),
                                 ("date", "datum")):
                    if pat in low:
                        cols[key] = i
            continue
        if cols is None or skip or len(cells) < width - 1 or all(re.match(r"^[-: ]*$", c) for c in cells):
            continue
        row = {k: (cells[i] if i < len(cells) else "") for k, i in cols.items()}
        rows.append(row)
    return rows


def import_queue(conn, project, path, counter) -> None:
    """Import the v1 queue table; the key is (branch, tip); the status text after the status word goes into the note."""
    for row in parse_queue(read_text(path)):
        def one(row=row):
            """Insert one queue row unless it exists."""
            if not row["branch"]:
                raise ValueError("no branch")
            status = queue_status(row["status"])
            if status is None:
                raise ValueError(f"{row['branch']}: unknown status {row['status'][:30]!r}")
            if conn.execute("SELECT 1 FROM queue WHERE project=? AND branch=? AND tip IS ?",
                            (project, row["branch"], row["tip"])).fetchone():
                counter.skipped += 1
                return
            note = " | ".join(p for p in (row["status"] if row["status"].lower() != "ready" else "", row["order"]) if p)
            dm = re.search(r"\d{4}-\d{2}-\d{2}", row["date"])
            conn.execute("INSERT INTO queue (project, branch, tip, plan_path, status, note, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (project, row["branch"], row["tip"], slash(row["plan"]), status, note,
                          f"{dm.group(0)}T00:00:00" if dm else None))
            counter.imported += 1
        guarded(conn, counter, row.get("branch", "?"), one)


# ---- plans ----

def import_plans(conn, project, patterns, workspace, counter) -> None:
    """Import v1 plan files matching the globs: status from line 2, packages from the v1 table; existing rows are kept."""
    files = set()
    for pattern in patterns:
        for p in glob.glob(pattern, recursive=True):
            if os.path.isfile(p):
                files.add(os.path.normpath(p))
    for f in sorted(files):
        def one(f=f):
            """Insert one plan and its packages unless the plan exists."""
            plan = plan_mod.read_legacy_plan(f)
            rel = f
            if workspace:
                try:
                    rel = os.path.relpath(f, workspace)
                except ValueError:
                    rel = f
            rel = slash(rel)
            cur = conn.execute("INSERT OR IGNORE INTO plans (project, path, title, status, updated_at) VALUES (?,?,?,?,?)",
                               (project, rel, plan.title, plan.status, mtime_iso(f)))
            if not cur.rowcount:
                counter.skipped += 1
                return
            for pkg in plan.packages:
                conn.execute("INSERT OR IGNORE INTO packages (project, plan_path, id, title, class, agent_type, capability, depends_on) "
                             "VALUES (?,?,?,?,?,?,?,?)",
                             (project, rel, pkg.id, pkg.title, pkg.cls, pkg.agent, pkg.capability,
                              json.dumps(pkg.depends_on)))
            counter.imported += 1
        guarded(conn, counter, f, one)


# ---- driver ----

def run_source(conn, results, name, fn) -> None:
    """Run one source import in its own transaction; a failure of the whole source is counted as one error."""
    counter = Counter()
    try:
        with db.transaction(conn):
            fn(counter)
    except Exception as exc:
        counter.error(f"{name}: {exc}")
    results[name] = counter.result()


def import_legacy(conn, project, runtime_dir=None, questions=None, answers=None, queue=None, plans=None) -> dict:
    """Import all given v1 sources into the database and return per source {imported, skipped, errors}."""
    if not project:
        raise CmdError("project is required")
    with db.transaction(conn):
        conn.execute("INSERT OR IGNORE INTO projects (name, priority, registered_at) VALUES (?,0,?)", (project, db.now()))
    ws = conn.execute("SELECT workspace FROM projects WHERE name=?", (project,)).fetchone()[0]
    results = {}
    if runtime_dir:
        base = Path(runtime_dir)
        if not base.is_dir():
            raise CmdError(f"runtime folder not found: {runtime_dir}")
        sources = (
            ("AGENTS.json", lambda c, p: import_agents(conn, project, p, False, c)),
            ("AGENTS-ARCHIVE.json", lambda c, p: import_agents(conn, project, p, True, c)),
            ("ROUNDS.log", lambda c, p: import_rounds(conn, project, p, c)),
            ("ORCH-EVENTS.log", lambda c, p: import_events_log(conn, project, p, c)),
            ("BUILD-JOBS.json", lambda c, p: import_builds(conn, project, p, c)),
            ("BRAIN-USAGE.jsonl", lambda c, p: import_usage(conn, project, p, c)),
            ("ORCH-HANDLED.json", lambda c, p: import_handled(conn, project, p, c)),
            ("NOFOLLOW.json", lambda c, p: import_nofollow(conn, project, p, c)),
        ) + tuple((fname, (lambda c, p, k=key: import_kv(conn, project, p, k, c))) for fname, key in KV_FILES)
        for fname, fn in sources:
            path = base / fname
            if path.is_file():
                run_source(conn, results, fname, lambda c, fn=fn, path=path: fn(c, path))
    for name, path, fn in (("questions", questions, import_questions), ("answers", answers, import_answers),
                           ("queue", queue, import_queue)):
        if path:
            if not Path(path).is_file():
                raise CmdError(f"{name} file not found: {path}")
            run_source(conn, results, name, lambda c, fn=fn, path=path: fn(conn, project, path, c))
    if plans:
        run_source(conn, results, "plans", lambda c: import_plans(conn, project, plans, ws, c))
    return {"ok": True, "project": project, "sources": results}


def h_import_legacy(a, c):
    """import-legacy: run the import with the command line arguments."""
    return import_legacy(c, a.project, a.runtime, a.questions, a.answers, a.queue, a.plans)


def register(sub) -> None:
    """Add the import-legacy command to the sub-parser collection of orch.py."""
    from . import runtime_cli
    p = sub.add_parser("import-legacy")
    p.add_argument("--project", required=True)
    for flag in ("runtime", "questions", "answers", "queue"):
        p.add_argument(f"--{flag}")
    p.add_argument("--plans", action="append", help="glob of v1 plan files (repeatable)")
    p.set_defaults(fn=runtime_cli.run(h_import_legacy))
