"""Overview data of the orchestrator (no drawing): everything the graphical dashboard (haivequeen/gui.py, through haivequeen/gui_data.py) shows.

collect(conn, ...) reads agents, limits, usage, system values, plans and questions from the database in one plain dict; the helpers around it
format token counts, model names and reset times. Nothing here prints or opens a window.
"""
import json
import os
import re
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

from . import buildjobs, config, db, platform as osplat, runstate, runtime, scheduler as sch, usage

FINISHED_HOURS = 1
ORCHESTRATOR_KINDS = ("agent", "brain")   # providers_usage kinds written by the orchestrator itself (the "all plans" row)
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




def _parse_time(text):
    """Parse an ISO time written by the runtime; None when missing or broken."""
    return db.parse_time(text)


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


# Hide quota snapshots that could not be refreshed from the provider.
def window_usage(raw, now):
    """Return {FiveHour, SevenDay} from a usage kv value ({fiveHour|sevenDay: {utilization, resetsAt}}).

    Each entry is {pct, reset} or None; a window whose reset time has passed shows "new window" without percent."""
    res = {"FiveHour": None, "SevenDay": None}
    if not isinstance(raw, dict) or raw.get("refresh_error"):
        return res
    t = int(now.timestamp())
    for key, name in (("fiveHour", "FiveHour"), ("sevenDay", "SevenDay")):
        w = raw.get(key)
        if not isinstance(w, dict):
            continue
        try:
            if w.get("resetsAt") is None:
                res[name] = {"pct": round(100 * float(w["utilization"])), "reset": ""}
            elif int(w.get("resetsAt") or 0) > t:
                res[name] = {"pct": round(100 * float(w.get("utilization"))), "reset": format_reset(w["resetsAt"], now)}
            else:
                res[name] = {"pct": None, "reset": "new window"}
        except (TypeError, ValueError):
            continue
    return res


# Return recent tasks whose two newest runs failed with the same final line.
def blocked_tasks(agents, now=None) -> list:
    """Tasks with two newest nonarchived failed runs matching in error, recent within 12h, and no later run."""
    now = now or datetime.now()
    groups = {}
    for a in agents:
        if not a.get("archived"):
            groups.setdefault((a.get("project"), a.get("plan_path"), a.get("package"), a.get("phase")), []).append(a)
    res = []
    for rows in groups.values():
        rows.sort(key=lambda r: r.get("started_at") or "")
        if len(rows) < 2:
            continue
        a, b = rows[-1], rows[-2]
        ended = _parse_time(a.get("ended_at"))
        last = str(a.get("last_line") or "")
        if (a.get("status") == b.get("status") == "failed" and ended
                and timedelta(0) <= now - ended <= timedelta(hours=12)
                and last == str(b.get("last_line") or "") and not last.startswith("pane never started")):
            res.append({"name": a.get("name") or "", "error": last, "project": a["project"]})
    return res


def build_jobs_line(conn, cfg, projects, agents, sysinfo) -> str:
    """SYSTEM line for build jobs, or None without a project that has buildProcessNames (a missing buildJobs table means the defaults).

    "Build jobs N of M cores (running|next build) | per job X MB | active: N cores": N is the running job count during a build, else the count
    `bzz integration acquire` would hand out now; X is the median per job of the project. With several such projects every project gets its
    part, named by the project, in this one line. `sysinfo["build_projects"]` names the projects with buildProcessNames (default: all shown)."""
    settings = {"buildJobs": cfg.get("buildJobs") if cfg.get("buildJobs") is not None else 0}   # no table or 0 = the defaults, the line stays
    shown = [p for p in projects if p in (sysinfo.get("build_projects") if sysinfo.get("build_projects") is not None else projects)]
    parts = []
    for name in shown:
        mine = [a for a in agents if a.get("project") == name]
        env = types.SimpleNamespace(conn=conn, name=name, settings=settings)
        part = buildjobs.jobs_line(env, [a for a in mine if a["status"] == "running"], sysinfo.get("compiler_count") if len(shown) == 1 else 0,
                                   cores=sysinfo["cores"], free_mb=sysinfo.get("free_mb"), host=sysinfo.get("host"))
        if part and len(shown) > 1:
            part = name + ": " + part
        if part:
            parts.append(part)
    return "  ||  ".join(parts) if parts else None


def usage_models(conn, agents_all, today) -> list:
    """Per model: today, all plans and total tokens (input/output) from providers_usage and the agents table.

    Rows of the orchestrator itself (kind agent/brain) plus finished/running agent rows are the "all plans" value;
    today/total use the machine-wide rows (any other kind) when the model has some, else the orchestrator rows."""
    models = {}

    def cell(label):
        """Return the accumulator of one model."""
        key = runtime.model_key(label)   # stored rows may still carry the label "Sonnet 5.5" next to the id "claude-sonnet-5-5": one entry per model
        return models.setdefault(key, {"Label": sch.model_label(key) if key.startswith("claude-") else key, "logs": [0, 0, 0, 0, False, None], "orch": [0, 0, 0, 0, False, None]})

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
    # A row that names only the family ("Sonnet") is a second key of the one model of that family: its numbers join that model.
    for key in [k for k in models if re.fullmatch(r"(?i)sonnet|opus|haiku", str(k))]:
        full = [k for k in models if str(k).lower().startswith("claude-%s-" % key.lower())]
        if len(full) == 1:
            alias = models.pop(key)
            for part in ("logs", "orch"):
                src, dst = alias[part], models[full[0]][part]
                dst[:4] = [d + v for d, v in zip(dst[:4], src[:4])]
                dst[4] = dst[4] or src[4]
                dst[5] = min([t for t in (dst[5], src[5]) if t is not None], default=None)
    out = []
    for m in models.values():
        use = m["logs"] if m["logs"][4] else m["orch"]
        orch = m["orch"]
        if not any((use[0], use[1], use[2], use[3], orch[0], orch[1])):
            continue   # today, plans and total are all 0 (or without data): no row
        out.append({"Label": m["Label"], "Since": use[5],
                    "Today": (use[2], use[3]) if use[4] else None,
                    "Plans": (orch[0], orch[1]) if orch[4] else None,
                    "Total": (use[0], use[1]) if use[4] else None})
    out.sort(key=lambda m: m["Label"])
    return out


# Collect dashboard data, including recent blocked tasks and provider limit rows.
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
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    loop = _kv(conn, "loop-state") or {}
    since = _parse_time(loop.get("since")) if isinstance(loop, dict) else None
    if since:
        orch_status = "idle since " + since.strftime("%H:%M")
    else:
        orch_status = "idle"
    last_round = conn.execute("SELECT ts FROM events WHERE kind='round'" + (" AND project=?" if project else "")
                              + " ORDER BY id DESC LIMIT 1", pa).fetchone()
    return {
        "now": now, "project": project, "projects": scope, "colours": colours, "cfg": cfg, "sysinfo": sysinfo,
        "run": runstate.get_state(conn), "hives": hive_counts(conn, project), "agents": agents, "fin": fin, "blocked": blocked_tasks(agents_all, now),
        "provider_limits": provider_lines(conn, cfg, agents_all),
        "cu": window_usage(_kv(conn, "usage-claude"), now), "xu": window_usage(_kv(conn, "usage-codex"), now),
        "usage": usage_models(conn, agents_all, day), "counts": counts, "drafts": drafts,
        "status": db.project_status(conn, project),
        "qs": db.open_questions(conn, project, only_open=True),
        "bj": build_jobs_line(conn, cfg, scope, all_rows, sysinfo), "host": host,
        "selfupdate": db.last_self_update(conn),
        "orch": {"status": orch_status},
        "round_errors": round_errors(conn, project, now),
        "last_round": (_parse_time(last_round[0]).strftime("%H:%M:%S") if last_round and _parse_time(last_round[0]) else "-"),
    }


# Build weekly provider usage and slot rows and mark the next medium provider.
def provider_lines(conn, cfg, agents) -> list:
    """Build the provider weekly quota and slot rows, marking the scheduler's next medium provider."""
    providers = sch.get_providers(cfg or {})
    free = sch.provider_free(providers, agents)
    next_provider = sch.select_provider(providers, "medium", [], free, conn=conn)
    rows = []
    for provider in providers:
        source = provider["limit"]
        used_slots = sum(1 for agent in agents if agent.get("status") in sch.ACTIVE
                         and sch.row_provider(agent) == provider["name"])
        used, hours, reset_at, stale_hours = None, None, None, None
        if source in ("claude", "codex"):
            limits_path = os.environ.get("BZZ_PROVIDER_LIMITS_FILE")
            if limits_path and Path(limits_path).is_file():
                entry = json.loads(Path(limits_path).read_text(encoding="utf-8-sig")).get(provider["name"]) or {}
                if entry.get("usedPercent") is not None:
                    used, hours = float(entry["usedPercent"]), entry.get("resetHours")
                    if hours is not None:
                        reset_at = time.time() + float(hours) * 3600
            elif not os.environ.get("BZZ_RUNTIME_OVERRIDE"):
                window = (sch.claude_usage(conn) if source == "claude" else sch.codex_usage()).get("SevenDay")
                if window and window.get("Pct") is not None:
                    used = float(window["Pct"])
                    if window.get("At"):
                        reset_at = float(window["At"])
                        hours = max(0.0, (reset_at - time.time()) / 3600)
                elif source == "claude":   # the newest check failed: show the last verified reading while it is younger than RETAIN_HOURS
                    kept = usage.retained_weekly(conn)
                    if kept:
                        used, stale_hours = float(kept[0]), usage.retained_age_hours(conn)
                        if kept[1]:
                            reset_at = float(kept[1])
                            hours = max(0.0, (reset_at - time.time()) / 3600)
        ratio = None
        if used is not None and hours is not None:
            remaining_week = max(0.0, float(hours)) / 168.0
            remaining_quota = max(0.0, 100.0 - used) / 100.0
            ratio = remaining_quota / remaining_week if remaining_week else float("inf")
        rows.append({"name": provider["name"], "used_pct": used, "hours": hours, "reset_at": reset_at, "ratio": ratio,
                     "stale": stale_hours is not None, "stale_hours": stale_hours,
                     "has_limit": source in ("claude", "codex"),
                     "used_slots": used_slots, "slots": free.get(provider["name"], 0), "total_slots": provider["slots"],
                     "model": ((provider.get("classes") or {}).get("medium") or {}).get("model"),
                     "next": provider is next_provider})
    return rows


# The failed rounds of the last day (the Beekeeper has no window of its own, so its errors show here).
def round_errors(conn, project, now) -> list:
    """The newest three round errors of the last 24 hours as dicts {ts, project, text}, newest first; `project` filters to one project."""
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT ts, project, text FROM events WHERE kind='rounds' AND text LIKE '%ERROR%' AND ts>=?"
                        + (" AND project=?" if project else "") + " ORDER BY id DESC LIMIT 3", (since, project) if project else (since,)).fetchall()
    return [{"ts": r[0], "project": r[1], "text": r[2]} for r in rows]


# Count the Hives that live per Apiary (the Hives themselves are listed in the Hive table of the dashboard).
def hive_counts(conn, project):
    """List of (Apiary, number of its Hives that are not done) for the Apiaries with at least one, in the project filter."""
    rows = conn.execute("SELECT project, COUNT(*) FROM hives WHERE state!='done'" + (" AND project=?" if project else "") + " GROUP BY project ORDER BY project",
                        (project,) if project else ()).fetchall()
    return [(r[0], r[1]) for r in rows]


def self_update_line(su) -> list:
    """Line parts of the last self-update: result in its colour, end (else start) time, new commit, and for a rollback the commit it went back to."""
    result = su.get("result") or "?"
    when = _parse_time(su.get("ended")) or _parse_time(su.get("started"))
    text = "  " + (when.strftime("%d.%m. %H:%M") if when else "-") + "  " + (su.get("new_commit") or "-")[:7]
    if result == "rollback":
        text += " (back to " + (su.get("old_commit") or "-")[:7] + ")"
    col = {"ok": "green", "rollback": "yellow"}.get(result, "red")
    return [("self-update ", "gray"), (result, col), (text, "gray")]


def live_sysinfo(conn, project=None) -> dict:
    """Read the live system values: cores, free and total RAM in MB, host name and running compiler processes
    (names from buildProcessNames of the project.toml of the shown projects; none = no count)."""
    if os.environ.get("BZZ_DASH_SYSINFO"):   # fixed values for the demo picture and tests
        return json.loads(os.environ["BZZ_DASH_SYSINFO"])
    names, build_projects = [], []
    rows = conn.execute("SELECT name, workspace FROM projects" + (" WHERE name=?" if project else ""),
                        (project,) if project else ()).fetchall()
    for r in rows:
        try:
            if r["workspace"]:
                mine = list(config.load_project(r["workspace"]).get("buildProcessNames") or [])
                names += mine
                if mine:
                    build_projects.append(r["name"])
        except Exception:   # a broken project file must not stop the dashboard
            continue
    names = sorted(set(names))
    return {"cores": osplat.logical_cores(), "free_mb": osplat.free_ram_mb(), "total_mb": _total_ram_mb(),
            "host": osplat.host_name(), "compiler_count": osplat.count_processes(names) if names else None, "build_projects": build_projects}


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
