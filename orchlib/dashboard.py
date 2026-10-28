"""Dashboard pane (port of dashboard.ps1 and dash-wrap.ps1): agents, limits, usage, system, plans, questions.

render(conn, ...) returns the whole screen as text with ANSI colours (testable with invented data);
main() redraws it every few seconds and reads the keys P (pause/resume), D (drain) and F (project filter).
Everything is read from the database; entry point: `python -m orchlib.dashboard [--project NAME] [--wrap]`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

if not __package__:   # started as a script: make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchlib import buildjobs, config, db, platform as osplat, runstate, runtime, usage  # noqa: E402

FULL, LIGHT, LINE = "█", "░", "─"
REFRESH_SECONDS = 5
FINISHED_HOURS = 1
HANDOFF_CONTEXT = 300000
ORCHESTRATOR_KINDS = ("agent", "brain")   # providers_usage kinds written by the orchestrator itself (the "all plans" row)

# Colour name (PowerShell console names as used by dashboard.ps1, lower case) -> ANSI SGR code.
COLOURS = {
    "black": "30", "darkred": "31", "darkgreen": "32", "darkyellow": "33", "darkblue": "34",
    "darkmagenta": "35", "darkcyan": "36", "gray": "37", "darkgray": "90", "red": "91", "green": "92",
    "yellow": "93", "blue": "94", "magenta": "95", "cyan": "96", "white": "97", "orange": "38;5;208",
}
RUN_STATE_COLOUR = {"RUNNING": "green", "PAUSED": "yellow", "DRAINING": "darkyellow", "DRAINED": "red"}
PHASE_SYMBOL = {"work": "✎", "verify": "⚲", "release": "⇪", "integrate": "⛁"}
STATUS_SYMBOL = {"running": "►", "starting": "◌", "done": "✓", "failed": "✗"}
STATUS_COLOUR = {"done": "green", "running": "yellow", "starting": "yellow", "failed": "red"}
PLAN_BADGES = (("DRAFT", "gray"), ("READY", "cyan"), ("IN_PROGRESS", "yellow"), ("DONE", "green"), ("PAUSED", "darkgray"))


# ---- formatting helpers ----

def fmt_tok0(n) -> str:
    """Whole-number token count for lists (75k, 2k, 1.5M); an en dash when unknown."""
    if n is None:
        return "–"
    if n >= 1e9:
        return "%.1fG" % (n / 1e9)
    if n >= 1e6:
        return "%.1fM" % (n / 1e6)
    if n >= 1e3:
        return "%dk" % int(n / 1e3 + 0.5)
    return str(int(n))


def fmt_tok(n) -> str:
    """Token count for the agent token line (12k, 1.5M); an en dash when unknown."""
    if n is None:
        return "–"
    if n >= 1e6:
        return "%.1fM" % (n / 1e6)
    if n >= 1e3:
        return "%dk" % int(n / 1e3 + 0.5)
    return str(int(n))


def token_line(ctx, tin, tout) -> str:
    """Token line of one agent: context, total input and output (or a no-data text)."""
    if not ((tin or 0) > 0 or (tout or 0) > 0 or (ctx or 0) > 0):
        return "Tokens: no data yet"
    return "Context %s | Total Input %s / Output %s" % (fmt_tok(ctx), fmt_tok(tin), fmt_tok(tout))


def level_colour(pct) -> str:
    """Colour by fill level: green under 60 %, yellow up to 85 %, red above."""
    return "green" if pct < 60 else ("yellow" if pct <= 85 else "red")


def model_short(provider, model, effort) -> str:
    """Short model text for the first agent line ("Son 5.5 med.", "Sol 6.1 med.")."""
    model, effort = model or "", effort or ""
    eff = "med." if effort == "medium" else effort
    if provider == "codex":
        m = re.match(r"^gpt-(\d+(?:\.\d+)?)-([a-z]+)", model)
        if m:
            return ("%s %s %s" % (m.group(2).title(), m.group(1), eff)).strip()
        return ("%s %s" % (model, eff)).strip()
    ver = ""
    if "sonnet" in model:
        nm, ver = "Son", "5.5"
    elif "opus" in model:
        nm, ver = "Opus", "5.5"
    elif "haiku" in model:
        nm, ver = "Hai", "5.5"
    else:
        nm = model
    m = re.search(r"(\d+)[-.](\d+)", model)
    if m:
        ver = m.group(1) + "." + m.group(2)
    return ("%s %s" % (nm, ver)).strip() + " " + eff


def format_reset(epoch, now) -> str:
    """Epoch seconds as local HH:MM (today) or DD.MM. HH:MM."""
    if not epoch:
        return ""
    d = datetime.fromtimestamp(int(epoch))
    return d.strftime("%H:%M") if d.date() == now.date() else d.strftime("%d.%m. %H:%M")


def sgr(colour) -> str:
    """ANSI start sequence for a colour name or an RRGGBB hex colour (agent colours); empty when unknown."""
    c = str(colour or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        return "\x1b[1;38;2;%d;%d;%dm" % (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    code = COLOURS.get(c.lower())
    return "\x1b[%sm" % code if code else ""


# ---- line model: a line is a list of (text, colour) parts ----

def wrap_parts(parts, w, out) -> None:
    """Wrap the parts of one logical line to width w: a part that does not fit moves to a new indented line,
    an over-long part is split hard. Nothing is cut. Appends the physical lines to `out`."""
    cur, used = [], 0
    for text, col in parts:
        t = text
        if used > 0 and used + len(t) > w and len(t.lstrip()) + 2 <= w:
            out.append(cur)
            cur, used = [], 0
            t = "  " + t.lstrip()
        while used + len(t) > w:
            room = w - used
            if room <= 0:
                out.append(cur)
                cur, used = [], 0
                continue
            cur.append((t[:room], col))
            out.append(cur)
            cur, used = [], 0
            t = "  " + t[room:].lstrip()
        if t:
            cur.append((t, col))
            used += len(t)
    if cur:
        out.append(cur)


def physical(lines, w) -> list:
    """Expand logical lines to physical lines for width w."""
    out = []
    for ln in lines:
        wrap_parts(ln, w, out)
    return out


def paint(lines, color=True) -> str:
    """Join physical lines to one text; with colour each part gets its ANSI sequence."""
    rows = []
    for ln in lines:
        if color:
            rows.append("".join((sgr(c) + t + "\x1b[0m") if sgr(c) else t for t, c in ln))
        else:
            rows.append("".join(t for t, _ in ln))
    return "\n".join(rows)


# ---- data collection (database only) ----

def _parse_time(text):
    """Parse an ISO time written by the runtime; None when missing or broken."""
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _kv(conn, key):
    """Return the parsed JSON of a kv key, preferring the global project `*`, else the newest row; None if missing."""
    row = conn.execute(
        "SELECT value FROM kv WHERE key=? ORDER BY (project=?) DESC, updated_at DESC LIMIT 1",
        (key, runstate.GLOBAL_PROJECT)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def window_usage(raw, now):
    """Return {FiveHour, SevenDay} from a usage kv value ({fiveHour|sevenDay: {utilization, resetsAt}}).

    Each entry is {pct, reset} or None; a window whose reset time has passed shows "new window" without percent."""
    res = {"FiveHour": None, "SevenDay": None}
    if not isinstance(raw, dict):
        return res
    t = int(now.timestamp())
    for key, name in (("fiveHour", "FiveHour"), ("sevenDay", "SevenDay")):
        w = raw.get(key)
        if not isinstance(w, dict):
            continue
        try:
            if int(w.get("resetsAt") or 0) > t:
                res[name] = {"pct": round(100 * float(w.get("utilization"))), "reset": format_reset(w["resetsAt"], now)}
            else:
                res[name] = {"pct": None, "reset": "new window"}
        except (TypeError, ValueError):
            continue
    return res


def blocked_tasks(agents) -> list:
    """Tasks (plan|package|phase) whose last two starts failed with the same error: blocked, never restarted."""
    groups = {}
    for a in agents:
        if a["status"] == "failed":
            groups.setdefault((a.get("plan_path"), a.get("package"), a.get("phase")), []).append(a)
    res = []
    for rows in groups.values():
        rows.sort(key=lambda r: r.get("started_at") or "")
        if len(rows) < 2:
            continue
        a, b = rows[-1], rows[-2]
        last = str(a.get("last_line") or "")
        if last == str(b.get("last_line") or "") and not last.startswith("pane never started"):
            res.append({"name": a.get("name") or "", "error": last, "project": a["project"]})
    return res


def build_jobs_line(conn, cfg, projects, agents, sysinfo) -> str:
    """SYSTEM line for build jobs, or None without a buildJobs setting.

    "Build jobs N of M cores (next build) | per job X MB | active: N cores"; the active part appears while a build
    agent runs or a compiler process (buildProcessNames of project.toml) is alive; no names = no display (C8)."""
    bj = cfg.get("buildJobs") or 0
    if not bj:
        return None
    cores = sysinfo["cores"]
    settings = buildjobs.settings_from(bj)
    marks = ",".join("?" * len(projects)) if projects else ""
    sql = "SELECT MAX(per_job_mb) FROM build_measurements WHERE host=?" + (" AND project IN (%s)" % marks if projects else "")
    per = conn.execute(sql, [sysinfo["host"]] + list(projects)).fetchone()[0]
    if settings["PerJobMB"] is not None:
        per = settings["PerJobMB"]
    running = [a for a in agents if a["status"] == "running" and a.get("buildJobs")]
    if running:
        text = "Build jobs %s of %d cores (running)" % (running[0]["buildJobs"], cores)
    else:
        jobs = buildjobs.job_count(settings, cores, sysinfo["free_mb"], per)["Jobs"]
        text = "Build jobs %d of %d cores (next build)" % (jobs, cores)
    text += " | per job " + ("%.0f MB" % per if per else "not measured yet")
    if sysinfo.get("compiler_count") is not None and (running or sysinfo["compiler_count"] > 0):
        text += " | active: %d cores" % sysinfo["compiler_count"]
    return text


def usage_models(conn, agents_all, today) -> list:
    """Per model: today, all plans and total tokens (input/output) from providers_usage and the agents table.

    Rows of the orchestrator itself (kind agent/brain) plus finished/running agent rows are the "all plans" value;
    today/total use the machine-wide rows (any other kind) when the model has some, else the orchestrator rows."""
    models = {}

    def cell(label):
        """Return the accumulator of one model."""
        return models.setdefault(label, {"Label": label, "logs": [0, 0, 0, 0, False, None], "orch": [0, 0, 0, 0, False, None]})

    def add(acc, ts, tin, tout):
        """Add one record to an accumulator: [total in, total out, today in, today out, any, since]."""
        acc[0] += tin
        acc[1] += tout
        if ts and ts >= today:
            acc[2] += tin
            acc[3] += tout
        acc[4] = True
        if ts and (acc[5] is None or ts < acc[5]):
            acc[5] = ts
    for r in conn.execute("SELECT ts, model, tokens_in, tokens_out, kind FROM providers_usage WHERE model IS NOT NULL"):
        acc = cell(r["model"])["orch" if r["kind"] in ORCHESTRATOR_KINDS else "logs"]
        add(acc, _parse_time(r["ts"]), r["tokens_in"] or 0, r["tokens_out"] or 0)
    for a in agents_all:
        if a.get("model") and a.get("tokens_in") is not None and a.get("tokens_out") is not None:
            add(cell(a["model"])["orch"], _parse_time(a.get("started_at")), a["tokens_in"], a["tokens_out"])
    out = []
    for m in models.values():
        use = m["logs"] if m["logs"][4] else m["orch"]
        orch = m["orch"]
        out.append({"Label": m["Label"], "Since": use[5],
                    "Today": (use[2], use[3]) if use[4] else None,
                    "Plans": (orch[0], orch[1]) if orch[4] else None,
                    "Total": (use[0], use[1]) if use[4] else None})
    out.sort(key=lambda m: m["Label"])
    return out


def collect(conn, project=None, now=None, cfg=None, sysinfo=None) -> dict:
    """Read everything the dashboard shows from the database (and the system values passed in `sysinfo`).

    `project` filters to one project (D4); `cfg` is the machine config (buildJobs); `sysinfo` holds
    cores, free_mb, total_mb, host and compiler_count (None = read the live values)."""
    now = now or datetime.now()
    cfg = cfg if cfg is not None else config.load_config()
    names = [r["name"] for r in conn.execute("SELECT name FROM projects ORDER BY priority DESC, name")]
    colours = {r["name"]: r["colour"] for r in conn.execute("SELECT name, colour FROM projects")}
    scope = [project] if project else names
    if sysinfo is None:
        sysinfo = live_sysinfo(conn, project)
    host = sysinfo["host"]
    try:
        usage.refresh_codex_usage(conn)
    except Exception:
        pass
    all_rows = runtime.list_agents(conn, project, "active")
    agents_all = runtime.list_agents(conn, project, "all")
    agents = sorted([a for a in all_rows if a["status"] in runtime.ACTIVE_STATUSES], key=lambda a: a.get("started_at") or "")
    fin = []
    for a in all_rows:
        end = _parse_time(a.get("ended_at"))
        if a["status"] not in runtime.ACTIVE_STATUSES and end and 0 <= (now - end).total_seconds() <= FINISHED_HOURS * 3600:
            fin.append(a)
    fin.sort(key=lambda a: a["ended_at"], reverse=True)
    pq, pa = ("WHERE project=?", (project,)) if project else ("", ())
    counts = {s: 0 for s, _ in PLAN_BADGES}
    drafts = []
    for p in conn.execute("SELECT path, status FROM plans " + pq, pa):
        counts[p["status"]] = counts.get(p["status"], 0) + 1
        if p["status"] == "DRAFT":
            drafts.append(re.sub(r"-?PLAN$", "", Path(p["path"]).stem))
    brain = db.rows(conn, "SELECT * FROM providers_usage WHERE kind='brain'" + (" AND project=?" if project else "") + " ORDER BY ts", pa)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    brain_today = [b for b in brain if (_parse_time(b["ts"]) or day) >= day and not b.get("error")]
    last_call = next((b for b in reversed(brain) if b.get("tokens_in") is not None), None)
    loop = _kv(conn, "loop-state") or {}
    since = _parse_time(loop.get("since")) if isinstance(loop, dict) else None
    if isinstance(loop, dict) and loop.get("status") == "brain":
        orch_status = "brain call running"
    elif since:
        orch_status = "idle since " + since.strftime("%H:%M")
    else:
        orch_status = "idle"
    last_round = conn.execute("SELECT ts FROM events WHERE kind='round'" + (" AND project=?" if project else "")
                              + " ORDER BY id DESC LIMIT 1", pa).fetchone()
    return {
        "now": now, "project": project, "projects": scope, "colours": colours, "cfg": cfg, "sysinfo": sysinfo,
        "run": runstate.get_state(conn), "agents": agents, "fin": fin, "blocked": blocked_tasks(agents_all),
        "cu": window_usage(_kv(conn, "usage-claude"), now), "xu": window_usage(_kv(conn, "usage-codex"), now),
        "usage": usage_models(conn, agents_all, day), "counts": counts, "drafts": drafts,
        "status": db.project_status(conn, project),
        "qs": db.open_questions(conn, project, only_open=True),
        "bj": build_jobs_line(conn, cfg, scope, all_rows, sysinfo), "host": host,
        "orch": {"status": orch_status, "calls": len(brain_today), "model": (last_call or {}).get("model"),
                 "ctx": (last_call or {}).get("context"),
                 "tin": sum(b["tokens_in"] or 0 for b in brain_today), "tout": sum(b["tokens_out"] or 0 for b in brain_today)},
        "last_round": (_parse_time(last_round[0]).strftime("%H:%M:%S") if last_round and _parse_time(last_round[0]) else "-"),
    }


def live_sysinfo(conn, project=None) -> dict:
    """Read the live system values: cores, free and total RAM in MB, host name and running compiler processes
    (names from buildProcessNames of the project.toml of the shown projects; none = no count)."""
    names = []
    rows = conn.execute("SELECT name, workspace FROM projects" + (" WHERE name=?" if project else ""),
                        (project,) if project else ()).fetchall()
    for r in rows:
        try:
            if r["workspace"]:
                names += list(config.load_project(r["workspace"]).get("buildProcessNames") or [])
        except Exception:   # a broken project file must not stop the dashboard
            continue
    names = sorted(set(names))
    return {"cores": osplat.logical_cores(), "free_mb": osplat.free_ram_mb(), "total_mb": _total_ram_mb(),
            "host": osplat.host_name(), "compiler_count": osplat.count_processes(names) if names else None}


def _total_ram_mb() -> int:
    """Total physical memory in MB (0 when unknown)."""
    try:
        if osplat.is_windows():
            import ctypes

            class Status(ctypes.Structure):
                """MEMORYSTATUSEX."""
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                            ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]
            st = Status()
            st.l = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.total // (1024 * 1024))
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, AttributeError, ValueError):
        pass
    return 0


# ---- building the lines ----

def build_lines(level, d, w, n_fin) -> list:
    """Build all logical lines for one detail level (0 = every model, 1 = models active today, 3 = at most two)
    with the newest `n_fin` ended agents; `d` is the result of collect()."""
    L = []
    now, colours = d["now"], d["colours"]

    def head(title, col="cyan", extra=""):
        """Section heading: "-- TITLE ------" over the full width."""
        used = 3 + len(title) + len(extra) + 1
        L.append([(LINE * 2 + " ", "darkgray"), (title, col), (extra, col), (" " + LINE * max(1, w - used), "darkgray")])

    def limit(label, entry):
        """One limit line: label, 10 character bar, percent and reset time."""
        lab = label.ljust(10)
        if entry is None:
            L.append([(lab, "gray"), ("n/a", "darkgray")])
        elif entry["pct"] is None:
            L.append([(lab, "gray"), (entry["reset"], "darkgray")])
        else:
            col = level_colour(entry["pct"])
            f = int(round(10 * min(100, max(0, entry["pct"])) / 100))
            L.append([(lab, "gray"), (FULL * f, col), (LIGHT * (10 - f), "darkgray"),
                      (" %3d%% %s" % (entry["pct"], entry["reset"]), col)])

    run = d["run"]
    sub = {"RUNNING": "", "PAUSED": "no new agents", "DRAINING": "waiting for %d agent(s)" % run["running_agents"],
           "DRAINED": "no agent runs, safe to shut down"}[run["state"]]
    L.append([(run["state"], RUN_STATE_COLOUR[run["state"]]), ("  " + sub if sub else "", "gray"),
              ("  project: %s" % (d["project"] or "all"), "gray"), ("  [P] pause/resume [D] drain [F] project", "darkgray")])
    o = d["orch"]
    L.append([("Orchestrator ", "cyan"), (o["status"], "gray"),
              (" | brain calls today %d | context %s | in %s / out %s" % (o["calls"], fmt_tok(o["ctx"]), fmt_tok0(o["tin"]), fmt_tok0(o["tout"])), "gray")])

    head("AGENTS %d" % len(d["agents"]), "cyan", "  %s  last round %s" % (now.strftime("%H:%M:%S"), d["last_round"]))
    if not d["agents"] and not d["blocked"] and not d["fin"]:
        L.append([("no agents", "darkgray")])
    for b in d["blocked"]:
        L.append([("BLOCKED %s: %s" % (b["name"], b["error"]), "red")])

    def agent(a):
        """One agent in the three-line format of the pane header, with project name and colour (D4)."""
        st = str(a["status"])
        start, end = _parse_time(a.get("started_at")), _parse_time(a.get("ended_at"))
        rt = ""
        if start:
            secs = int(((end or now) - start).total_seconds())
            rt = "%d:%02d:%02d" % (secs // 3600, secs % 3600 // 60, secs % 60) if secs >= 3600 else "%02d:%02d" % (secs // 60, secs % 60)
        typ = {"claude": "Claude", "codex": "Codex"}.get(a.get("provider"), str(a.get("provider") or ""))
        ms = model_short(a.get("provider"), a.get("model"), a.get("effort"))
        pn = str(a.get("person") or "-").upper()
        pcol = colours.get(a["project"]) or "white"
        proj = a["project"] + " | "
        tail = ((STATUS_SYMBOL.get(st, "‖") + " " + st + " " + rt).strip())
        mid = "%s %s" % (typ, ms)
        ident = str(a.get("name") or "")
        own_line = False
        room = w - len(proj + pn + " | " + " | " + mid + " | " + tail)
        if len(ident) > room:
            if room >= 20:
                ident = ident[:room - 3] + "..."
            else:
                ident, own_line = "", True
        left = "%s | %s | %s | " % (pn, ident, mid) if ident else "%s | %s | " % (pn, mid)
        if len(proj) + len(left) + len(tail) > w:
            tail = tail[:max(0, w - len(proj) - len(left) - 3)] + "..."
        L.append([(proj, pcol), (left, a.get("color") or "FFFFFF"), (tail, STATUS_COLOUR.get(st, "darkgray"))])
        if own_line:
            full = str(a.get("name") or "")
            L.append([(full if len(full) <= w else full[:w - 3] + "...", a.get("color") or "FFFFFF")])
        task = a.get("task") or ("%s %s" % (Path(str(a.get("plan_path") or "")).name, a.get("package") or "")).strip()
        task = re.sub(r"\s+", " ", ("%s %s" % (PHASE_SYMBOL.get(a.get("phase"), ""), task)).strip())
        L.append([(task if len(task) <= w else task[:w - 3] + "...", "gray")])
        ctx = a.get("ctxTokens")
        over = ctx is not None and int(ctx) >= HANDOFF_CONTEXT
        tl = ("▲ " if over else "") + token_line(ctx, a.get("tokens_in"), a.get("tokens_out"))
        L.append([(tl if len(tl) <= w else tl[:w - 3] + "...", "red" if over else "white")])

    shown = d["agents"] + d["fin"][:n_fin]
    head_done = False
    for a in shown:
        if not head_done and a["status"] not in ("running", "starting"):
            L.append([("ended in the last hour", "darkgray")])
            head_done = True
        agent(a)
    hidden = d["fin"][n_fin:]
    if hidden:
        L.append([("more ended: %d, of which failed: %d" % (len(hidden), sum(1 for a in hidden if a["status"] == "failed")), "darkgray")])

    head("◔ LIMITS")
    limit("Claude 5h", d["cu"]["FiveHour"])
    limit("Claude 7d", d["cu"]["SevenDay"])
    if d["xu"]["FiveHour"]:
        limit("Codex 5h", d["xu"]["FiveHour"])
    limit("Codex 7d", d["xu"]["SevenDay"])

    head("Σ USAGE")
    models = [m for m in d["usage"] if level == 0 or (m["Today"] and m["Today"][0] > 0) or m["Plans"]]
    n_hidden = 0
    if level >= 3 and len(models) > 2:
        n_hidden, models = len(models) - 2, models[:2]
    if not models:
        L.append([("no data", "darkgray")])
    else:
        for m in models:
            m["Label"] = m["Label"].replace("codex-auto-review", "Auto-Review")
        since = sorted(m["Since"] for m in models if m["Since"])
        rows = ["today", "all plans", "total since " + since[0].strftime("%d.%m.") if since else "total"]

        def cell(v):
            """Text of one table cell."""
            return "no data" if v is None else "Total Input %s / Output %s" % (fmt_tok0(v[0]), fmt_tok0(v[1]))
        cells = {m["Label"]: [cell(m["Today"]), cell(m["Plans"]), cell(m["Total"])] for m in models}
        lw = max(len(r) for r in rows)
        cw = {m["Label"]: max(len(m["Label"]), max(len(c) for c in cells[m["Label"]])) for m in models}
        if lw + sum(2 + cw[m["Label"]] for m in models) <= w:
            L.append([("".ljust(lw), "gray")] + [("  " + m["Label"].rjust(cw[m["Label"]]), "cyan") for m in models])
            for i in range(3):
                L.append([(rows[i].ljust(lw), "gray")] + [("  " + cells[m["Label"]][i].rjust(cw[m["Label"]]), "white") for m in models])
        else:
            cwa = max(len(c) for m in models for c in cells[m["Label"]])
            lab_all = ["today", "all plans", "since 00.00."]
            lw2 = max(len(x) for x in lab_all)
            one_line = (2 + lw2 + 1 + cwa) <= w
            for m in models:
                L.append([(m["Label"], "cyan")])
                labs = [rows[0], rows[1], "since " + m["Since"].strftime("%d.%m.") if m["Since"] else "total"]
                for i in range(3):
                    if one_line:
                        L.append([("  " + labs[i].ljust(lw2), "gray"), (" " + cells[m["Label"]][i].rjust(cwa), "white")])
                    else:
                        L.append([("  " + labs[i], "gray")])
                        L.append([(cells[m["Label"]][i].rjust(w), "white")])
        if n_hidden:
            L.append([("more models: %d" % n_hidden, "darkgray")])

    head("▣ SYSTEM")
    si = d["sysinfo"]
    tot, free = si["total_mb"] / 1024.0, si["free_mb"] / 1024.0
    used = tot - free
    pct = int(round(100 * used / tot)) if tot else 0
    L.append([(("▲ " if tot and free < 2 else "") + "RAM %d%% %.1f/%.0f GB" % (pct, used, tot), level_colour(pct))])
    if d["bj"]:
        L.append([(d["bj"], "gray")])

    head("☰ PLANS")
    pl = [("%s %d  " % (name, d["counts"].get(name, 0)), col) for name, col in PLAN_BADGES]
    if d["counts"].get("DRAFT"):
        pl.append(("(draft: %s)" % ", ".join(d["drafts"]), "darkyellow"))
    L.append(pl)
    for st in d["status"]:
        L.append([(st["project"], colours.get(st["project"]) or "white"),
                  (" %d / %d integrated (%d%%)" % (st["integrated"], st["total"], st["percent"]), "gray")])
    if not d["qs"]:
        head("? QUESTIONS")
        L.append([("none open", "darkgray")])
    else:
        head("! QUESTIONS", "red")
        L.append([("open: %d  %s" % (len(d["qs"]), ", ".join("%s#%s" % (q["project"], q["id"]) for q in d["qs"])), "yellow")])
    return L


def render(conn, width=80, height=40, project=None, now=None, cfg=None, sysinfo=None, color=True) -> str:
    """Return the whole dashboard as text (ANSI colours when `color`), at most `height` lines of `width` characters.

    The first detail level whose line count fits is used; when the height is short the list of ended agents shrinks first."""
    d = collect(conn, project, now, cfg, sysinfo)
    lines, fits = [], False
    for lv in (0, 1, 2, 3):
        for nf in range(len(d["fin"]), -1, -1):
            lines = physical(build_lines(lv, d, width, nf), width)
            if len(lines) <= height:
                fits = True
                break
        if fits:
            break
    return paint(lines[:height], color)


# ---- keys and main loop ----

class KeyReader:
    """Reads single key presses without Enter on Windows (msvcrt) and POSIX (termios); use as a context manager."""

    def __enter__(self):
        """Switch a POSIX terminal to cbreak mode (Windows needs nothing)."""
        self.saved = None
        if not osplat.is_windows() and sys.stdin.isatty():
            import termios
            import tty
            self.saved = (sys.stdin.fileno(), termios.tcgetattr(sys.stdin.fileno()))
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *exc):
        """Restore the terminal."""
        if self.saved:
            import termios
            termios.tcsetattr(self.saved[0], termios.TCSADRAIN, self.saved[1])

    def get(self, timeout):
        """Wait up to `timeout` seconds for a key; returns it in lower case or "" when none came."""
        if osplat.is_windows():
            import msvcrt
            end = time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):   # function/arrow key: swallow its second code
                        msvcrt.getwch()
                        return ""
                    return ch.lower()
                if time.monotonic() >= end:
                    return ""
                time.sleep(0.05)
        if not sys.stdin.isatty():
            time.sleep(timeout)
            return ""
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1).lower() if ready else ""


def handle_key(conn, key, project, names):
    """Apply one key: P toggles pause/resume, D drains, F steps through the project filter. Returns the new filter."""
    if key == "p":
        runstate.pause(conn) if runstate.get_state(conn)["state"] == "RUNNING" else runstate.resume(conn)
    elif key == "d":
        runstate.drain(conn)
    elif key == "f":
        cycle = [None] + list(names)
        project = cycle[(cycle.index(project) + 1) % len(cycle)] if project in cycle else None
    return project


def screen_size():
    """Return (width, height) for drawing; ORCH_DASH_WIDTH / ORCH_DASH_HEIGHT override it (tests)."""
    size = shutil.get_terminal_size((100, 40))
    w = int(os.environ.get("ORCH_DASH_WIDTH") or max(20, size.columns - 1))
    h = int(os.environ.get("ORCH_DASH_HEIGHT") or max(5, size.lines - 1))
    return w, h


def run(project=None, once=False) -> int:
    """Open the database and redraw the dashboard until Ctrl+C (`once` draws one frame and returns)."""
    conn = db.connect(config.db_path())
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        stdout.reconfigure(encoding="utf-8")
    try:
        with KeyReader() as keys:
            while True:
                try:
                    w, h = screen_size()
                    text = render(conn, w, h, project)
                except Exception as exc:   # a failed redraw must not close the pane
                    text = "\x1b[91mdashboard error: %s\x1b[0m" % exc
                stdout.write(("" if os.environ.get("ORCH_DASH_WIDTH") else "\x1b[H\x1b[2J") + text + "\n")
                stdout.flush()
                if once:
                    return 0
                end = time.monotonic() + REFRESH_SECONDS
                while time.monotonic() < end:
                    key = keys.get(0.2)
                    if key:
                        names = [r["name"] for r in conn.execute("SELECT name FROM projects ORDER BY priority DESC, name")]
                        project = handle_key(conn, key, project, names)
                        break
    except KeyboardInterrupt:
        return 0
    finally:
        conn.close()


def wrap(project=None) -> int:
    """Run the dashboard as a child process and start it again when it ends (dash-wrap.ps1); stops when the file
    DASH-STOP exists in the data folder."""
    cmd = [sys.executable, "-I", str(Path(__file__).resolve())] + (["--project", project] if project else [])
    while True:
        subprocess.call(cmd)
        if (config.data_dir() / "DASH-STOP").exists():
            return 0
        time.sleep(1)


def main(argv=None) -> int:
    """Entry point: `python -m orchlib.dashboard [--project NAME] [--wrap] [--once]`."""
    args = list(sys.argv[1:] if argv is None else argv)
    project = None
    if "--project" in args:
        project = args[args.index("--project") + 1]
    if "--wrap" in args:
        return wrap(project)
    return run(project, once="--once" in args)


if __name__ == "__main__":
    sys.exit(main())
