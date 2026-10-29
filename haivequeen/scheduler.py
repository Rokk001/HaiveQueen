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

from . import config, db, runtime
from . import platform as osplat

CLASS_NAMES = ("light", "medium", "heavy")
USES = ("preferred", "fallback", "capability-only")
PROTOCOLS = ("claude", "codex")
GLOBAL = "*"   # project value of machine-wide kv rows
ACTIVE = ("running", "starting")

# Prompt templates of the agents the routine starts itself (placeholders {name}, see expand_template). A project may replace any of them
# in the [prompts] table of its project.toml.
DEFAULT_PROMPTS = {
    "fixBlocked": "Plan: {plan}. The integrator blocked branch {branch} (tip {tip}). Fix exactly what the queue row says (integration queue of project {project}, row of {branch}): {status} Work in the worktree {cwd} on that branch, run {compileCheck} until exit 0 and the check scripts of the changed files, push normally and register the branch again with bzz ready (new tip, note 'Kompiliertest: Exit 0'). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the fix is finished.",
    "fixBlockedNoPlan": "The integrator blocked branch {branch} (tip {tip}) and the queue row has no plan. The row says: {status} The main checkout ({cwd}) stays on {integrationBranch} and is never edited: create your own worktree for that branch next to it ({newWorktree}), fix exactly what the row says, run {compileCheck} until exit 0 and the check scripts of the changed files, push normally and register the branch again with bzz ready (new tip, note 'Kompiliertest: Exit 0'). Follow all rules in section 1 of AGENT-RULES.md, end when the fix is finished.",
    "answeredQuestion": "Plan: {plan}, item {item} (question {qid}). {owner} answered: {answer} Implement exactly this answer. {where} Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the item is finished.",
    "verifyOpenWork": "Plan: {plan}, package {pkg}. The independent verification {verifier} answered that requirements are open (answer: {answerPath}). Implement exactly the open requirements it lists, nothing else, and nothing the plan leaves to {owner}. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state with evidence per requirement, end when all of them are done.",
    "restartWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} never got a pane (technical failure of the window); start the package again from the plan (what is already done stays done). Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
    "workInMainCheckout": "The main checkout ({cwd}) stays on {integrationBranch} and is never edited. Work in your own worktree next to it ({newWorktree}) on a new sub-branch named {subBranch} of the feature branch that the plan names (create the feature branch from origin/{integrationBranch} if it does not exist, merge your sub-branch back into it locally when the package is finished).",
    "workInWorktree": "Work in the worktree {cwd} and the branch the plan names for this package (never invent them).",
    "release": "Release of the branch of plan {plan} (worktree {cwd}): merge origin/{integrationBranch} (keep both sides of every conflict), run {compileCheck} until exit 0, run the check scripts for the changed files, push normally and register the branch with bzz ready (note 'Kompiliertest: Exit 0'; {buildFlowDoc} rules 3-5). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the release is finished.",
    "integrator": "You are the integrator. Follow {integratorInstructions} and {buildFlowDoc}: read the ready rows of the integration queue (bzz queue --project {project}), merge them, one Release build, check scripts, load test, push {integrationBranch}, deploy, set the rows to integrated (bzz queue upsert). Follow all rules in section 1 of AGENT-RULES.md.",
    "interruptedWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} was interrupted (laptop sleep, network or API problem, or its process vanished), not finished. Continue exactly where it stopped: the work already in the worktree is kept, what is already done stays done. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
    "unfinishedWork": "Plan: {plan}, package {pkg}. The previous work agent {previous} ended without a new commit on its branch, so the package is not finished. Read its report ({answerPath}) and the handoff in the plan's state file, continue exactly where it stopped, commit your work and do not repeat what is already done. Work in {cwd} (the main checkout is never edited: use your own worktree on a sub-branch of the plan's feature branch when code changes). Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished.",
    "lockQuestion": "{marker} - {tag}\n\nPackage {pkg} is locked: the checks repeatedly reported open items, also after the attempt with a higher class. Last check ({agent}): {text}\n\nQuestion: How should the package continue (other approach, change the requirement, check in the game)? Recommendation: {owner} decides after seeing the open items.",
}

# Settings every project gets; machine config.toml and project.toml overlay them (project over machine over these defaults).
DEFAULT_SETTINGS = {
    "owner": "Owner",
    "openMarker": "Open for Owner",
    "integratorSlots": 1,
    "roundMinutes": 5,
    "escalateAfter": 2,
    "progressVerifies": 3,   # a package whose open count does not fall over this many consecutive verifies is stopped (question to the owner)
    "planStallHours": 6,     # a plan with agents running but no integrated package for this many hours is stopped (question to the owner)
    "clockJumpSeconds": 120,   # a wall clock jump above this many seconds during an agent run counts as laptop sleep
    "retryWaitMinutes": [1, 2, 4, 8, 15],   # wait before the n-th retry of consecutive interrupted ends (API, connection); the last value repeats
    "rateLimitWaitMinutes": 15,   # wait after a rate limit when no reset time of the limited provider is known
    "minClass": {"verify": "medium", "integrate": "medium"},
    "bundle": {"maxPackages": 3},
    "contextLimitTokens": 300000,
    "integrationBranch": "main",
    "compileCheck": "",
    "buildFlowDoc": "",
    "integratorInstructions": "",
    "worktreePrefix": "wt",
    "planStandaloneRegex": "",
    "planSuffixRegex": "-?PLAN$",
    "buildProcessNames": [],
    "capabilityRules": [],
    "triage": True,
    "window": "HaiveQueen",
    "terminal": "auto",
    "brain": {"command": "claude", "model": "sonnet", "effort": "medium", "timeoutSeconds": 1500,
              "args": ["--output-format", "stream-json", "--verbose", "--model", "{brainModel}", "--permission-mode", "auto",
                       "--effort", "{brainEffort}", "--tools", "Read,Glob,Grep", "--add-dir", "{repo}",
                       "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config"]},
}
MACHINE_ONLY = ("providers", "slots", "buildJobs", "ramReserveMb", "weeklyLimitSources", "exportDir", "projects", "window")   # window: the Queen, the dashboard and all agents share one terminal window


def deep_merge(base: dict, over: dict) -> dict:
    """Return base overlaid with over (nested dicts are merged key by key, everything else is replaced)."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def build_settings(machine: dict, project_cfg: dict) -> dict:
    """Merge defaults, machine settings and project settings (project.toml); machine-only keys cannot be overridden by a project."""
    merged = deep_merge(DEFAULT_SETTINGS, {k: v for k, v in machine.items() if k not in ("projects",)})
    over = {k: v for k, v in project_cfg.items() if k not in MACHINE_ONLY}
    merged = deep_merge(merged, over)
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
    minimum = (settings.get("minClass") or {}).get(phase)
    if minimum:
        i = max(i, class_index(minimum))
    return CLASS_NAMES[i]


# ---- providers ----

def get_providers(settings: dict) -> list:
    """Return the provider entries as dicts (name, protocol, command, args, slots, use, capabilities, classes, limit, endpoint).

    A provider without `slots` gets the machine value `slots`. A wrong `use` or `protocol` raises ValueError.
    """
    out = []
    for p in settings.get("providers") or []:
        name = str(p.get("name") or "")
        if not name:
            raise ValueError("a provider entry has no name")
        use = str(p.get("use") or "preferred")
        if use not in USES:
            raise ValueError("provider %s: use must be preferred, fallback or capability-only, not '%s'" % (name, use))
        proto = str(p.get("protocol") or name)
        if proto not in PROTOCOLS:
            raise ValueError("provider %s: protocol must be claude or codex, not '%s'" % (name, proto))
        limit = p.get("limit")
        out.append({
            "name": name, "protocol": proto, "command": str(p.get("command") or proto),
            "args": [str(a) for a in (p.get("args") or [])],
            "slots": int(p.get("slots", settings.get("slots", 0)) or 0), "use": use,
            "capabilities": [str(c) for c in (p.get("capabilities") or [])],
            "classes": p.get("classes") or {},
            "limit": str(limit.get("source", "")) if isinstance(limit, dict) else str(limit or ""),
            "endpoint": p.get("endpoint"),
        })
    return out


def get_provider(providers: list, name: str):
    """Return the provider with that name or None."""
    for p in providers:
        if p["name"] == name:
            return p
    return None


def row_provider(row: dict) -> str:
    """Provider name of an agent row: the field provider, for old rows the field type."""
    return str(row.get("provider") or row.get("type") or "")


def provider_free(providers: list, rows: list) -> dict:
    """Free slots per provider: its slots minus the running or starting workers of it (integrator agents use no slot); never below 0."""
    free = {}
    for p in providers:
        used = sum(1 for r in rows if r.get("status") in ACTIVE and r.get("phase") != "integrate"
                   and row_provider(r) == p["name"])
        free[p["name"]] = max(0, p["slots"] - used)
    return free


# ---- weekly limits ----

def format_reset_time(epoch) -> str:
    """Convert epoch seconds to local HH:MM, or dd.mm. HH:MM when not today."""
    if not epoch:
        return ""
    d = datetime.fromtimestamp(int(epoch))
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%d.%m. %H:%M")


def save_claude_usage(conn, event: dict) -> None:
    """Remember the newest Claude usage windows (from a rate_limit_event of a headless claude run) in the global kv key usage-claude."""
    w = ((event or {}).get("rate_limit_info") or {}).get("unifiedWindows")
    if not w:
        return
    runtime.kv_set(conn, GLOBAL, "usage-claude", json.dumps(
        {"saved": datetime.now().isoformat(timespec="seconds"), "fiveHour": w.get("five_hour"), "sevenDay": w.get("seven_day")}))


def claude_usage(conn) -> dict:
    """Return {FiveHour, SevenDay} of Claude from the newest kv row usage-claude (any project); each is None, {Pct, Reset, At} or a reset marker."""
    res = {"FiveHour": None, "SevenDay": None}
    try:
        row = conn.execute("SELECT value FROM kv WHERE key='usage-claude' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row is None or not str(row[0]).strip():
            return res
        j = json.loads(row[0])
        now = time.time()
        for key, win in (("FiveHour", j.get("fiveHour")), ("SevenDay", j.get("sevenDay"))):
            if not win:
                continue
            if int(win.get("resetsAt", 0)) > now:
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
    base = Path(sessions_dir) if sessions_dir else Path.home() / ".codex" / "sessions"
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


def provider_limit(provider: dict, conn=None, limits=None) -> dict:
    """Weekly limit of a provider as {FreePct, Hours}: 100 minus the used percent and the hours until the reset.

    No limit source, no reading or an unknown value counts as 100 % free with a reset in 168 h. `limits` (name -> {usedPercent, resetHours})
    or the file in BZZ_PROVIDER_LIMITS_FILE replaces the real sources (tests); with a test runtime (BZZ_RUNTIME_OVERRIDE) and neither
    nothing is read from this PC.
    """
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
        u = (claude_usage(conn)["SevenDay"] if provider["limit"] == "claude" and conn is not None
             else codex_usage()["SevenDay"] if provider["limit"] == "codex" else None)
        if u and u.get("Pct") is not None:
            used = u["Pct"]
            if u.get("At"):
                hours = (float(u["At"]) - time.time()) / 3600
    if used is None:
        return {"FreePct": 100.0, "Hours": 168.0}
    return {"FreePct": max(0.0, 100.0 - float(used)), "Hours": float(hours) if hours is not None else 168.0}


# ---- capabilities and selection ----

DEFAULT_CAPABILITY_RULE = {"capability": "images", "patterns": [
    r"(?i)\b(models?|textures?|materials?|particles?|icons?|clips?|sounds?)\b", r"(?i)\b(Blender|assets?|graphics?)\b"]}


def package_needs(settings: dict, content: str, capability: str = "") -> list:
    """Capabilities a package needs: those named in its plan header (comma separated) as they stand, else the capabilityRules matching its title."""
    declared = [c.strip() for c in str(capability or "").split(",") if c.strip()]
    if declared:
        return declared
    rules = settings.get("capabilityRules") or [DEFAULT_CAPABILITY_RULE]
    need = []
    for r in rules:
        if all(re.search(pat, content or "") for pat in r.get("patterns", [])) and r["capability"] not in need:
            need.append(str(r["capability"]))
    return need


def capability_available(providers: list, needs: list) -> bool:
    """True when at least one provider with ALL the capabilities has slots above 0 (slots 0 = off for everything)."""
    return any(p["slots"] > 0 and all(n in p["capabilities"] for n in needs) for p in providers)


def select_provider(providers: list, cls: str, needs, free: dict, avoid: str = "", prefer: str = "",
                    protocol: str = "", conn=None, limits=None):
    """Return the provider that gets a task of this class, or None.

    Candidates have slots above 0 and a free slot, the class in their entry, all needed capabilities (a package that needs one goes
    only to providers that have it and the weekly limit does not matter then); `capability-only` providers only get packages that need
    a capability. Without a need, providers at their weekly limit drop out. `preferred` and `capability-only` come first, `fallback`
    only when none of them has a free slot. The highest (100 - used percent) / hours to reset wins, then list order. `prefer` keeps a
    follow-up with an earlier provider when it is a candidate, `avoid` (verify) prefers another provider, `protocol` limits to claude or codex.
    """
    cname = CLASS_NAMES[class_index(cls)]
    needs = [n for n in (needs or []) if n]
    cand = []
    for idx, p in enumerate(providers, 1):
        if p["slots"] <= 0 or int(free.get(p["name"], 0)) <= 0:
            continue
        if protocol and p["protocol"] != protocol:
            continue
        model = (p["classes"] or {}).get(cname)
        if not (model and model.get("model")):
            continue
        if any(n not in p["capabilities"] for n in needs):
            continue
        if p["use"] == "capability-only" and not needs:
            continue
        lim = provider_limit(p, conn, limits)
        if not needs and lim["FreePct"] <= 0:
            continue
        cand.append((p, idx, lim["FreePct"] / max(0.1, lim["Hours"])))
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
    return sorted(pool, key=lambda c: (-c[2], c[1]))[0][0]


def endpoint_args(provider: dict) -> list:
    """Extra arguments of a provider with an endpoint: a model_provider of the codex CLI with the base URL (-c key=value)."""
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


def model_long(protocol: str, model: str, effort: str) -> str:
    """Long model text for pane titles: 'Claude Sonnet 5.5 medium' / 'Codex GPT-6.1-Sol medium'."""
    if protocol == "codex":
        m = re.match(r"^gpt-(.+)$", model or "")
        if m:
            return "Codex GPT-" + "-".join(_title(x) for x in m.group(1).split("-")) + " " + effort
        return "Codex %s %s" % (model, effort)
    nm, v = model, ""
    for key, label in (("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")):
        if key in (model or ""):
            nm, v = label, "5.5"
    m = re.search(r"(\d+)[-.](\d+)", model or "")
    if m:
        v = m.group(1) + "." + m.group(2)
    return ("Claude %s %s" % (nm, v)).replace("  ", " ").strip() + " " + effort


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
        self.providers = get_providers(settings)

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
        """Write a history event and a line into the ORCH-EVENTS.log mirror (what the orchestrator pane shows)."""
        runtime.append_event(self.conn, self.name, text, mirror="events", error=error)

    def agents(self, scope: str = "active") -> list:
        """Agent rows of the project (scope active, archive or all)."""
        return runtime.list_agents(self.conn, self.name, scope)

    def update_agent(self, name: str, fields: dict) -> dict:
        """Create or update the active row of an agent."""
        return runtime.upsert_agent(self.conn, self.name, name, fields)

    def packages(self, plan: str) -> list:
        """Package table of a plan: dicts with id, cls, content (title), depends, depends_all, phase, capability. Dependency 'all' means every other package."""
        res = []
        for p in runtime.list_packages(self.conn, self.name, plan):
            deps = [str(d) for d in p["depends_on"] if d and not re.match(r"(?i)^all(e)?$", str(d))]
            cls = str(p["class"] or "")
            res.append({"id": str(p["id"]), "cls": cls.lower() if re.match(r"(?i)^(light|medium|heavy)$", cls) else "medium",
                        "content": str(p["title"] or ""), "depends": deps, "phase": str(p["phase"] or ""),
                        "depends_all": any(re.match(r"(?i)^all(e)?$", str(d)) for d in p["depends_on"]),
                        "capability": str(p["capability"] or "")})
        for r in res:
            if r["depends_all"]:
                r["depends"] = [x["id"] for x in res if x["id"] != r["id"]]
        return res


def load_projects(conn, machine: dict) -> list:
    """Return a ProjectEnv for every registered project that has a workspace folder (project.toml is read fresh each time)."""
    envs = []
    for row in db.rows(conn, "SELECT * FROM projects ORDER BY name"):
        ws = row["workspace"]
        if not ws or not Path(ws).is_dir():
            continue
        pcfg = config.load_project(ws)
        settings = build_settings(machine, pcfg)
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
    """ISO time of the oldest package of a READY or IN_PROGRESS plan that waits for an agent ('' when none waits).

    A package waits when it is not done and no active agent works on it. An agent that exists for it clears its marker.
    """
    active = {(norm_plan(a.get("plan_path")), str(a.get("package"))) for a in env.agents() if a.get("status") in ACTIVE}
    oldest = ""
    for plan in runtime.list_plans(env.conn, env.name):
        if plan["status"] not in ("READY", "IN_PROGRESS"):
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
