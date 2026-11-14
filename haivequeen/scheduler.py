"""Scheduler: settings, provider list and selection by weekly limit, slots, classes and escalation, bundling helpers, multi-project order.

Ports the provider and class parts of lib.ps1 one to one onto dicts and the database. Every function is plain Python without
global state; the database connection is only needed for the Claude usage window (kv) and the agent rows.
"""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import config, db, offline, plans, profile, runtime, servers
from . import platform as osplat
from . import localtime
from .i18n import tr

CLASS_NAMES = ("light", "medium", "heavy")
USES = ("preferred", "fallback")
BEE_MODES = ("subagent", "process")   # how the Queen of a provider runs her Bees: embedded subagents or separate agent processes
GLOBAL = "*"   # project value of machine-wide kv rows
ACTIVE = ("running", "starting")

# Prompt templates of the agents the routine starts itself (placeholders {name}, see expand_template). A project may replace any of them
# in the [prompts] table of its project.toml.
DEFAULT_PROMPTS = {
    "answeredQuestion": "Plan: {plan}, item {item} (question {qid}). {owner} answered: {answer} Implement exactly this answer. {where} Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the item is finished.",
    "verifyOpenWork": "Plan: {plan}, package {pkg}. The independent verification {verifier} answered that requirements are open (answer: {answerPath}). Implement exactly the open requirements it lists, nothing else, and nothing the plan leaves to {owner}. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state with evidence per requirement, end when all of them are done.",
    "restartWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} never got a pane (technical failure of the window); start the package again from the plan (what is already done stays done). Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
    "workInWorktree": "Work in the worktree {cwd} and the branch the plan names for this package (never invent them).",
    "interruptedWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} was interrupted (laptop sleep, network or API problem, or its process vanished), not finished. Continue exactly where it stopped: the work already in the worktree is kept, what is already done stays done. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
    "unfinishedWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} ended without a new commit on its branch, so the package is not finished. Read its report ({answerPath}) and the handoff in the plan's state file, continue exactly where it stopped, commit your work and do not repeat what is already done. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
}

# Settings every project gets; machine config.toml and project.toml overlay them (project over machine over these defaults).
DEFAULT_SETTINGS = {
    "owner": "Owner",
    "hives": 2,
    "excludedProviders": [],
    "queenClass": "medium",
    "diagnosisClass": "medium",
    "beesPerQueen": 3,
    "roundMinutes": 5,
    "escalateAfter": 3,
    "clockJumpSeconds": 120,   # a wall clock jump above this many seconds during an agent run counts as laptop sleep
    "providerRecheckMinutes": 5,   # how often an offline provider (its agent ended with a 5xx or connection error) is checked again
    "retryWaitMinutes": [1, 2, 4, 8, 15],   # wait before the n-th retry of consecutive interrupted ends (API, connection); the last value repeats
    "rateLimitWaitMinutes": 15,   # wait after a rate limit when no reset time of the limited provider is known
    "compileStallMinutes": 10,   # `bzz compile-check` ends a check red as stalled after this many minutes without output and without CPU use
    "maxConcurrentCompileChecks": 2,   # the most compile checks that run at the same time on this computer (machine-wide, all projects)
    "compileMaxMinutes": 120,   # `bzz compile-check` ends a check red after this many minutes of running (waiting for another check does not count)
    "contextLimitTokens": 300000,
    "integrationBranch": "main",
    "remoteName": "origin",
    "worktreeLocation": "",
    "ownerTopics": [],
    "compileCheck": "",
    "buildCommand": "",    # the Build step of the Orchestrator pipeline: one command line run in the plan's worktree; empty = the step is skipped
    "deployCommand": "",   # the Deploy step of the Orchestrator pipeline, likewise
    "deleteFinishedWorktrees": True,
    "deleteIntegratedBranches": False,
    "publishReleases": False,   # HaiveQueen publishes a GitHub release at each integration (only for a remote on GitHub)
    "nextVersion": "",          # the owner's next version (e.g. 2.1.0); without it the last digit counts up
    "worktreePrefix": "wt",
    "buildProcessNames": [],
}
MACHINE_ONLY = ("hives", "excludedProviders", "providers", "providerRecheckMinutes", "slots", "buildJobs", "maxConcurrentCompileChecks", "ramReserveMb", "weeklyLimitSources", "exportDir", "projects")


def effective_verify(settings: dict, verify) -> str:
    """Who closes a package: its verify value, except that a package marked build is closed like a code package: the build is the Build step of the
    Orchestrator after `bzz ready`, so it never holds the Queen."""
    value = str(verify or "code")
    return "code" if value == "build" else value


def deep_merge(base: dict, over: dict) -> dict:
    """Return base overlaid with over (nested dicts are merged key by key, everything else is replaced)."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# Merge defaults, machine settings and project settings (project.toml); machine-only keys cannot be overridden by a project.
def build_settings(machine: dict, project_cfg: dict, legacy_deploy: bool = False, workspace=None) -> dict:
    """Merge defaults, machine settings and project settings (project.toml); machine-only keys cannot be overridden by a project."""
    defaults = DEFAULT_SETTINGS   # legacy_deploy has no effect any more: the switches are gone, the Deploy command decides
    merged = deep_merge(defaults, {k: v for k, v in machine.items() if k not in ("projects",)})
    from . import projsettings
    over = {k: v for k, v in project_cfg.items() if k not in MACHINE_ONLY and k not in projsettings.DROPPED_FROM_PROJECT}   # General and the providers decide the dropped ones
    merged = deep_merge(merged, over)
    for key in ("progressVerifies", "planStallHours", "minClass", "bundle", "openMarker", "triage", "brain", "integratorSlots"):
        merged.pop(key, None)
    projsettings.apply_derived(merged, project_cfg, workspace)   # remote and integration branch from the repository
    if "minFreeRamGB" not in merged:
        merged["minFreeRamGB"] = float(machine.get("ramReserveMb", 2048)) / 1024.0
    return merged


def expand_template(template: str, values: dict) -> str:
    """Replace every {name} by its value in one pass (a value that itself contains {name} is not expanded again)."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def prompt_text(settings: dict, name: str, values: dict, base_values: dict) -> str:
    """Return the prompt template `name` (project override, else the default) expanded with base_values and values."""
    tpl = (settings.get("prompts") or {}).get(name, DEFAULT_PROMPTS.get(name))
    if tpl is None:
        raise ValueError("prompt template '%s' is missing" % name)
    merged = dict(base_values)
    merged.update(values)
    return expand_template(str(tpl), merged)


def to_slug(text: str) -> str:
    """Return a folder and branch name: lower-case letters, digits and single hyphens; 'pkg' when nothing is left."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "pkg"


def norm_plan(path) -> str:
    """Return a plan path in one comparable form (forward slashes, lower case)."""
    return str(path or "").replace("\\", "/").lower()


# ---- classes ----

def class_index(name) -> int:
    """Return the index of a class (light 0, medium 1, heavy 2); empty or unknown counts as medium."""
    s = str(name or "").strip().lower()
    return CLASS_NAMES.index(s) if s in CLASS_NAMES else 1


def resolve_agent_class(settings: dict, phase: str, requested: str, failures: int = 0) -> str:
    """Final class of an agent start: raised one class per `escalateAfter` failures (never above heavy), then to the phase minimum."""
    i = class_index(requested)
    every = max(1, int(settings.get("escalateAfter", 2)))
    i = min(2, i + failures // every)
    return CLASS_NAMES[i]


# ---- providers ----

def declared_capabilities(p):
    capabilities = [canonical_capability(c) for c in (p.get("capabilities") or [])]
    if not p.get("capabilitiesVersion"):
        if p.get("use") != "capability-only" and "Coding" not in capabilities:
            capabilities.append("Coding")
        if not servers.is_local(p) and "Queen" not in capabilities:
            capabilities.append("Queen")
    return list(dict.fromkeys(capabilities))


def provider_capabilities(p):
    capabilities = declared_capabilities(p)
    from . import mcp_setup
    verified = set(mcp_setup.status(p)["available"])
    return [c for c in dict.fromkeys(capabilities) if c in mcp_setup.NATIVE or c in verified]


def capability_thresholds(p):
    """Validate stored thresholds and fill defaults, including legacy capabilities."""
    values = p.get("capabilityThresholds", {})
    if not isinstance(values, dict):
        raise ValueError(tr("provider %s: capability thresholds must be a table", p.get("name", "")))
    result = {}
    for capability, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValueError(tr("provider %s: capability threshold must be a whole percent from 1 to 100", p.get("name", "")))
        result[canonical_capability(capability)] = value
    for capability in declared_capabilities(p):
        result.setdefault(capability, 100)
    return result


def get_providers(settings: dict) -> list:
    """Return the provider entries as dicts (name, protocol, command, args, slots, use, capabilities, classes, limit, endpoint).

    A provider without `slots` gets the machine value `slots`. A wrong `use` or `protocol` raises ValueError.
    """
    out = []
    settings = servers.migrate(settings)   # an old endpoint entry of the Codex CLI is read as a provider with an own server
    for p in settings.get("providers") or []:
        name = str(p.get("name") or "")
        if not name:
            raise ValueError(tr("a provider entry has no name"))
        if p.get("server"):   # a [[servers]] entry that does not exist (the existing ones are folded into the providers by servers.migrate)
            raise ValueError(tr("provider %s: server %s does not exist", name, p["server"]))
        server = servers.server_of(p)
        access = servers.access_of(p)
        use = str(p.get("use") or "preferred")
        old_capability_only = use == "capability-only"
        if old_capability_only:
            use = "preferred"
        if use not in USES:
            raise ValueError(tr("provider %s: use must be preferred, fallback or capability-only, not '%s'", name, use))
        proto = str(p.get("protocol") or name)
        fmt = profile.check_protocol(name, proto, p.get("outputFormat"))   # a CLI HaiveQueen cannot read is refused here, with a clear message
        bee_mode = servers.bee_mode_of(p)
        if bee_mode not in BEE_MODES:
            raise ValueError(tr("provider %s: beeMode must be subagent or process, not '%s'", name, bee_mode))
        limit = p.get("limit")
        capabilities = provider_capabilities(p)
        out.append({
            "name": name, "enabled": p.get("enabled", True) is not False, "protocol": proto, "format": fmt, "command": str(p.get("command") or proto), "beeMode": bee_mode,
            "args": [str(a) for a in (p.get("args") or [])],
            "slots": int(p.get("slots", settings.get("slots", 0)) or 0), "use": use,
            "capabilities": capabilities, "capabilitiesVersion": 1, "capabilityThresholds": capability_thresholds(p),
            "classes": p.get("classes") or {},
            "limit": servers.limit_source(p),   # derived from the CLI and the access type, a stored source is not used
            "usageInterval": float(limit.get("interval", 60)) if isinstance(limit, dict) else 60,
            "endpoint": p.get("endpoint"), "server": server, "access": access, "apiKey": str(p.get("apiKey") or "") if access == "api" else "", "nameEdited": bool(p.get("nameEdited", True)), "claudeTools": str(p.get("claudeTools") or ""), "contextLimit": int(p.get("contextLimit") or 0), **profile.custom_fields(p),
        })
        out[-1]["local"] = servers.is_local(out[-1])
        # Ready: with account login the login in the HaiveQueen profile, with an own server the last connection test and the models; not ready = not available
        out[-1]["login"], out[-1]["notReady"] = servers.readiness(out[-1], profile.has_login(out[-1]))
    return out


def active_providers(settings: dict) -> list:
    """The providers of the settings that are switched on (a disabled provider gets no usage check and no readiness notice)."""
    return [p for p in get_providers(settings) if p["enabled"]]


def get_provider(providers: list, name: str):
    """Return the provider with that name or None."""
    for p in providers:
        if p["name"] == name:
            return p
    return None


def context_limit(settings: dict, providers: list, provider, cls) -> int:
    """Return the context size at which an agent of that provider and class is asked to hand off: the contextLimitTokens of its model entry
    under [providers.classes], else the global contextLimitTokens (project.toml over config.toml), else 300000."""
    found = get_provider(providers, provider) or {}
    entry = (found.get("classes") or {}).get(cls) or {}
    limit = int(entry.get("contextLimitTokens") or found.get("contextLimit") or settings.get("contextLimitTokens") or 300000)
    window = servers.window_of(found.get("server") or {})
    return min(limit, window) if window else limit   # an own server: hand off before the window of the server is full


def row_provider(row: dict) -> str:
    """Provider name of an agent row: the field provider, for old rows the field type."""
    return str(row.get("provider") or row.get("type") or "")


def embedded_slots(conn) -> dict:
    """Slots held by subagent Agents per provider: every embedded Agent whose end HaiveQueen has not closed yet (table embedded_bees, all Projects)."""
    if conn is None or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedded_bees'").fetchone():
        return {}
    return {str(r[0]): int(r[1]) for r in conn.execute("SELECT provider, COUNT(*) FROM embedded_bees WHERE ended_at IS NULL GROUP BY provider")}


# Free slots per provider: subtract every running or starting agent process and every open subagent Bee; never below 0.
def provider_free(providers: list, rows: list, conn=None) -> dict:
    """Free slots per provider: subtract every running or starting agent process and, with `conn`, every subagent Agent that is still open
    (an Agent inside its Queen holds a slot of her provider too); never below 0."""
    free = {}
    embedded = embedded_slots(conn)
    for p in providers:
        used = embedded.get(p["name"], 0) + sum(1 for r in rows if r.get("status") in ACTIVE
                                                and row_provider(r) == p["name"])
        free[p["name"]] = max(0, p["slots"] - used) if p.get("login", True) else 0
    return free


# ---- weekly limits ----

def format_reset_time(epoch) -> str:
    """Convert epoch seconds to local HH:MM, or dd.mm. HH:MM when not today."""
    if not epoch:
        return ""
    d = datetime.fromtimestamp(int(epoch))
    return localtime.time(d) if d.date() == datetime.now().date() else localtime.date_time(d)


def save_claude_usage(conn, event: dict) -> None:
    """Remember the newest Claude usage windows (from a rate_limit_event of a headless claude run) in the global kv key usage-claude."""
    w = ((event or {}).get("rate_limit_info") or {}).get("unifiedWindows")
    if not w:
        return
    runtime.kv_set(conn, GLOBAL, "usage-claude", json.dumps(
        {"saved": datetime.now().isoformat(timespec="seconds"), "fiveHour": w.get("five_hour"), "sevenDay": w.get("seven_day")}))


# Read verified Claude windows for the dashboard and provider ranking.
def claude_usage(conn) -> dict:
    """Return {FiveHour, SevenDay} of Claude from the newest kv row usage-claude (any project); each is None, {Pct, Reset, At} or a reset marker."""
    res = {"FiveHour": None, "SevenDay": None}
    try:
        row = conn.execute("SELECT value FROM kv WHERE key='usage-claude' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None or not str(row[0]).strip():
            return res
        j = json.loads(row[0])
        if j.get("refresh_error"):
            return res
        now = time.time()
        for key, win in (("FiveHour", j.get("fiveHour")), ("SevenDay", j.get("sevenDay"))):
            if not win:
                continue
            if win.get("resetsAt") is None:
                res[key] = {"Pct": round(100 * float(win["utilization"])), "Reset": "", "At": None}
            elif int(win.get("resetsAt", 0)) > now:
                res[key] = {"Pct": round(100 * float(win["utilization"])), "Reset": format_reset_time(win["resetsAt"]),
                            "At": int(win["resetsAt"])}
            else:
                res[key] = {"Pct": None, "Reset": "new window", "At": None}
    except (ValueError, TypeError, KeyError):
        pass
    return res


def codex_usage(sessions_dir=None) -> dict:
    """Return {FiveHour, SevenDay} of Codex from the newest session logs (last rate_limits block; windows by window_minutes)."""
    res = {"FiveHour": None, "SevenDay": None}
    base = Path(sessions_dir) if sessions_dir else profile.sessions_dir("codex")
    try:
        files = sorted(base.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        for f in files:
            with open(f, "rb") as h:
                h.seek(0, 2)
                size = h.tell()
                h.seek(max(0, size - 400 * 1024))
                data = h.read().decode("utf-8", errors="replace")
            lines = [ln for ln in data.split("\n") if '"rate_limits":{' in ln]
            if not lines:
                continue
            rl = (json.loads(lines[-1]).get("payload") or {}).get("rate_limits")
            if not rl:
                continue
            now = time.time()
            for w in (rl.get("primary"), rl.get("secondary")):
                if not w or int(w.get("resets_at", 0)) <= now:
                    continue
                v = {"Pct": round(float(w["used_percent"])), "Reset": format_reset_time(w["resets_at"]), "At": int(w["resets_at"])}
                res["FiveHour" if int(w.get("window_minutes", 0)) <= 400 else "SevenDay"] = v
            break
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return res


def without_limit(provider: dict) -> bool:
    """True for a provider without a usage limit (limit source none, as every Local LLM has): it never runs out."""
    return str(provider.get("limit") or "") in ("", "none")


# Do not rank a provider using a quota reading whose refresh failed.
def provider_limit(provider: dict, conn=None, limits=None) -> dict:
    """Weekly limit of a provider as {FreePct, Hours}: 100 minus the used percent and the hours until the reset.

    No limit source, no reading or an unknown value counts as 100 % free with a reset in 168 h. `limits` (name -> {usedPercent, resetHours})
    or the file in BZZ_PROVIDER_LIMITS_FILE replaces the real sources (tests); with a test runtime (BZZ_RUNTIME_OVERRIDE) and neither
    nothing is read from this PC.
    """
    if provider.get("limit") == "none":
        return {"FreePct": 100.0, "Hours": 168.0}
    used = hours = None
    table = limits
    lf = os.environ.get("BZZ_PROVIDER_LIMITS_FILE")
    if table is None and lf and Path(lf).is_file():
        table = json.loads(Path(lf).read_text(encoding="utf-8-sig"))
    if table is not None:
        entry = table.get(provider["name"])
        if entry:
            used, hours = entry.get("usedPercent"), entry.get("resetHours")
    elif not os.environ.get("BZZ_RUNTIME_OVERRIDE") and provider["limit"] in ("claude", "codex"):
        from . import usage
        kept = None
        if provider["limit"] == "claude" and conn is not None:
            from . import usage
            if usage.claude_refresh_failed(conn, provider=provider):
                kept = usage.retained_weekly(conn, provider=provider)   # the last verified weekly reading while it is recent; without any kept reading the quota counts as unavailable
                if kept is None and usage.retained_claude(conn, provider=provider) is None:
                    return {"FreePct": 0.0, "Hours": 168.0}
        u = ({"Pct": kept[0], "At": kept[1]} if kept else
             ({"Pct": w.get("pct"), "At": w.get("at")} if w else None) if provider["limit"] == "claude" and conn is not None and (w := usage.get_claude_usage(conn, provider=provider).get("seven_day"))
             else ({"Pct": w.get("pct"), "At": w.get("at")} if w else None) if provider["limit"] == "codex" and conn is not None and (w := usage.cached_codex_usage(conn, provider=provider).get("seven_day")) else None)
        if u and u.get("Pct") is not None:
            used = u["Pct"]
            if u.get("At"):
                hours = (float(u["At"]) - time.time()) / 3600
    if used is None:
        return {"FreePct": 100.0, "Hours": 168.0}
    return {"FreePct": max(0.0, 100.0 - float(used)), "Hours": float(hours) if hours is not None else 168.0}


def provider_reserve(provider: dict, conn=None, limits=None, now=None):
    """The quota reserve of a provider from both usage windows: weekly quota x 5-hour quota (the 5-hour one counts at most 1.0).

    None for a provider without limit (+99 %); a used-up window gives 0 (no new work until it resets, capability packages included).
    A passed reset counts as reset; an unknown reading counts as a full week free. A kept reading of a failed Claude check counts with both windows.
    `now` is the one clock reading a ranking shares, so equal usage gives exactly equal reserves."""
    from . import reserve
    source = str(provider.get("limit") or "") if not isinstance(provider.get("limit"), dict) else str((provider.get("limit") or {}).get("source", ""))
    p = {**provider, "limit": source}
    if without_limit(p):
        return None
    lim = provider_limit(p, conn, limits)
    now = time.time() if now is None else now
    weekly = reserve.window_quota(lim["FreePct"], reset_epoch=now + float(lim["Hours"]) * 3600.0, now=now)
    five = _five_hour_window(p, conn, limits, now)
    if five is None:
        return reserve.reserve(weekly)
    q5 = reserve.window_quota(five["free_pct"], reset_epoch=five["reset_epoch"], now=now, window_hours=5.0)
    return reserve.reserve(weekly, q5)


def _five_hour_window(provider: dict, conn=None, limits=None, now=None):
    """The 5-hour usage window of a provider as {free_pct, reset_epoch}, or None when it is not known."""
    name = provider["name"]
    now = time.time() if now is None else now
    table = limits
    lf = os.environ.get("BZZ_PROVIDER_LIMITS_FILE")
    if table is None and lf and Path(lf).is_file():
        table = json.loads(Path(lf).read_text(encoding="utf-8-sig"))
    if table is not None:
        entry = table.get(name) or {}
        wins = entry.get("windows") or []
        w = wins[0] if wins else None
        if w and w.get("usedPercent") is not None:
            hours = w.get("resetHours")
            return {"free_pct": max(0.0, 100.0 - float(w["usedPercent"])),
                    "reset_epoch": now + float(hours) * 3600 if hours is not None else None}
        return None   # the test table is authoritative: without a windows entry there is no 5-hour window
    source = str(provider.get("limit") or "")
    if conn is not None and source in ("claude", "codex") and not os.environ.get("BZZ_RUNTIME_OVERRIDE"):
        from . import usage
        if source == "claude" and usage.claude_refresh_failed(conn, provider=provider):
            kept = usage.retained_five_hour(conn, provider=provider)   # the kept five-hour window of the last verified reading, while it is recent
            return {"free_pct": max(0.0, 100.0 - float(kept[0])), "reset_epoch": kept[1]} if kept else None
        u = (usage.get_claude_usage(conn, provider=provider) if source == "claude" else usage.cached_codex_usage(conn, provider=provider)) or {}
        w = u.get("five_hour")
        if w and w.get("pct") is not None:
            return {"free_pct": max(0.0, 100.0 - float(w["pct"])), "reset_epoch": w.get("at")}
    return None


def reserve_zero(provider: dict, conn=None):
    """True when a usage window of the provider is used up (quota reserve 0): no new work until it resets."""
    resv = provider_reserve(provider, conn)
    return resv is not None and resv <= 0


def threshold_reached(provider, needs, conn=None, limits=None, now=None):
    """Whether either active usage window reached a required capability threshold."""
    if without_limit(provider):
        return False
    now = time.time() if now is None else now
    thresholds = provider.get("capabilityThresholds") or {}
    threshold = min((thresholds.get(canonical_capability(n), 100) for n in (needs or ["Coding"])), default=100)
    weekly = provider_limit(provider, conn, limits)
    if weekly["Hours"] > 0 and 100.0 - weekly["FreePct"] >= threshold:
        return True
    five = _five_hour_window(provider, conn, limits, now)
    return bool(five and (five["reset_epoch"] is None or five["reset_epoch"] > now)
                and 100.0 - five["free_pct"] >= threshold)


# ---- capabilities and selection ----

CAPABILITY_ALIASES = {"images": "Image generation", "blender": "3D modelling", "build": "Build"}

def canonical_capability(value):
    value = str(value).strip()
    return CAPABILITY_ALIASES.get(value.lower(), value)

DEFAULT_CAPABILITY_RULE = {"capability": "Image generation", "patterns": [
    r"(?i)\b(models?|textures?|materials?|particles?|icons?|clips?|sounds?)\b", r"(?i)\b(Blender|assets?|graphics?)\b"]}


def package_needs(settings: dict, content: str, capability: str = "") -> list:
    """Capabilities a package needs: those named in its plan header (comma separated) as they stand, else the capabilityRules matching its title."""
    declared = [canonical_capability(c) for c in str(capability or "").split(",") if c.strip()]
    if declared:
        return declared
    rules = settings.get("capabilityRules") or [DEFAULT_CAPABILITY_RULE]
    need = []
    for r in rules:
        if all(re.search(pat, content or "") for pat in r.get("patterns", [])) and canonical_capability(r["capability"]) not in need:
            need.append(canonical_capability(r["capability"]))
    return need


def _local_confidential(provider):
    from urllib.parse import urlparse
    import ipaddress
    endpoint = provider.get("endpoint") or {}
    try:
        url = (provider.get("server") or {}).get("address") or (endpoint.get("baseUrl", "") if isinstance(endpoint, dict) else "")
        host = urlparse(url).hostname
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def capability_available(providers: list, needs: list) -> bool:
    """True when at least one provider with ALL the capabilities has slots above 0 (slots 0 = off for everything)."""
    return any(p["slots"] > 0 and ("Confidential data" not in [canonical_capability(n) for n in needs] or _local_confidential(p)) and all(canonical_capability(n) in [canonical_capability(c) for c in p["capabilities"]] for n in needs) for p in providers)


def select_provider(providers: list, cls: str, needs, free: dict, avoid: str = "", prefer: str = "",
                    protocol: str = "", conn=None, limits=None):
    """Return the provider that gets a task of this class, or None.

    Candidates have slots above 0 and a free slot, the class in their entry, all needed capabilities (a package that needs one goes
    only to providers that have it); `capability-only` providers only get packages that need
    a capability. A provider with a used-up usage window (quota reserve 0) drops out for every package. `preferred` and `capability-only` come first, `fallback`
    only when none of them has a free slot. A provider without a limit (limit source none, every Local LLM) always comes first (list order among them);
    then the best quota reserve from both usage windows wins; equal reserves go to a local provider first, then list order. `prefer` keeps a
    follow-up with an earlier provider when it is a candidate, `avoid` (verify) prefers another provider, `protocol` limits to claude or codex.
    """
    cname = CLASS_NAMES[class_index(cls)]
    needs = [canonical_capability(n) for n in (needs or []) if n]
    cand = []
    now = time.time()
    for idx, p in enumerate(providers, 1):
        if p["slots"] <= 0 or int(free.get(p["name"], 0)) <= 0:
            continue
        if protocol and p["protocol"] != protocol:
            continue
        model = (p["classes"] or {}).get(cname)
        if not (model and model.get("model")):
            continue
        if "Confidential data" in needs and not _local_confidential(p):
            continue
        if any(n not in p["capabilities"] for n in needs):
            continue
        if "Coding" not in p["capabilities"] and not needs:
            continue
        if threshold_reached(p, needs, conn, limits, now):
            continue
        resv = provider_reserve(p, conn, limits, now)   # the quota reserve from both usage windows; None without limit (+99 %)
        if resv is not None and resv <= 0:
            continue   # a used-up window blocks every package until it resets, capability packages included
        cand.append((p, idx, resv))
    if not cand:
        return None
    pool = [c for c in cand if c[0]["use"] != "fallback"] or cand
    if prefer:
        hit = [c for c in pool if c[0]["name"] == prefer]
        if hit:
            return hit[0][0]
    if avoid:
        other = [c for c in pool if c[0]["name"] != avoid]
        if other:
            pool = other
    # the raw reserve decides (never a rounded one); the providers share one clock reading, so equal usage ties exactly: then a local provider
    # comes first (also between providers without a limit, +99 %), then the order of the settings
    return sorted(pool, key=lambda c: (not without_limit(c[0]), 0 if without_limit(c[0]) else -c[2], not servers.is_local(c[0]), c[1]))[0][0]


def endpoint_args(provider: dict) -> list:
    """Extra arguments of a provider with an endpoint (a Custom CLI or another CLI than Codex, kept as before) or an own server: a model_provider of the codex CLI
    with the base URL (-c key=value)."""
    if provider.get("server"):
        return servers.arguments(provider)
    ep = provider.get("endpoint") or {}
    if not ep.get("baseUrl"):
        return []
    pid = str(ep.get("id") or provider["name"])
    nm = str(ep.get("name") or provider["name"])
    return ["-c", "model_provider=" + pid, "-c", "model_providers.%s.name=%s" % (pid, nm),
            "-c", "model_providers.%s.base_url=%s" % (pid, ep["baseUrl"]), "-c", "model_providers.%s.wire_api=responses" % pid]


def class_model(providers: list, name: str, cls: str) -> dict:
    """Model and effort of a provider for a class; an unlisted class falls back to the nearest lower class it has, else the nearest higher."""
    p = get_provider(providers, name)
    i = class_index(cls)
    for k in list(range(i, -1, -1)) + list(range(i + 1, 3)):
        m = ((p or {}).get("classes") or {}).get(CLASS_NAMES[k])
        if m and m.get("model"):
            return {"Model": str(m["model"]), "Effort": str(m.get("effort", ""))}
    return {"Model": "", "Effort": ""}


# ---- model texts ----

def _title(word: str) -> str:
    """Capitalise the first letter, rest lower case (like ToTitleCase for one word)."""
    return word[:1].upper() + word[1:].lower()


def model_label(model: str) -> str:
    """Readable model name: claude-sonnet-5-5 -> Sonnet 5.5, gpt-6.1-sol -> GPT-6.1-Sol."""
    m = re.match(r"^claude-([a-z]+)-(\d+)-(\d+)", model or "")
    if m:
        return "%s %s.%s" % (_title(m.group(1)), m.group(2), m.group(3))
    parts = (model or "").split("-")
    out = []
    for i, x in enumerate(parts):
        if i == 0 and len(parts) > 1:
            out.append(x.upper())
        elif re.match(r"^\d", x):
            out.append(x)
        else:
            out.append(_title(x))
    return "-".join(out)


def model_short(protocol: str, model: str, effort: str) -> str:
    """Short model text for the first display line: 'Son 5.5 med.' / 'Sol 6.1 med.'."""
    eff = "med." if effort == "medium" else effort
    if protocol == "codex":
        m = re.match(r"^gpt-(\d+(?:\.\d+)?)-([a-z]+)", model or "")
        if m:
            return "%s %s %s" % (_title(m.group(2)), m.group(1), eff)
        return "%s %s" % (model, eff)
    nm = "Son" if "sonnet" in model else "Opus" if "opus" in model else "Hai" if "haiku" in model else model
    v = "5.5" if any(k in model for k in ("sonnet", "opus", "haiku")) else ""
    m = re.search(r"(\d+)[-.](\d+)", model)
    if m:
        v = m.group(1) + "." + m.group(2)
    return ("%s %s" % (nm, v)).strip() + " " + eff


def format_tokens(n) -> str:
    """Token count in short form (51k, 1.2M); a dash when unknown."""
    if n is None:
        return "–"
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%dk" % int(n / 1e3 + 0.5)
    return str(n)


# ---- project environment and order ----

class ProjectEnv:
    """Everything the round needs for one project: connection, name, workspace, merged settings and its providers."""

    def __init__(self, conn, name: str, workspace, settings: dict, colour: str = "white", priority: int = 0, plan_globs=None, plan_exclude=None):
        """Remember the project data (plan_globs: where its plan files are, plan_exclude: globs removed from them); the provider list is built from the settings."""
        self.plan_globs = list(plan_globs or ["docs/plans/*.md"])
        self.plan_exclude = list(plan_exclude or [])
        self.conn = conn
        self.name = name
        self.workspace = Path(workspace)
        self.settings = settings
        self.colour = colour
        self.priority = priority
        self.providers = offline.apply_state(get_providers(settings), conn)   # a disabled or offline provider has 0 slots

    @property
    def code_repo(self) -> Path:
        """Git main checkout of the project: `codeRepo` of project.toml when the code lives apart from the workspace, else the workspace."""
        cr = self.settings.get("codeRepo")
        return Path(cr) if cr else self.workspace

    def run_dir(self, agent: str) -> Path:
        """Folder of the raw files of one agent run (prompt.md, final.txt, HANDOFF, ...): <data>/runs/<project>/<agent>."""
        return osplat.runs_dir() / self.name / agent

    def log(self, text: str) -> None:
        """Write a history event and a line into the ROUNDS.log mirror."""
        runtime.append_event(self.conn, self.name, text, mirror="rounds")

    def event(self, text: str, error: bool = False) -> None:
        """Write a history event and a line into the ORCH-EVENTS.log mirror (what the dashboard log shows)."""
        runtime.append_event(self.conn, self.name, text, mirror="events", error=error)

    def agents(self, scope: str = "active") -> list:
        """Agent rows of the project (scope active, archive or all)."""
        return runtime.list_agents(self.conn, self.name, scope)

    def update_agent(self, name: str, fields: dict) -> dict:
        """Create or update the active row of an agent."""
        return runtime.upsert_agent(self.conn, self.name, name, fields)

    def packages(self, plan: str) -> list:
        """Package table of a plan: dicts with id, cls, content (title), depends, depends_all, phase, capability, verify. Dependency 'all' means every other package."""
        res = []
        for p in runtime.list_packages(self.conn, self.name, plan):
            deps = [str(d) for d in p["depends_on"] if d and not re.match(r"(?i)^all(e)?$", str(d))]
            cls = str(p["class"] or "")
            res.append({"id": str(p["id"]), "cls": cls.lower() if re.match(r"(?i)^(light|medium|heavy)$", cls) else "medium",
                        "content": str(p["title"] or ""), "depends": deps, "phase": str(p["phase"] or ""),
                        "depends_all": any(re.match(r"(?i)^all(e)?$", str(d)) for d in p["depends_on"]),
                        "capability": str(p["capability"] or ""), "verify": effective_verify(self.settings, p.get("verify"))})
        for r in res:
            if r["depends_all"]:
                r["depends"] += [x["id"] for x in res if x["id"] != r["id"] and x["id"] not in r["depends"]]
        return res


def load_projects(conn, machine: dict) -> list:
    """Return a ProjectEnv for every registered project that has a workspace folder (project.toml is read fresh each time)."""
    envs = []
    for row in db.rows(conn, "SELECT * FROM projects ORDER BY name"):
        ws = row["workspace"]
        if not ws or not Path(ws).is_dir():
            continue
        pcfg = config.load_project(ws)
        settings = build_settings(machine, pcfg, bool(row["legacy_deploy"]), ws)
        envs.append(ProjectEnv(conn, row["name"], ws, settings, pcfg.get("colour") or row["colour"] or "white",
                               int(pcfg.get("priority", row["priority"] or 0)), pcfg.get("planGlobs"), pcfg.get("planExclude")))
    return envs


def waiting_since(env: ProjectEnv, plan_path: str, package_id: str, now_iso: str = None) -> str:
    """First time (ISO) a package was seen waiting for an agent; stored under the kv key wait:<plan>|<package> on first sight."""
    key = "wait:%s|%s" % (plan_path, package_id)
    got = runtime.kv_get(env.conn, env.name, key)
    if got:
        return got
    stamp = now_iso or db.now()
    runtime.kv_set(env.conn, env.name, key, stamp)
    return stamp


def oldest_waiting(env: ProjectEnv) -> str:
    """ISO time of the oldest package of a QUEUED, RUNNING or WAITING plan that waits for an agent ('' when none waits).

    A package waits when it is not done and no active agent works on it. An agent that exists for it clears its marker.
    """
    active = {(norm_plan(a.get("plan_path")), str(a.get("package"))) for a in env.agents() if a.get("status") in ACTIVE}
    oldest = ""
    for plan in runtime.list_plans(env.conn, env.name):
        if plan["status"] not in ("QUEUED", "RUNNING", "WAITING"):
            continue
        for p in runtime.list_packages(env.conn, env.name, plan["path"]):
            key = "wait:%s|%s" % (plan["path"], p["id"])
            if p["phase"] == "done" or (norm_plan(plan["path"]), p["id"]) in active:
                runtime.kv_delete(env.conn, env.name, key)
                continue
            since = waiting_since(env, plan["path"], p["id"])
            if not oldest or since < oldest:
                oldest = since
    return oldest


def order_projects(envs: list) -> list:
    """Order projects for a round (D1, D4): highest `priority` first; ties by the oldest waiting package; projects with nothing waiting last."""
    keyed = []
    for env in envs:
        waiting = oldest_waiting(env)
        keyed.append((-env.priority, waiting or "9999-12-31T23:59:59", env.name, env))
    keyed.sort(key=lambda k: k[:3])
    return [k[3] for k in keyed]
