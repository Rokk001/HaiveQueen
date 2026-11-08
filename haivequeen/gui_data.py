"""Data for the graphical dashboard (haivequeen/gui.py): one plain-dict snapshot of everything the window shows, read from the database."""
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, gui_overlays, hives, overview, runstate, runtime, reserve as quota_reserve
from . import platform as osplat
from . import i18n, localtime
from . import scheduler as sch
from .i18n import N_, tr

LOG_LINES = 80         # newest event rows (without routine noise) shown in the log panel
LOG_FETCH = 500        # newest event rows read to find those LOG_LINES
CONTEXT_FALLBACK = 300000   # context limit in tokens when the provider's own limit is unknown (scheduler default contextLimitTokens)
ACTIVITY_DAYS = 7       # window of the token usage panel
LIVE_LINES = 300        # newest lines shown in the live view of an agent
LIVE_FILE = "milestones.txt"   # live text file of an agent, in its run folder (written by agent_runner.AgentRun.say)
LIVE_COLOURS = {"White": "text", "Green": "green", "Red": "red", "Cyan": "cyan", "Yellow": "yellow", "Gray": "dim"}   # colour word of a live line -> gui.COL name


# Strip the plan suffix so a plan file name reads as a Hive name.
def hive_name(plan_path) -> str:
    """Short Plan name of a plan path: file name without the trailing -PLAN and extension."""
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
        return i18n.decimal("%.1fM" % (n / 1_000_000))
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


# Show a stored time in the system format.
def fmt_stamp(text) -> str:
    """A stored ISO time as date and time in the system format; other text unchanged, nothing for none."""
    when = db.parse_time(text)
    return localtime.date_time(when) if when else str(text or "")


# Text of a recorded start blocker.
def blocker_text(reason) -> str:
    """Text of the reason why a Plan could not start its Queen, in the language of the GUI."""
    reason = str(reason)
    if reason.startswith("Plan limit "):
        return tr("Plan limit %s reached", reason.split()[2])
    if reason.startswith("free RAM "):
        return tr("Not enough free RAM: %s", i18n.decimal(reason.split(" is below")[0][9:]))
    if reason.startswith("no provider with a free slot"):
        return tr("No free provider with a Queen model")
    if reason.startswith("waiting for provider "):
        name, colon, detail = reason[len("waiting for provider "):].partition(": ")
        return tr("Waiting for provider %s", name) + (": " + i18n.log_text(detail) if colon else "")
    if reason.startswith("its branch "):
        return tr("Plan branch is used elsewhere")
    if reason.startswith("worktree for branch "):
        return tr("Plan worktree could not be prepared")
    return tr({"STOP": N_("HaiveQueen stopped"), "PAUSED": N_("HaiveQueen paused"),
               "waiting for the integration place": N_("Waiting for integration"), "handing off, Agent processes are still running": N_("Handoff: Agents still running"),
               "retry after an interrupted Queen is not due yet": N_("Waiting after interrupted Queen"),
               "no branch or worktree named in the plan or its state file": N_("Plan branch or worktree missing"),
               "the Plan is already claimed": N_("Start already reserved")}.get(reason, i18n.log_text(reason)))


# State text of a Hive without a running Queen.
def status_text(hive) -> str:
    """Words in the language of the GUI for the persisted Plan state, the actual start blocker and the pending questions."""
    state = hive.get("state")
    questions = hive.get("pending_questions") or []
    suffix = " (#" + ", #".join(map(str, questions)) + ")" if questions else ""
    if state in ("answer", "owner_check") and questions:
        return (tr("Waiting for owner check") if state == "owner_check" else tr("Waiting for answer")) + suffix
    if hive.get("blocker") and state in ("running", "waiting", "starting", "requeen", "codex_wait", "provider_wait", "integration_wait"):
        return blocker_text(hive["blocker"]) + suffix
    if state == "running":
        return tr("No active Queen") + (tr("; open questions %s", "#" + ", #".join(map(str, questions))) if questions else "")
    if state == "done":
        return tr("Done")
    if state == "resting":
        return tr("Resting until %s", fmt_stamp(hive.get("reset_at")) or "?")
    return tr({"queenless": N_("Queenless"), "integration_wait": N_("Waiting for integration"), "answer": N_("Waiting for answer"), "owner_check": N_("Waiting for owner check"),
               "codex_wait": N_("Waiting for Codex"), "provider_wait": N_("Waiting for provider"), "handoff": N_("Handoff"), "waiting": N_("Waiting to start"),
               "starting": N_("Waiting to start"), "requeen": N_("Waiting to start")}.get(state, ""))


# Read which packages have a Bee assigned and which a Bee has finished.
def bee_tables(conn) -> tuple:
    """(assigned, completed): {(project, plan, package): (class, provider)} of the Agent assignments and the set of (project, plan, package) that are done.

    A package is done only when its current package_progress state is done. Older databases without that table have no completed packages."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('bee_assignments','bee_completed','package_progress')")}
    assigned = {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute("SELECT project,plan_path,package,class,provider FROM bee_assignments")} if "bee_assignments" in have else {}
    completed = set()
    if "package_progress" in have:
        completed = {(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT project,plan_path,package FROM package_progress WHERE state='done'")}
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


# Find the subagent entry of a Queen that belongs to an embedded Bee.
def match_sub(queen, name):
    """The entry {desc, ctx, tin, tout} of the Queen's subCtx whose Task description names the Agent `name`, or None."""
    return next((v for v in ((queen or {}).get("subCtx") or {}).values()
                 if re.search(r"\b%s\b" % re.escape(str(name)), str(v.get("desc") or ""), re.I)), None)


# Describe one Hive for the active-hives table.
def hive_row(hive, agents, packages, now, assigned=None, completed=None, ctx_limit=None, agents_all=None, implemented=None, paused=None, rejected=None, waiting=None, embedded=None, plan_rejected=None) -> dict:
    """Row of one Plan: name, project, queen with context and tokens, Agents busy/assigned, progress (finished packages of all packages), status and runtime.

    `assigned` and `completed` come from bee_tables, `ctx_limit(agent)` gives an agent's context limit in tokens, `agents_all` (all rows, also archived)
    gives the Plan's token total over its whole life, `implemented` maps (project, plan) to the packages reported implemented but not verified, `paused` is the set of (project, plan) whose plan status is PAUSED, `rejected` maps (project, plan) to the packages the owner did not accept (never counted as done)."""
    key = (hive["project"], hive["plan_path"])
    mine = [a for a in agents if (a.get("project"), a.get("plan_path")) == key]
    queen = hive.get("queen")
    live = bool(queen and queen.get("status") in runtime.ACTIVE_STATUSES)
    total_pkg = packages.get(key, 0)
    done = {p for (pr, pl, p) in (completed or ()) if (pr, pl) == key}
    rejected_pkgs = (rejected or {}).get(key, set())
    waiting_pkgs = (waiting or {}).get(key, [])
    done -= rejected_pkgs
    running_pkgs = {a.get("package") for a in mine if a.get("phase") == "bee" and a.get("status") in runtime.ACTIVE_STATUSES and a.get("package")}
    running_pkgs |= {b["package"] for b in embedded or () if (b["project"], b["plan_path"]) == key and b.get("package")}   # a Bee that runs as a subagent of the Queen is busy too
    open_pkgs = {p for (pr, pl, p) in (assigned or {}) if (pr, pl) == key and p not in done} | running_pkgs
    progress = min(100, round(100 * len(done) / total_pkg)) if total_pkg else 0
    is_paused = key in (paused or ())
    is_rejected = key in (plan_rejected or ())
    if is_rejected:
        status, kind = tr("Plan cancelled"), "wait"
    elif is_paused:
        status, kind = (tr("Running, plan paused") if live else tr("Plan paused")), "wait"
    elif live:
        status, kind = tr("Running"), "ok"
    else:
        status = status_text(hive)
        kind = "wait" if status else "idle"
        status = status or tr("Idle")
    qin, qout = agent_tokens(queen) if queen else (0, 0)
    for bee in embedded or ():   # the tokens of a Bee that runs as a subagent are shown at the Bee, not again at its Queen
        sub = match_sub(queen, bee["name"]) if queen and (bee["project"], bee["plan_path"]) == key else None
        if sub:
            qin, qout = max(0, qin - int(sub.get("tin") or 0)), max(0, qout - int(sub.get("tout") or 0))
    hin = sum(agent_tokens(a)[0] for a in (agents_all if agents_all is not None else agents) if (a.get("project"), a.get("plan_path")) == key)
    hout = sum(agent_tokens(a)[1] for a in (agents_all if agents_all is not None else agents) if (a.get("project"), a.get("plan_path")) == key)
    return {"name": hive_name(hive["plan_path"]), "apiary": hive["project"], "plan_path": hive["plan_path"],
            "queen": str((queen or {}).get("person") or (queen or {}).get("name") or "–") if live else "–", "queen_agent": (queen or {}).get("name"),   # a Queen that has ended no longer holds her name
            "queen_model": overview.model_short(queen.get("provider"), queen.get("model"), queen.get("effort")) if live else "",
            "queen_ctx": int((queen or {}).get("ctxTokens") or 0) if live else 0, "queen_ctx_limit": int(ctx_limit(queen)) if live and ctx_limit else 0,
            "queen_tin": qin, "queen_tout": qout, "hive_tin": hin, "hive_tout": hout,
            "steps": {b["name"]: b["step"] for b in hive.get("bees", []) if b.get("step")},
            "package_steps": {p: r["step"] for p, r in (hive.get("package_steps") or {}).items()},
            "bees_running": len(running_pkgs), "bees_total": len(open_pkgs) if live else len(running_pkgs), "live": live, "paused": is_paused, "plan_rejected": is_rejected, "done": len(done), "implemented": (implemented or {}).get(key, 0), "rejected": len(rejected_pkgs), "waiting": len(waiting_pkgs), "waiting_reasons": ", ".join("%s %s" % (p, why) for p, why in waiting_pkgs), "total": total_pkg, "progress": progress,
            "status": status, "kind": kind, "runtime": fmt_elapsed(queen.get("started_at"), now) if live else ""}


# Describe one running Bee for the busy-bees table.
def step_fraction(step) -> float:
    """The finished share of a reported step like "2/5" (0.4); 0.0 for anything else."""
    done, _, total = str(step or "").partition("/")
    try:
        return max(0.0, min(1.0, int(done) / int(total)))
    except (ValueError, ZeroDivisionError):
        return 0.0


# Read how many steps the newest Bee of each package got.
def step_counts(conn) -> dict:
    """{(project, plan, package): "0/m"} for the packages whose Agent was started with m numbered steps; empty when the table does not exist yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bee_step_counts'").fetchone():
        return {}
    return {(r[0], r[1], r[2]): "0/%d" % r[3] for r in conn.execute("SELECT project,plan_path,package,steps FROM bee_step_counts") if r[3] > 0}


# Describe one running Bee as a row of the Bees table.
def bee_row(agent, now, steps=None, ctx_limit=None, package_steps=None) -> dict:
    """Row of one running Agent: kind "agent", person name, model label, plan, project, plan path, task (with its reported step like 2/5), context, tokens, status and runtime."""
    name = str(agent.get("person") or agent.get("name"))
    step = (package_steps or {}).get((agent.get("project"), agent.get("plan_path") or "", agent.get("package"))) or (steps or {}).get(name, "")
    tin, tout = agent_tokens(agent)
    return {"kind": "bee", "name": name, "agent": agent.get("name"), "project": agent.get("project"), "plan_path": agent.get("plan_path") or "",
            "model": overview.model_short(agent.get("provider"), agent.get("model"), agent.get("effort")),
            "hive": hive_name(agent.get("plan_path") or ""), "apiary": agent.get("project"),
            "task": str(agent.get("task") or agent.get("package") or i18n.log_text(str(agent.get("last_line") or "")))[:40].strip(), "step": step, "step_frac": step_fraction(step),
            "ctx": int(agent.get("ctxTokens") or 0), "ctx_limit": int(ctx_limit(agent)) if ctx_limit else 0, "tin": tin, "tout": tout,
            "status": "running" if agent.get("status") in runtime.ACTIVE_STATUSES else str(agent.get("status")), "runtime": fmt_elapsed(agent.get("started_at"), now)}


# Describe the running Queen of a Hive for the Bees table, from the Hive's row.
def queen_row(hive) -> dict:
    """Row of the running Queen of the Plan row `plan` (kind "queen"): name, model with effort, context, tokens and runtime; the Plan table does not repeat them."""
    return {"kind": "queen", "name": hive["queen"], "agent": hive["queen_agent"], "project": hive["apiary"], "plan_path": hive["plan_path"],
            "model": hive["queen_model"] or "–", "hive": hive["name"], "apiary": hive["apiary"], "task": "Queen",
            "ctx": hive["queen_ctx"], "ctx_limit": hive["queen_ctx_limit"], "tin": hive["queen_tin"], "tout": hive["queen_tout"],
            "status": "running", "runtime": hive["runtime"]}


# Read the embedded Bees (subagents of a Queen) that have not reported yet.
def embedded_rows(conn) -> list:
    """The open rows of the table embedded_bees as dicts, oldest first; none when the table does not exist yet."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        return []
    return [dict(r) for r in db.rows(conn, "SELECT * FROM embedded_bees WHERE ended_at IS NULL ORDER BY started_at")]


# Collect the Bees of the active Hives: running Bee processes and packages that have a Bee assigned but no process yet.
def bee_list(agents, assigned, completed, now, ctx_limit=None, steps=None, hive_keys=None, hive_rows=None, embedded=None, package_steps=None, startable=None, stopped=None) -> list:
    """Rows for the Agents table. A package with an Agent assignment but no running process shows as 'waiting'; finished packages are left out.

    `hive_keys` limits the waiting rows to the (project, plan) pairs whose Queen is running: only a running Queen starts Agents, so the assignments of any other Plan are not waiting Agents.
    With `hive_rows` (the Plan rows) every Plan with a running Queen gets the Queen's row first and its Agents directly below it; Agents of other Plans follow."""
    rows, seen = [], set()
    for agent in agents:
        if agent.get("phase") == "bee" and agent.get("status") in runtime.ACTIVE_STATUSES:
            rows.append(bee_row(agent, now, steps, ctx_limit, package_steps))
            seen.add((agent.get("project"), agent.get("plan_path"), agent.get("package")))
    for bee in embedded or ():   # a Bee that runs as a subagent of its Queen has no agent row: it shows up under her like any other Bee
        if hive_keys is not None and (bee["project"], bee["plan_path"]) not in hive_keys:
            continue
        step = (package_steps or {}).get((bee["project"], bee["plan_path"], bee["package"])) or (steps or {}).get(bee["name"], "")
        queen = next((a for a in agents if a.get("phase") == "queen" and a.get("status") in runtime.ACTIVE_STATUSES
                      and (a.get("project"), a.get("plan_path")) == (bee["project"], bee["plan_path"])), None)
        # The runner of the Queen reads each subagent's context from her event stream (no extra model call); the Task description names the Bee.
        sub = match_sub(queen, bee["name"])
        rows.append({"kind": "bee", "name": bee["name"], "agent": None, "project": bee["project"], "plan_path": bee["plan_path"],
                     "model": overview.model_short(bee["provider"], bee["model"], bee["effort"]), "hive": hive_name(bee["plan_path"]), "apiary": bee["project"],
                     "task": str(bee["package"]).strip(), "step": step, "step_frac": step_fraction(step),
                     "ctx": int((sub or {}).get("ctx") or 0), "ctx_limit": int(ctx_limit(queen)) if sub and ctx_limit else 0,
                     "tin": int((sub or {}).get("tin") or 0), "tout": int((sub or {}).get("tout") or 0),
                     "status": "stopping" if bee.get("outcome") == "stopping" else "running", "stopping": bee.get("outcome") == "stopping",
                     "runtime": fmt_elapsed(bee["started_at"], now), "embedded": True, "package": bee["package"]})
        seen.add((bee["project"], bee["plan_path"], bee["package"]))
    for (project, plan, package) in sorted(assigned or {}):
        if (project, plan, package) in seen or (project, plan, package) in (completed or ()) or (hive_keys is not None and (project, plan) not in hive_keys):
            continue
        # A Bee that has not started has no name yet (names are given at the start) and its stored provider is only the choice of an earlier try,
        # so neither the package id nor that provider is shown as the Bee's name or model.
        rows.append({"kind": "bee", "name": "–", "agent": None, "project": project, "plan_path": plan, "model": "–", "hive": hive_name(plan),
                     "apiary": project, "task": package, "ctx": 0, "ctx_limit": 0, "tin": 0, "tout": 0, "status": "waiting", "runtime": "–"})
        seen.add((project, plan, package))
    for (project, plan), names in sorted((startable or {}).items()):   # Startable packages the running Queen has not started yet
        for package in names:
            if (project, plan, package) in seen or (hive_keys is not None and (project, plan) not in hive_keys):
                continue
            rows.append({"kind": "bee", "name": "–", "agent": None, "project": project, "plan_path": plan, "model": "–", "hive": hive_name(plan),
                         "apiary": project, "task": package, "ctx": 0, "ctx_limit": 0, "tin": 0, "tout": 0, "status": "startable, not started", "runtime": "–"})
            seen.add((project, plan, package))
    for (project, plan, package) in sorted(stopped or ()):   # A package the owner stopped stays here until the owner resumes it
        if (project, plan, package) in seen or (project, plan, package) in (completed or ()):
            continue
        rows.append({"kind": "bee", "name": "–", "agent": None, "project": project, "plan_path": plan, "model": "–", "hive": hive_name(plan),
                     "apiary": project, "task": package, "package": package, "ctx": 0, "ctx_limit": 0, "tin": 0, "tout": 0, "status": "stopped", "stopped": True, "runtime": "–"})
        seen.add((project, plan, package))
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
    """(time, tag, text) of an event row: tag is QUEEN x, AGENT x, PROJECT x or SYSTEM depending on who wrote it.

    `who` maps an agent name to (phase, person) so the tag shows the Queen or Agent by the person's name."""
    when = db.parse_time(event["ts"])
    stamp = localtime.time(when) if when else "--:--"
    agent, project = event["agent"], event["project"]
    if agent:
        phase, person = (who or {}).get(agent, (str(agent).split("__")[-1], str(agent).split("__")[0]))
        tag = ("QUEEN" if str(phase).startswith("queen") else "AGENT") + " " + str(person)
    elif project and project != runstate.GLOBAL_PROJECT:
        tag = "PROJECT " + str(project)
    else:
        tag = "SYSTEM"
    text = str(event["text"] or event["kind"]).replace("\n", " ")
    return stamp, tag, text if free_text(event) else i18n.log_text(text)


# Tell a line somebody wrote from a line of the program.
def free_text(event) -> bool:
    """True for words of an agent or the owner (a note of an agent, a question, an answer, a final report, a ready line): the log shows them as written, whatever they look like."""
    return event["kind"] in ("question", "answer", "done", "ready") or (event["kind"] == "note" and bool(event["agent"]))


# Sum the tokens of every Apiary and Hive over the activity window.
def activity_rows(agents_all, now) -> list:
    """Token use of the last ACTIVITY_DAYS days: [{kind, name, project, pct, tin, tout, last}] with the Projects first, then the Plans, each busiest first.

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
                         "tin": tin, "tout": tout, "last": localtime.date_time(last)})
    return rows


# Read the integration queue and open questions for the apiary-queue panel.
def queue_panel(conn, project) -> dict:
    """Open question count, queued integration rows and their short labels (PROJECT#id)."""
    where, args = ("WHERE project=?", (project,)) if project else ("", ())
    glue = " AND" if where else " WHERE"
    open_q = conn.execute("SELECT COUNT(*) FROM questions " + where + glue + " status='open'", args).fetchone()[0]
    queued = db.rows(conn, "SELECT id,project FROM queue " + where + glue + " status IN ('ready','in_run') ORDER BY id", args)
    return {"open": open_q, "queued": len(queued), "labels": ["%s#%d" % (q["project"], q["id"]) for q in queued][:4]}


# Tell routine round events that report nothing apart from the rest.
def is_noise(event) -> bool:
    """True for round events that say nothing happened (no Queen started, hook ok, nothing cleaned up): the log panel hides them unless asked."""
    text = str(event.get("text") or "")
    return event.get("kind") == "rounds" and (text.startswith("round done: 0 Queen(s) started") or text.startswith("event hook ok")
                                               or (text.startswith("Worktree cleanup:") and '"removed": []' in text))


# What a log line needs for filtering.
def log_meta(event, plan_of) -> dict:
    """{project, agent, plan, noise} of an event: `plan` is the name of the Plan of the agent that wrote it ("" for events without an agent)."""
    plan = plan_of.get(event["agent"]) if event["agent"] else ""
    return {"project": event["project"], "agent": event["agent"], "hive": hive_name(plan) if plan else "", "noise": is_noise(event)}


# Describe the provider limits as one row per provider.
def limit_rows(d) -> list:
    """One dict per provider in the order of the settings: {name, model, next, reserve, slots, weekly, five}.

    `reserve` is {text, level, hover, age}: the quota reserve in whole percent ((quota factor - 1) x 100), its colour level (ok, bad or dim),
    the hover sentence and the age of a last verified reading. `slots` is {total, used, extra, none, running}. `weekly` is {pct, level, text}
    (`pct` None = no bar) and `five` the 5-hour line ("" = none). All texts are in the language of the GUI."""
    def level(pct):
        """Colour level of a used share."""
        return {"green": "ok", "yellow": "warn", "red": "bad"}[overview.level_colour(pct)] if pct is not None else "dim"
    def age_text(p):
        """Age of the last verified reading, like 2h or 15min."""
        minutes = p["age_minutes"] if p.get("age_minutes") is not None else int((p.get("stale_hours") or 0) * 60)
        return "%dh" % (minutes // 60) if minutes >= 60 else "%dmin" % minutes
    out = []
    for p in d["provider_limits"]:
        verified = bool(p.get("stale")) or (bool(p.get("error")) and p.get("age_minutes") is not None)   # the newest check failed, an older reading is shown
        due = p.get("hours") is not None and p["hours"] <= 0
        reset_date = datetime.fromtimestamp(p["reset_at"]) if p.get("reset_at") else None
        reset = reset_date.strftime("%H:%M" if reset_date.date() == datetime.now().date() else "%d.%m. %H:%M") if reset_date else ""
        reserve = {"text": "", "level": "dim", "hover": "", "age": ""}
        blocked_slots = False
        if not p["has_limit"]:   # a provider without a limit never runs out and gets work first: the reserve shows the highest value
            reserve.update(text="+99 %", level="ok", hover=tr("No limit: this provider never runs out and gets work first."))
        elif p["used_pct"] is None:
            reserve["text"] = tr("unknown")
            reserve["hover"] = p.get("error") or ""
            if p.get("error"):   # the newest check failed and no verified reading is young enough: red "unknown", no new work (reserve 0), grey slot cells
                reserve["level"] = "bad"
                blocked_slots = True
        else:
            if verified:
                reserve["age"] = age_text(p)
            ratio = p.get("quota_reserve", p["ratio"])
            if ratio is None:
                reserve["text"] = tr("unknown")
            else:
                reserve["text"] = quota_reserve.display(min(ratio, 2.0))
                reserve["level"] = "bad" if ratio == 0 or reserve["text"].startswith("−") else "ok"
                pct = abs(round((ratio - 1) * 100)) if ratio != float("inf") else 99
                reserve["hover"] = tr("%d %% more quota left than time until the reset (%s)" if ratio >= 1 else "%d %% less quota left than time until the reset (%s)", pct, reset)
        total = int(p["total_slots"] or 0)
        used = int(p["used_slots"] or 0)
        slots = {"total": total, "used": used, "extra": max(0, used - total), "none": tr("no slots") if not total else "",
                 "running": tr("%d running", used) if not total and used else ""}
        slots["blocked"] = blocked_slots or reserve["text"] == "0 %"
        if p.get("offline"):   # an offline provider shows "offline" in red instead of its free slots
            slots["offline"] = tr("offline")
            slots["hover"] = tr("Offline since %s. HaiveQueen checks again and uses this provider as soon as it answers.", p["offline"])
        if not p["has_limit"]:
            weekly = {"pct": None, "level": "dim", "text": tr("no limit")}
        elif p["used_pct"] is None:
            weekly = {"pct": None, "level": "dim", "text": tr("7d unknown")}
        else:
            pct = round(p["used_pct"])
            when = tr("reset now") if due else (reset if reset else tr("reset unknown"))
            weekly = {"pct": pct, "level": level(p["used_pct"]), "text": "7d %d %% · %s" % (pct, when)}
        five = ""
        if p.get("five_hour"):
            five = tr("5h reset now") if p["five_hour"]["pct"] is None else "5h %d %%" % p["five_hour"]["pct"]
            if p["five_hour"].get("reset") and p["five_hour"]["pct"] is not None:
                five += " · " + p["five_hour"]["reset"]
            if verified:
                five += " · " + age_text(p)
        out.append({"name": p["name"].capitalize(), "model": p["model"] or tr("no medium model"), "next": bool(p["next"]),
                    "reserve": reserve, "slots": slots, "weekly": weekly, "five": five})
    return out


# Describe the token usage per model per model.
def usage_lines(d) -> list:
    """[(text, colour)] with one line per model: today, all plans and total input/output."""
    def cell(v):
        """Input/output of one usage cell, or 'no data'."""
        return tr("no data") if v is None else "%s/%s" % (overview.fmt_tok0(v[0]), overview.fmt_tok0(v[1]))
    return [(tr("%s  today %s  plans %s  total %s", m["Label"].replace("codex-auto-review", "Auto-Review"), cell(m["Today"]), cell(m["Plans"]), cell(m["Total"])), "text")
            for m in d["usage"]] or [(tr("no data"), "dim")]


# Collect the sections of the status panel: everything the overview data holds that has no place in the other panels.
def info_sections(d) -> list:
    """[(title, [(text, colour)])] for run state, usage, system, plans, questions, round errors and the last self-update (the limits have their own panel, see limit_rows)."""
    run = d["run"]
    state = {"RUNNING": "ok", "PAUSED": "warn"}[run["state"]]
    sub = {"RUNNING": "", "PAUSED": tr("no new agents")}[run["state"]]
    out = [(tr("STATE"), [(tr(run["state"]) + ("  " + sub if sub else "") + tr("  Project: %s", d["project"] or tr("all")), state),
                          (tr("Orchestrator %s", d["orch"]["status"]), "text")])]
    plans = "  ".join("%s %d" % (name, d["counts"].get(name, 0)) for name, _ in overview.PLAN_BADGES)
    if d["counts"].get("DRAFT"):
        plans += tr("  (draft: %s)", ", ".join(d["drafts"]))
    out.append((tr("USAGE"), usage_lines(d)))
    system = [(d["bj"], "text")] if d["bj"] else []
    out.append((tr("SYSTEM"), system + [(i18n.decimal("RAM %d%% %.1f/%.0f GB" % (round(100 * (1 - d["sysinfo"]["free_mb"] / d["sysinfo"]["total_mb"])) if d["sysinfo"]["total_mb"] else 0,
                                                                  (d["sysinfo"]["total_mb"] - d["sysinfo"]["free_mb"]) / 1024.0, d["sysinfo"]["total_mb"] / 1024.0)), "text")]))
    out.append((tr("PLANS"), [(plans, "text")] + [(tr("%s %d / %d integrated (%d%%)", st["project"], st["integrated"], st["total"], st["percent"]), "dim")
                                                  for st in d["status"]]))
    if d["qs"]:
        qs = [(tr("open: %d  %s", len(d["qs"]), ", ".join("%s#%s" % (q["project"], q["id"]) for q in d["qs"])), "warn")]
    else:
        qs = [(tr("none open"), "dim")]
    out.append((tr("QUESTIONS"), qs))
    if d.get("global_faults"):   # faults that belong to no Apiary: shown here, never as an inbox question
        out.append((tr("FAULTS"), [((localtime.time(db.parse_time(f["at"])) if db.parse_time(f["at"]) else "--:--") + " " + i18n.log_text(f["text"]), "bad")
                                   for f in d["global_faults"]]))
    if d["round_errors"]:
        out.append((tr("ROUND ERRORS"), [((localtime.time(db.parse_time(e["ts"])) if db.parse_time(e["ts"]) else "--:--") + " " + e["project"] + " " + i18n.log_text(e["text"]), "bad")
                                     for e in d["round_errors"]]))
    if d["selfupdate"]:
        out.append((tr("SELF-UPDATE"), [("".join(text for text, _ in overview.self_update_line(d["selfupdate"])), "text")]))
    return out


# Combine the loop process and the run state into the one state the window shows.
def orchestrator_state(run_state, loop_pid, stop_pending) -> dict:
    """{label, level}: STOPPED (no loop process), STOPPING (a stop waits for the running agents), else the run state RUNNING or PAUSED."""
    if not loop_pid:
        return {"label": "STOPPED", "level": "bad"}
    if stop_pending:
        return {"label": "STOPPING", "level": "warn"}
    return {"label": run_state, "level": {"RUNNING": "ok", "PAUSED": "warn"}.get(run_state, "warn")}


# The faults of the program itself for the status panel.
def _global_faults(conn, now) -> list:
    """Global faults to show; none when they cannot be read."""
    try:
        from . import faults
        return faults.global_faults(conn, now)
    except Exception:
        return []


# Read the installed package version for the header.
_VERSION_CACHE = {}


def _version() -> str:
    """The installed version (release tag of the install folder, see updates.installed_version), read at most once a minute; an en dash when unknown."""
    import time
    now = time.monotonic()
    if _VERSION_CACHE and now - _VERSION_CACHE["at"] < 60:
        return _VERSION_CACHE["value"]
    try:
        from . import updates
        value = updates.installed_version()
    except Exception:
        value = "–"
    _VERSION_CACHE.update(at=now, value=value)
    return value


# Collect everything the graphical dashboard shows in one call.
def snapshot(conn, project=None, now=None, cfg=None, sysinfo=None) -> dict:
    """Plain dict with the header tiles, the Plan and Agent tables, the log, activity, system, queue and status panels.

    The numbers come from overview.collect(). `project` filters to one Project;
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
    plan_rejected = {(r[0], r[1]) for r in conn.execute("SELECT project, path FROM plans WHERE status='CANCELLED'")}
    plan_done = {(r[0], r[1]) for r in conn.execute("SELECT project, path FROM plans WHERE status='DONE'")}   # rejected and done Hives only show in the Hives window
    embedded = embedded_rows(conn)
    hive_rows = [hive_row(h, agents, packages, now, assigned, completed, ctx_limit, agents_all, impl, paused, rejected, waiting, embedded, plan_rejected) for h in hives.hive_data(conn, project, agents)]
    live = [a for a in agents if a.get("status") in runtime.ACTIVE_STATUSES]
    queens = [a for a in live if a.get("phase") == "queen"]
    steps = {k: v for h in hive_rows for k, v in h["steps"].items()}
    package_steps = step_counts(conn)   # a running Bee starts at 0/m of the steps it was given ...
    package_steps.update({(h["apiary"], h["plan_path"], p): step for h in hive_rows for p, step in h["package_steps"].items()})   # ... and shows the newest step report of its package
    from . import bees as bee_mod
    stopped = [s for s in bee_mod.stopped_packages(conn) if not project or s[0] == project]
    bees = bee_list(agents, assigned, completed, now, ctx_limit, steps, {(h["apiary"], h["plan_path"]) for h in hive_rows if h["live"]}, hive_rows, embedded, package_steps,
                    bee_mod.startable_map(conn), stopped)
    workers = [b for b in bees if b["kind"] == "bee"]
    apiaries = sorted({h["apiary"] for h in hive_rows})
    slots = sum(int(p["total_slots"] or 0) for p in d["provider_limits"])
    today = [m["Today"] for m in d["usage"] if m.get("Today")]
    who = {a["name"]: (a.get("phase") or "", a.get("person") or a["name"]) for a in agents_all}
    plan_of = {a["name"]: a.get("plan_path") for a in agents_all}
    events = db.rows(conn, "SELECT id,ts,project,kind,agent,text FROM events" + (" WHERE project IN (?, ?)" if project else "") +
                     " ORDER BY id DESC LIMIT %d" % LOG_FETCH, (project, runstate.GLOBAL_PROJECT) if project else ())
    kept = [e for e in events if not is_noise(e)][:LOG_LINES]   # the newest LOG_LINES events that carry news, plus the noise that is newer than the oldest of them
    floor = kept[-1]["id"] if kept else 0
    events = [e for e in events if e["id"] >= floor]
    try:
        from . import loop
        orchestrator = orchestrator_state(d["run"]["state"], loop.loop_running(), loop.stop_flag().exists())
    except Exception:   # a broken lock file must not stop the window: show the run state alone
        orchestrator = orchestrator_state(d["run"]["state"], 1, False)
    shown = [h for h in hive_rows if not h["plan_rejected"] and (h["apiary"], h["plan_path"]) not in plan_done]
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    overlays, start_tile = gui_overlays.build(conn, d["usage"], agents_all, shown, bees, apiaries, day, [str(p.get("name")) for p in cfg.get("providers") or [] if p.get("name")])
    return {"now": now, "project": project, "run_state": d["run"]["state"], "orchestrator": orchestrator, "version": _version(),
            "tiles": {"apiaries": len(apiaries), "hives": sum(1 for h in hive_rows if h["live"] and not h["plan_rejected"] and (h["apiary"], h["plan_path"]) not in plan_done), "queens": len(queens), "bees": sum(1 for b in workers if b["status"] == "running"),
                      "bee_capacity": max(len(workers), slots), "tokens_in": sum(t[0] for t in today), "tokens_out": sum(t[1] for t in today), "start_ctx": start_tile},
            "overlays": overlays,
            "hives": [h for h in hive_rows if not h["plan_rejected"] and (h["apiary"], h["plan_path"]) not in plan_done], "bees": bees, "apiaries": apiaries,
            "projects": [r["name"] for r in conn.execute("SELECT name FROM projects ORDER BY priority DESC, name")],
            "log": [log_line(e, who) for e in reversed(events)], "log_ids": [e["id"] for e in reversed(events)], "log_meta": [log_meta(e, plan_of) for e in reversed(events)], "activity": activity_rows(agents_all, now),
            "system": sysinfo if sysinfo is not None else system_info(d["sysinfo"]), "queue": queue_panel(conn, project),
            "info": info_sections({**d, "global_faults": _global_faults(conn, now)}), "limits": limit_rows(d)}


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
    """[(gui colour name, text)] of the last lines of an agent's live file: a line "<Colour><TAB><text>" gets that colour, any other line the plain text colour.
    Program lines are shown in the language of the GUI; the words of the agent stay as written."""
    out = []
    for row in tail_file(path, lines):
        word, tab, rest = row.partition(chr(9))
        out.append((LIVE_COLOURS[word], i18n.live_text(word, rest)) if tab and word in LIVE_COLOURS else ("text", row))
    return out
