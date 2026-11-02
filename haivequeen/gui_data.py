"""Data for the graphical dashboard (haivequeen/gui.py): one plain-dict snapshot of everything the window shows, read from the database."""
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, hives, overview, runstate, runtime
from . import platform as osplat
from . import scheduler as sch

LOG_LINES = 80         # newest event rows shown in the log panel
CONTEXT_FALLBACK = 300000   # context limit in tokens when the provider's own limit is unknown (scheduler default contextLimitTokens)
ACTIVITY_DAYS = 7       # window of the token usage panel
LIVE_LINES = 300        # newest lines shown in the live view of an agent
LIVE_FILE = "milestones.txt"   # live text file of an agent, in its run folder (written by agent_runner.AgentRun.say)
LIVE_COLOURS = {"White": "text", "Green": "green", "Red": "red", "Cyan": "cyan", "Yellow": "yellow", "Gray": "dim"}   # colour word of a live line -> gui.COL name


# Strip the plan suffix so a plan file name reads as a Hive name.
def hive_name(plan_path) -> str:
    """Short Hive name of a plan path: file name without the trailing -PLAN and extension."""
    stem = Path(str(plan_path)).stem
    return stem[:-5] if stem.upper().endswith("-PLAN") else stem


# Format an elapsed time the way the table cells show it.
def fmt_elapsed(start, now) -> str:
    """MM:SS below one hour, H:MM:SS above; an en dash when the start is unknown."""
    when = db.parse_time(start)
    if when is None:
        return "–"
    secs = max(0, int((now - when).total_seconds()))
    hours, rest = divmod(secs, 3600)
    return "%d:%02d:%02d" % (hours, rest // 60, rest % 60) if hours else "%02d:%02d" % (rest // 60, rest % 60)


# Format a token count with a k or M suffix.
def fmt_tokens(n) -> str:
    """Compact token count: 964k, 1.1M, 36k."""
    n = int(n or 0)
    if n >= 100_000_000:
        return "%dM" % round(n / 1_000_000)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    return "%dk" % round(n / 1000) if n >= 1000 else str(n)


_cpu_last = {"sample": None}


# Read the machine's cumulative busy and total CPU time.
def _cpu_times():
    """(busy, total) CPU time counters, or None when this system does not offer them."""
    try:
        if sys.platform == "win32":
            import ctypes
            idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            total = kernel.value + user.value   # kernel time already includes idle time
            return total - idle.value, total
        fields = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(fields) - fields[3] - (fields[4] if len(fields) > 4 else 0), sum(fields)
    except (OSError, ValueError, AttributeError, IndexError):
        return None


# Compute the CPU load since the previous call.
def cpu_percent() -> int:
    """CPU load in percent since the previous call (0 on the first call or when unknown)."""
    now = _cpu_times()
    last, _cpu_last["sample"] = _cpu_last["sample"], now
    if now is None or last is None or now[1] <= last[1]:
        return 0
    return max(0, min(100, round(100 * (now[0] - last[0]) / (now[1] - last[1]))))


# Read the live machine values for the system panel.
def system_info(base) -> dict:
    """CPU, RAM and disk values: cores, cpu %, ram used/total in GB and %, disk used/total in TB and %.

    `base` is the sysinfo dict of overview.collect (cores, free_mb, total_mb), so the window shows the same RAM."""
    total_mb, free_mb = base["total_mb"], base["free_mb"]
    used_mb = max(0, total_mb - free_mb) if total_mb else 0
    try:
        disk = shutil.disk_usage(Path(config.data_dir()).anchor or "/")
    except OSError:
        disk = None
    gb = 1024 ** 3
    return {"cores": base["cores"], "cpu": cpu_percent(),
            "ram_used_gb": used_mb / 1024, "ram_total_gb": total_mb / 1024,
            "ram_pct": round(100 * used_mb / total_mb) if total_mb else 0,
            "disk_used_tb": (disk.used / gb / 1024) if disk else 0, "disk_total_tb": (disk.total / gb / 1024) if disk else 0,
            "disk_pct": round(100 * disk.used / disk.total) if disk and disk.total else 0}


# English text of a recorded start blocker (hives.blocker_label writes the terminal-era German words).
def blocker_text(reason) -> str:
    """English text of the reason why a Hive could not start its Queen."""
    reason = str(reason)
    if reason.startswith("Hive limit "):
        return "Hive limit %s reached" % reason.split()[2]
    if reason.startswith("free RAM "):
        return "Not enough free RAM: " + reason.split(" is below")[0][9:]
    if reason.startswith("no provider with a free slot"):
        return "No free provider with a Queen model"
    if reason.startswith("waiting for a free slot or limit of provider "):
        return "Provider busy or quota used up: " + reason.rsplit(" ", 1)[-1]
    if reason.startswith("its branch "):
        return "Plan branch is used elsewhere"
    if reason.startswith("worktree for branch "):
        return "Plan worktree could not be prepared"
    return {"STOP": "HaiveQueen stopped", "PAUSED": "HaiveQueen paused",
            "waiting for the integration place": "Waiting for integration", "handing off, Bee processes are still running": "Handoff: Bees still running",
            "retry after an interrupted Queen is not due yet": "Waiting after interrupted Queen",
            "no branch or worktree named in the plan or its state file": "Plan branch or worktree missing",
            "the Hive is already claimed": "Start already reserved"}.get(reason, reason)


# English state text of a Hive without a running Queen (hives.state_label writes German words).
def status_text(hive) -> str:
    """English words for the persisted Hive state, the actual start blocker and the pending questions."""
    state = hive.get("state")
    questions = hive.get("pending_questions") or []
    suffix = " (#" + ", #".join(map(str, questions)) + ")" if questions else ""
    if state in ("answer", "owner_check") and questions:
        return ("Waiting for owner check" if state == "owner_check" else "Waiting for answer") + suffix
    if hive.get("blocker") and state in ("running", "waiting", "starting", "requeen", "codex_wait", "provider_wait", "integration_wait"):
        return blocker_text(hive["blocker"]) + suffix
    if state == "running":
        return "No active Queen" + ("; open questions #" + ", #".join(map(str, questions)) if questions else "")
    if state == "done":
        return "Done"
    if state == "resting":
        return "Resting until " + str(hive.get("reset_at") or "?").replace("T", " ")[:16]
    return {"queenless": "Queenless", "integration_wait": "Waiting for integration", "answer": "Waiting for answer", "owner_check": "Waiting for owner check",
            "codex_wait": "Waiting for Codex", "provider_wait": "Waiting for provider", "handoff": "Handoff", "waiting": "Waiting to start",
            "starting": "Waiting to start", "requeen": "Waiting to start"}.get(state, "")


# Read which packages have a Bee assigned and which a Bee has finished.
def bee_tables(conn) -> tuple:
    """(assigned, completed): {(project, plan, package): (class, provider)} of the Bee assignments and the set of (project, plan, package) that are done.

    A package is done when a Bee finished it or its Queen reported it done (table package_progress), unless the owner rejected it. Both are empty when the tables do not exist yet (older database)."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('bee_assignments','bee_completed','package_progress')")}
    assigned = {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute("SELECT project,plan_path,package,class,provider FROM bee_assignments")} if "bee_assignments" in have else {}
    completed = {(r[0], r[1], r[2]) for r in conn.execute("SELECT project,plan_path,package FROM bee_completed")} if "bee_completed" in have else set()
    if "package_progress" in have:
        completed |= {(r[0], r[1], r[2]) for r in conn.execute("SELECT project,plan_path,package FROM package_progress WHERE state='done'")}
        completed -= {(r[0], r[1], r[2]) for r in conn.execute("SELECT project,plan_path,package FROM package_progress WHERE state='rejected'")}   # the owner did not accept it: not done
    return assigned, completed


# Count the packages a Queen reported as implemented but not yet verified.
def implemented_counts(conn) -> dict:
    """{(project, plan): number of packages in state implemented}; empty when the table does not exist yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='package_progress'").fetchone():
        return {}
    return {(r[0], r[1]): r[2] for r in conn.execute("SELECT project,plan_path,COUNT(*) FROM package_progress WHERE state='implemented' GROUP BY project,plan_path")}


# Find the packages the owner did not accept.
def rejected_map(conn) -> dict:
    """{(project, plan): set of package ids in state rejected}; empty when the table does not exist yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='package_progress'").fetchone():
        return {}
    found = {}
    for project, plan, package in conn.execute("SELECT project,plan_path,package FROM package_progress WHERE state='rejected'"):
        found.setdefault((project, plan), set()).add(package)
    return found


# Find the packages that wait for an answer, a build or the owner.
def waiting_map(conn) -> dict:
    """{(project, plan): [(package, reason)]} of the packages in state waiting, in package order; empty when the table or its reason column does not exist yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='package_progress'").fetchone():
        return {}
    if "reason" not in [r[1] for r in conn.execute("PRAGMA table_info(package_progress)")]:
        return {}
    found = {}
    for project, plan, package, reason in conn.execute("SELECT project,plan_path,package,reason FROM package_progress WHERE state='waiting' ORDER BY package"):
        found.setdefault((project, plan), []).append((package, reason))
    return found


# Input and output tokens of one agent row.
def agent_tokens(agent) -> tuple:
    """(tokens in, tokens out) of an agent row; zero when not reported."""
    return int(agent.get("tokens_in") or 0), int(agent.get("tokens_out") or 0)


# Describe one Hive for the active-hives table.
def hive_row(hive, agents, packages, now, assigned=None, completed=None, ctx_limit=None, agents_all=None, implemented=None, paused=None, rejected=None, waiting=None) -> dict:
    """Row of one Hive: name, apiary, queen with context and tokens, Bees busy/assigned, progress (finished packages of all packages), status and runtime.

    `assigned` and `completed` come from bee_tables, `ctx_limit(agent)` gives an agent's context limit in tokens, `agents_all` (all rows, also archived)
    gives the Hive's token total over its whole life, `implemented` maps (project, plan) to the packages reported implemented but not verified, `paused` is the set of (project, plan) whose plan status is PAUSED, `rejected` maps (project, plan) to the packages the owner did not accept (never counted as done)."""
    key = (hive["project"], hive["plan_path"])
    mine = [a for a in agents if (a.get("project"), a.get("plan_path")) == key]
    queen = hive.get("queen")
    live = bool(queen and queen.get("status") in runtime.ACTIVE_STATUSES)
    total_pkg = packages.get(key, 0)
    done = {p for (pr, pl, p) in (completed or ()) if (pr, pl) == key}
    done |= {a.get("package") for a in mine if a.get("status") == "done" and a.get("phase") in ("work", "bee") and a.get("package")}
    rejected_pkgs = (rejected or {}).get(key, set())
    waiting_pkgs = (waiting or {}).get(key, [])
    done -= rejected_pkgs
    running_pkgs = {a.get("package") for a in mine if a.get("phase") == "bee" and a.get("status") in runtime.ACTIVE_STATUSES and a.get("package")}
    open_pkgs = {p for (pr, pl, p) in (assigned or {}) if (pr, pl) == key and p not in done} | running_pkgs
    progress = min(100, round(100 * len(done) / total_pkg)) if total_pkg else 0
    is_paused = key in (paused or ())
    if is_paused:
        status, kind = ("Running, plan paused" if live else "Plan paused"), "wait"
    elif live:
        status, kind = "Running", "ok"
    else:
        status = status_text(hive) or "Idle"
        kind = "idle" if status == "Idle" else "wait"
    qin, qout = agent_tokens(queen) if queen else (0, 0)
    hin = sum(agent_tokens(a)[0] for a in (agents_all if agents_all is not None else agents) if (a.get("project"), a.get("plan_path")) == key)
    hout = sum(agent_tokens(a)[1] for a in (agents_all if agents_all is not None else agents) if (a.get("project"), a.get("plan_path")) == key)
    return {"name": hive_name(hive["plan_path"]), "apiary": hive["project"], "plan_path": hive["plan_path"],
            "queen": str((queen or {}).get("person") or (queen or {}).get("name") or "–"), "queen_agent": (queen or {}).get("name"),
            "queen_model": overview.model_short(queen.get("provider"), queen.get("model"), queen.get("effort")) if live else "",
            "queen_ctx": int((queen or {}).get("ctxTokens") or 0) if live else 0, "queen_ctx_limit": int(ctx_limit(queen)) if live and ctx_limit else 0,
            "queen_tin": qin, "queen_tout": qout, "hive_tin": hin, "hive_tout": hout,
            "steps": {b["name"]: b["step"] for b in hive.get("bees", []) if b.get("step")},
            "bees_running": len(running_pkgs), "bees_total": len(open_pkgs) if live else len(running_pkgs), "live": live, "paused": is_paused, "done": len(done), "implemented": (implemented or {}).get(key, 0), "rejected": len(rejected_pkgs), "waiting": len(waiting_pkgs), "waiting_reasons": ", ".join("%s %s" % (p, why) for p, why in waiting_pkgs), "total": total_pkg, "progress": progress,
            "status": status, "kind": kind, "runtime": fmt_elapsed(queen.get("started_at"), now) if live else ""}


# Describe one running Bee for the busy-bees table.
def bee_row(agent, now, steps=None, ctx_limit=None) -> dict:
    """Row of one running Bee: kind "bee", person name, model label, hive, apiary, plan path, task (with its reported step like 2/5), context, tokens, status and runtime."""
    name = str(agent.get("person") or agent.get("name"))
    step = (steps or {}).get(name, "")
    tin, tout = agent_tokens(agent)
    return {"kind": "bee", "name": name, "agent": agent.get("name"), "project": agent.get("project"), "plan_path": agent.get("plan_path") or "",
            "model": overview.model_short(agent.get("provider"), agent.get("model"), agent.get("effort")),
            "hive": hive_name(agent.get("plan_path") or ""), "apiary": agent.get("project"),
            "task": (str(agent.get("task") or agent.get("package") or agent.get("last_line") or "")[:40] + (" " + step if step else "")).strip(),
            "ctx": int(agent.get("ctxTokens") or 0), "ctx_limit": int(ctx_limit(agent)) if ctx_limit else 0, "tin": tin, "tout": tout,
            "status": "running" if agent.get("status") in runtime.ACTIVE_STATUSES else str(agent.get("status")), "runtime": fmt_elapsed(agent.get("started_at"), now)}


# Describe the running Queen of a Hive for the Bees table, from the Hive's row.
def queen_row(hive) -> dict:
    """Row of the running Queen of the Hive row `hive` (kind "queen"): name, model with effort, context, tokens and runtime; the Hive table does not repeat them."""
    return {"kind": "queen", "name": hive["queen"], "agent": hive["queen_agent"], "project": hive["apiary"], "plan_path": hive["plan_path"],
            "model": hive["queen_model"] or "–", "hive": hive["name"], "apiary": hive["apiary"], "task": "Queen",
            "ctx": hive["queen_ctx"], "ctx_limit": hive["queen_ctx_limit"], "tin": hive["queen_tin"], "tout": hive["queen_tout"],
            "status": "running", "runtime": hive["runtime"]}


# Collect the Bees of the active Hives: running Bee processes and packages that have a Bee assigned but no process yet.
def bee_list(agents, assigned, completed, now, ctx_limit=None, steps=None, hive_keys=None, hive_rows=None) -> list:
    """Rows for the Bees table. A package with a Bee assignment but no running process shows as 'waiting'; finished packages are left out.

    `hive_keys` limits the waiting rows to the (project, plan) pairs whose Queen is running: only a running Queen starts Bees, so the assignments of any other Hive are not waiting Bees.
    With `hive_rows` (the Hive rows) every Hive with a running Queen gets the Queen's row first and its Bees directly below it; Bees of other Hives follow."""
    rows, seen = [], set()
    for agent in agents:
        if agent.get("phase") == "bee" and agent.get("status") in runtime.ACTIVE_STATUSES:
            rows.append(bee_row(agent, now, steps, ctx_limit))
            seen.add((agent.get("project"), agent.get("plan_path"), agent.get("package")))
    for (project, plan, package) in sorted(assigned or {}):
        if (project, plan, package) in seen or (project, plan, package) in (completed or ()) or (hive_keys is not None and (project, plan) not in hive_keys):
            continue
        # A Bee that has not started has no name yet (names are given at the start) and its stored provider is only the choice of an earlier try,
        # so neither the package id nor that provider is shown as the Bee's name or model.
        rows.append({"kind": "bee", "name": "–", "agent": None, "project": project, "plan_path": plan, "model": "–", "hive": hive_name(plan),
                     "apiary": project, "task": package, "ctx": 0, "ctx_limit": 0, "tin": 0, "tout": 0, "status": "waiting", "runtime": "–"})
    if not hive_rows:
        return rows
    ordered, placed = [], set()
    for hive in hive_rows:
        if hive["live"]:
            mine = [r for r in rows if (r["apiary"], r["plan_path"]) == (hive["apiary"], hive["plan_path"])]
            ordered += [queen_row(hive)] + mine
            placed.update(id(r) for r in mine)
    return ordered + [r for r in rows if id(r) not in placed]


# Format one event row as a log line with a source tag.
def log_line(event, who=None) -> tuple:
    """(time, tag, text) of an event row: tag is QUEEN x, BEE x, APIARY x or SYSTEM depending on who wrote it.

    `who` maps an agent name to (phase, person) so the tag shows the Queen or Bee by the person's name."""
    when = db.parse_time(event["ts"])
    stamp = when.strftime("%H:%M") if when else "--:--"
    agent, project = event["agent"], event["project"]
    if agent:
        phase, person = (who or {}).get(agent, (str(agent).split("__")[-1], str(agent).split("__")[0]))
        tag = ("QUEEN" if str(phase).startswith("queen") else "BEE") + " " + str(person)
    elif project and project != runstate.GLOBAL_PROJECT:
        tag = "APIARY " + str(project)
    else:
        tag = "SYSTEM"
    return stamp, tag, str(event["text"] or event["kind"]).replace("\n", " ")


# Sum the tokens of every Apiary and Hive over the activity window.
def activity_rows(agents_all, now) -> list:
    """Token use of the last ACTIVITY_DAYS days: [{kind, name, apiary, pct, tin, tout, last}] with the Apiaries first, then the Hives, each busiest first.

    `pct` is the share of that row among the rows of its kind."""
    since = now - timedelta(days=ACTIVITY_DAYS)
    sums = {"apiary": {}, "hive": {}}
    for a in agents_all:
        started = db.parse_time(a.get("started_at"))
        if started is None or started < since:
            continue
        tin, tout = agent_tokens(a)
        keys = [("apiary", (a.get("project"), a.get("project")))]
        if a.get("plan_path"):
            keys.append(("hive", (a.get("project"), hive_name(a["plan_path"]))))
        for kind, key in keys:
            entry = sums[kind].setdefault(key, [0, 0, started])
            entry[0] += tin
            entry[1] += tout
            entry[2] = max(entry[2], started)
    rows = []
    for kind in ("apiary", "hive"):
        total = sum(v[0] + v[1] for v in sums[kind].values())
        for (apiary, name), (tin, tout, last) in sorted(sums[kind].items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
            rows.append({"kind": kind, "name": name, "apiary": apiary, "pct": round(100 * (tin + tout) / total) if total else 0,
                         "tin": tin, "tout": tout, "last": last.strftime("%d.%m. %H:%M")})
    return rows


# Read the integration queue and open questions for the apiary-queue panel.
def queue_panel(conn, project) -> dict:
    """Open question count, queued integration rows and their short labels (PROJECT#id)."""
    where, args = ("WHERE project=?", (project,)) if project else ("", ())
    glue = " AND" if where else " WHERE"
    open_q = conn.execute("SELECT COUNT(*) FROM questions " + where + glue + " status='open'", args).fetchone()[0]
    queued = db.rows(conn, "SELECT id,project FROM queue " + where + glue + " status IN ('ready','in_run') ORDER BY id", args)
    return {"open": open_q, "queued": len(queued), "labels": ["%s#%d" % (q["project"], q["id"]) for q in queued][:4]}


# Describe the provider limits as bars.
def limit_bars(d) -> list:
    """[{label, pct, note, level}] for the Claude/Codex 5-hour windows and, per provider, the weekly quota used and the slots in use.

    `pct` is the used share in percent (None = unknown), `level` is ok, warn or bad by how much is used (dim when unknown, slots for slot bars)
    and `note` is the text beside the bar."""
    def level(pct):
        """Colour level of a used share."""
        return {"green": "ok", "yellow": "warn", "red": "bad"}[overview.level_colour(pct)] if pct is not None else "dim"
    out = []
    for label, entry in (("Claude 5h", d["cu"]["FiveHour"]), ("Codex 5h", d["xu"]["FiveHour"])):
        if label == "Codex 5h" and not entry:
            continue
        if entry is None:
            out.append({"label": label, "pct": None, "note": "no data from the provider", "level": "dim"})
        elif entry["pct"] is None:
            out.append({"label": label, "pct": None, "note": entry["reset"], "level": "dim"})
        else:
            out.append({"label": label, "pct": entry["pct"], "note": ("resets " + entry["reset"]).strip(), "level": level(entry["pct"])})
    for p in d["provider_limits"]:
        marker = "  ◀ next" if p["next"] else ""
        if p["has_limit"]:
            reset = sch.format_reset_time(p["reset_at"]) if p["reset_at"] else ""
            quota = "" if p["ratio"] is None else "quota %.2fx" % p["ratio"]
            note = " | ".join(part for part in ("resets " + reset if reset else "reset unknown", quota) if part)
            out.append({"label": p["name"] + " 7d", "pct": None if p["used_pct"] is None else round(p["used_pct"]), "note": note, "level": level(p["used_pct"])})
        total = int(p["total_slots"] or 0)
        out.append({"label": p["name"] + " slots", "pct": round(100 * p["used_slots"] / total) if total else None,
                    "note": "%d/%d | %s%s" % (p["used_slots"], total, p["model"] or "no medium model", marker), "level": "slots"})
    return out


# Describe the token usage per model per model.
def usage_lines(d) -> list:
    """[(text, colour)] with one line per model: today, all plans and total input/output."""
    def cell(v):
        """Input/output of one usage cell, or 'no data'."""
        return "no data" if v is None else "%s/%s" % (overview.fmt_tok0(v[0]), overview.fmt_tok0(v[1]))
    return [("%s  today %s  plans %s  total %s" % (m["Label"].replace("codex-auto-review", "Auto-Review"), cell(m["Today"]), cell(m["Plans"]), cell(m["Total"])), "text")
            for m in d["usage"]] or [("no data", "dim")]


# Collect the sections of the status panel: everything the overview data holds that has no place in the other panels.
def info_sections(d) -> list:
    """[(title, [(text, colour)])] for run state, usage, system, plans, questions, round errors and the last self-update (the limits are bars, see limit_bars)."""
    run = d["run"]
    state = {"RUNNING": "ok", "PAUSED": "warn"}[run["state"]]
    sub = {"RUNNING": "", "PAUSED": "no new agents"}[run["state"]]
    out = [("STATE", [(run["state"] + ("  " + sub if sub else "") + "  Apiary: %s" % (d["project"] or "all"), state),
                      ("Beekeeper " + d["orch"]["status"], "text")])]
    plans = "  ".join("%s %d" % (name, d["counts"].get(name, 0)) for name, _ in overview.PLAN_BADGES)
    if d["counts"].get("DRAFT"):
        plans += "  (draft: %s)" % ", ".join(d["drafts"])
    out.append(("USAGE", usage_lines(d)))
    system = [(d["bj"], "text")] if d["bj"] else []
    out.append(("SYSTEM", system + [("RAM %d%% %.1f/%.0f GB" % (round(100 * (1 - d["sysinfo"]["free_mb"] / d["sysinfo"]["total_mb"])) if d["sysinfo"]["total_mb"] else 0,
                                                                  (d["sysinfo"]["total_mb"] - d["sysinfo"]["free_mb"]) / 1024.0, d["sysinfo"]["total_mb"] / 1024.0), "text")]))
    out.append(("PLANS", [(plans, "text")] + [("%s %d / %d integrated (%d%%)" % (st["project"], st["integrated"], st["total"], st["percent"]), "dim")
                                              for st in d["status"]]))
    if d["qs"]:
        qs = [("open: %d  %s" % (len(d["qs"]), ", ".join("%s#%s" % (q["project"], q["id"]) for q in d["qs"])), "warn")]
    else:
        qs = [("none open", "dim")]
    if d["triage"]:
        qs.append(("in triage: %d" % d["triage"], "text"))
    out.append(("QUESTIONS", qs))
    if d["round_errors"]:
        out.append(("ROUND ERRORS", [((db.parse_time(e["ts"]).strftime("%H:%M") if db.parse_time(e["ts"]) else "--:--") + " " + e["project"] + " " + e["text"], "bad")
                                     for e in d["round_errors"]]))
    if d["selfupdate"]:
        out.append(("SELF-UPDATE", [("".join(text for text, _ in overview.self_update_line(d["selfupdate"])), "text")]))
    return out


# Combine the loop process and the run state into the one state the window shows.
def orchestrator_state(run_state, loop_pid, stop_pending) -> dict:
    """{label, level}: STOPPED (no loop process), STOPPING (a stop waits for the running agents), else the run state RUNNING or PAUSED."""
    if not loop_pid:
        return {"label": "STOPPED", "level": "bad"}
    if stop_pending:
        return {"label": "STOPPING", "level": "warn"}
    return {"label": run_state, "level": {"RUNNING": "ok", "PAUSED": "warn"}.get(run_state, "warn")}


# Read the installed package version for the header.
def _version() -> str:
    """Version of the haivequeen package, or an en dash when it is not installed."""
    try:
        from importlib.metadata import version
        return version("haivequeen")
    except Exception:
        return "–"


# Collect everything the graphical dashboard shows in one call.
def snapshot(conn, project=None, now=None, cfg=None, sysinfo=None) -> dict:
    """Plain dict with the header tiles, the Hive and Bee tables, the log, activity, system, queue and status panels.

    The numbers come from overview.collect(). `project` filters to one Apiary;
    `now`, `cfg` and `sysinfo` (the GUI form of the system values) replace the clock, the machine config and the live system reads (tests)."""
    now = now or datetime.now()
    cfg = cfg if cfg is not None else config.load_config()
    base = None
    if sysinfo is not None:
        total = int(sysinfo["ram_total_gb"] * 1024)
        base = {"cores": sysinfo["cores"], "total_mb": total, "free_mb": total - int(sysinfo["ram_used_gb"] * 1024), "host": "gui", "compiler_count": None}
    d = overview.collect(conn, project, now, cfg, base)
    agents_all = runtime.list_agents(conn, project, "all")
    agents = [a for a in agents_all if not a.get("archived")]
    packages = {(r["project"], r["plan_path"]): r["n"] for r in
                db.rows(conn, "SELECT project, plan_path, COUNT(*) AS n FROM packages GROUP BY project, plan_path")}
    try:
        envs = {e.name: e for e in sch.load_projects(conn, cfg)}
    except Exception:   # a broken project file must not stop the window
        envs = {}

    def ctx_limit(agent):
        """Context limit in tokens of the agent's provider and class (the setting's default when unknown)."""
        env = envs.get(agent.get("project"))
        try:
            return sch.context_limit(env.settings, env.providers, sch.row_provider(agent), agent.get("class")) if env else CONTEXT_FALLBACK
        except Exception:
            return CONTEXT_FALLBACK
    assigned, completed = bee_tables(conn)
    impl = implemented_counts(conn)
    rejected = rejected_map(conn)
    waiting = waiting_map(conn)
    paused = {(r[0], r[1]) for r in conn.execute("SELECT project, path FROM plans WHERE status='PAUSED'")}
    hive_rows = [hive_row(h, agents, packages, now, assigned, completed, ctx_limit, agents_all, impl, paused, rejected, waiting) for h in hives.hive_data(conn, project, agents)]
    live = [a for a in agents if a.get("status") in runtime.ACTIVE_STATUSES]
    queens = [a for a in live if a.get("phase") == "queen"]
    steps = {k: v for h in hive_rows for k, v in h["steps"].items()}
    bees = bee_list(agents, assigned, completed, now, ctx_limit, steps, {(h["apiary"], h["plan_path"]) for h in hive_rows if h["live"]}, hive_rows)
    workers = [b for b in bees if b["kind"] == "bee"]
    apiaries = sorted({h["apiary"] for h in hive_rows})
    slots = sum(int(p["total_slots"] or 0) for p in d["provider_limits"])
    today = [m["Today"] for m in d["usage"] if m.get("Today")]
    who = {a["name"]: (a.get("phase") or "", a.get("person") or a["name"]) for a in agents_all}
    events = db.rows(conn, "SELECT id,ts,project,kind,agent,text FROM events" + (" WHERE project IN (?, ?)" if project else "") +
                     " ORDER BY id DESC LIMIT %d" % LOG_LINES, (project, runstate.GLOBAL_PROJECT) if project else ())
    try:
        from . import loop
        orchestrator = orchestrator_state(d["run"]["state"], loop.loop_running(), loop.stop_flag().exists())
    except Exception:   # a broken lock file must not stop the window: show the run state alone
        orchestrator = orchestrator_state(d["run"]["state"], 1, False)
    return {"now": now, "project": project, "run_state": d["run"]["state"], "orchestrator": orchestrator, "version": _version(),
            "tiles": {"apiaries": len(apiaries), "hives": len(hive_rows), "queens": len(queens), "bees": sum(1 for b in workers if b["status"] == "running"),
                      "bee_capacity": max(len(workers), slots), "tokens_in": sum(t[0] for t in today), "tokens_out": sum(t[1] for t in today)},
            "hives": hive_rows, "bees": bees, "apiaries": apiaries,
            "projects": [r["name"] for r in conn.execute("SELECT name FROM projects ORDER BY priority DESC, name")],
            "log": [log_line(e, who) for e in reversed(events)], "log_ids": [e["id"] for e in reversed(events)], "activity": activity_rows(agents_all, now),
            "system": sysinfo if sysinfo is not None else system_info(d["sysinfo"]), "queue": queue_panel(conn, project),
            "info": info_sections(d), "limits": limit_bars(d)}


# Find the live text file of an agent.
def live_path(project, agent) -> Path:
    """Path of the live text file of an agent: <data>/runs/<project>/<agent>/milestones.txt (the run folder the runner and the loop use)."""
    return osplat.runs_dir() / str(project) / str(agent) / LIVE_FILE


# Read the end of a text file.
def tail_file(path, lines=LIVE_LINES, max_bytes=256 * 1024) -> list:
    """The last `lines` lines of a text file as strings (utf-8, undecodable bytes replaced); [] when the file is missing or empty.

    Only the last `max_bytes` bytes are read, so a very large file costs no more than a small one; a line cut by that limit is dropped."""
    try:
        with open(str(path), "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    rows = text.splitlines()
    if start > 0 and rows:
        rows = rows[1:]   # the first line may be cut in the middle
    return rows[-lines:] if lines > 0 else []


# Turn live file lines into (colour, text) pairs.
def live_lines(path, lines=LIVE_LINES) -> list:
    """[(gui colour name, text)] of the last lines of an agent's live file: a line "<Colour><TAB><text>" gets that colour, any other line the plain text colour."""
    out = []
    for row in tail_file(path, lines):
        word, tab, rest = row.partition(chr(9))
        out.append((LIVE_COLOURS.get(word, "text"), rest) if tab and word in LIVE_COLOURS else ("text", row))
    return out
