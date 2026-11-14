"""The slim set of project settings: what a project.toml may hold, the values HaiveQueen derives itself and the migration of old files.

A project keeps a few fields (repository, name, integration branch, compile check and the Advanced ones). Everything else is either taken
from General (owner name, language, round length, escalation, Queen class, Agents per Queen), from the providers (context limit, tools of
Claude agents, allowed commands), from the repository (remote, default branch, .gitignore) or dropped. `migrate_all` rewrites every known
project.toml once, keeps the old file as project.toml.bak and lists every removed key with its old value in removed-settings.md beside it.
"""
import copy
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import config

DEFAULT_CLAUDE_TOOLS = config.DEFAULT_CLAUDE_TOOLS
MIGRATION_FILE = "removed-settings.md"

# Keys of a project.toml that stay. Fields of the project page (P-1, P-2) and the internal keys without a field of their own.
KEPT_KEYS = (
    "name", "codeRepo", "integrationBranch", "compileCheck", "buildCommand", "deployCommand", "serialIntegrationBuild",
    "plans", "worktreeLocation", "deleteFinishedWorktrees", "deleteIntegratedBranches",
    "nextVersion", "onEvent", "colour", "priority", "buildProcessNames", "ownerTopics",
    "wayOfWorking", "mergeMode", "checkMode", "buildMode", "deployMode", "releaseMode",
    "checkEnabled", "buildEnabled", "deployEnabled", "releaseEnabled", "releaseFolder", "releasePostfix",
    "ignoredBuildOutput", "worktreePrefix", "upstreamRepo", "upstreamRules", "rulesOff",
    "prompts", "readyCheckClass", "readyCheckTimeoutSec", "agentStallSeconds", "rateLimitWaitMinutes", "retryWaitMinutes", "clockJumpSeconds",
)
GENERAL_NOTE = "the General value applies"
NO_EFFECT = "had no effect and is dropped"
REMOVED_NOTES = {
    "language": GENERAL_NOTE, "escalateAfter": GENERAL_NOTE, "roundMinutes": GENERAL_NOTE,
    "owner": "now the General field Owner name",
    "protectedWorktrees": "the cleanup removes only worktrees of branches it integrated",
    "claudeTools": "now the setting Tools available to Claude agents of each Claude provider",
    "allowedTools": "the push permission of the integrating Queen is given by HaiveQueen; other entries moved to the Claude providers",
    "compileCheckEnabled": "an empty compile check command means no compile check",
    "buildEnabled": "an empty Build command means no build; the Orchestrator runs the Build command after the check",
    "deployEnabled": "an empty Deploy command means no deploy; the Orchestrator runs the Deploy command after the push",
    "buildFlowDoc": "no instruction text: the Orchestrator runs the Build command itself",
    "integratorInstructions": "no instruction text: the Orchestrator integrates, no Queen does",
    "queenClass": "now the General field Queen class", "beesPerQueen": "now the General field Agents per Queen",
    "contextLimitTokens": "now the field Context limit of each provider; this project value does not apply any more",
    "capabilityRules": "the built-in capability rule applies", "preferredProvider": "dropped, the built-in rules apply",
    "ownerCheckText": "the default text applies (the decision is taken by buttons)",
    "views": "the dashboard and bzz export replace the generated overviews",
    "integrationStateFile": "the queue is named integration queue",
    "planGlobs": "merged into the field Plans", "planExclude": "merged into the field Plans",
    "repo": "now the key codeRepo (Repository)",
    "minClass": NO_EFFECT, "bundle": NO_EFFECT, "openMarker": NO_EFFECT, "integratorSlots": NO_EFFECT, "paneCloseDelaySeconds": NO_EFFECT,
    "terminalProfile": NO_EFFECT, "ruleSources": "agents get the checked-in project rules of the repository only", "phaseRules": "agents get the checked-in project rules of the repository only",
    "planStandaloneRegex": "the plan tag comes from the plan file name", "planSuffixRegex": "the plan tag comes from the plan file name",
}
UNKNOWN_NOTE = "not used"
# Keys of a project that General or the providers now decide; build_settings ignores them in a project file (the migration has moved their effect).
DROPPED_FROM_PROJECT = ("language", "escalateAfter", "roundMinutes", "owner", "protectedWorktrees", "claudeTools", "allowedTools",
                        "compileCheckEnabled", "buildFlowDoc", "integratorInstructions", "queenClass", "beesPerQueen", "contextLimitTokens", "capabilityRules", "preferredProvider", "ownerCheckText",
                        "views", "integrationStateFile", "planStandaloneRegex", "planSuffixRegex", "ruleSources", "phaseRules")
PUSH_FORMS = ("Bash(git push {r} HEAD:{b})", "Bash(git -C * push {r} HEAD:{b})", "PowerShell(git push {r} HEAD:{b})", "PowerShell(git -C * push {r} HEAD:{b})")

STEP_FIELDS = {"check": "compileCheck", "build": "buildCommand", "deploy": "deployCommand", "release": "releaseFolder"}
STEPS = ("merge", *STEP_FIELDS)


def working_settings(data: dict) -> dict:
    """Return the working-way settings, including defaults for legacy project files."""
    result = copy.deepcopy(data)
    legacy = "wayOfWorking" not in data
    result.setdefault("wayOfWorking", "local")
    for step in STEPS:
        result.setdefault(step + "Mode", "local")
    for step, field in STEP_FIELDS.items():
        if step == "release":
            enabled = bool(data.get("publishReleases"))
        else:
            enabled = bool(str(data.get(field) or "").strip())
            if legacy and step == "check" and data.get("compileCheckEnabled") is False:
                enabled = False
        if legacy:
            result[step + "Enabled"] = enabled
        else:
            result.setdefault(step + "Enabled", False)
    result.setdefault("releaseFolder", "")
    result.setdefault("releasePostfix", "dev")
    return result


def select_working_way(data: dict, way: str) -> dict:
    """Select a preset, or retain the individual modes for custom."""
    if way not in ("local", "git", "custom"):
        raise ValueError("Unknown way of working")
    result = working_settings(data)
    result["wayOfWorking"] = way
    if way != "custom":
        for step in STEPS:
            result[step + "Mode"] = way
    return result


def select_step_mode(data: dict, step: str, mode: str) -> dict:
    """Change one mode and keep the overall choice consistent."""
    if step not in STEPS or mode not in ("local", "git"):
        raise ValueError("Unknown step or mode")
    result = working_settings(data)
    result[step + "Mode"] = mode
    modes = {result[name + "Mode"] for name in STEPS}
    result["wayOfWorking"] = modes.pop() if len(modes) == 1 else "custom"
    return result


def validate_working_settings(data: dict) -> list:
    """Return validation errors for the working-way model."""
    s = working_settings(data)
    errors = []
    if s["wayOfWorking"] not in ("local", "git", "custom"):
        errors.append("Unknown way of working")
    for step in STEPS:
        if s[step + "Mode"] not in ("local", "git"):
            errors.append("Unknown mode for " + step)
    modes = {s[step + "Mode"] for step in STEPS}
    if s["wayOfWorking"] in ("local", "git") and modes != {s["wayOfWorking"]}:
        errors.append("Working way does not match step modes")
    for step, field in STEP_FIELDS.items():
        if not isinstance(s[step + "Enabled"], bool):
            errors.append(step + " switch must be on or off")
        elif s[step + "Enabled"] and s[step + "Mode"] == "local" and not str(s.get(field) or "").strip():
            errors.append(field + " is required")
    if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", str(s["releasePostfix"])):
        errors.append("Invalid release postfix")
    return errors


# ---------------------------------------------------------------- git facts

def _git(repo, *args):
    """Output of a git command in `repo`, or None when it fails."""
    try:
        res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def remotes_of(repo) -> list:
    """The names of the Git remotes of a repository ([] when there are none or it is no repository)."""
    out = _git(repo, "remote") if repo else None
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def rule_remote(remotes) -> str:
    """The remote HaiveQueen uses without a setting: origin when it exists, else the only remote not named upstream, else '' (the owner has to choose).
    A repository without remotes gives origin."""
    if not remotes or "origin" in remotes:
        return "origin"
    others = [r for r in remotes if r != "upstream"]
    return others[0] if len(others) == 1 else ""


def derive_remote(repo, explicit="") -> tuple:
    """(remote name, choice needed): a set remote that differs from the rule is kept, else the rule; '' and True when the owner has to choose."""
    rule = rule_remote(remotes_of(repo))
    explicit = str(explicit or "").strip()
    if explicit and explicit != rule:
        return explicit, False
    return (rule, False) if rule else ("", True)


def default_branch(repo, remote="origin") -> str:
    """The default branch of the repository's remote (from the last fetch), else main."""
    out = _git(repo, "symbolic-ref", "--short", "refs/remotes/%s/HEAD" % remote) if repo else None
    name = (out or "").strip()
    prefix = remote + "/"
    return name[len(prefix):] if name.startswith(prefix) and len(name) > len(prefix) else "main"


def default_name(repo) -> str:
    """The default name of a new project: the folder name of its repository."""
    return Path(str(repo)).name if repo else ""


def apply_derived(settings: dict, project_cfg: dict, workspace=None) -> dict:
    """Fill the derived values (remote, integration branch) of merged settings from the repository of the project; changes and returns `settings`.
    Without a repository folder nothing is derived."""
    repo = project_cfg.get("codeRepo") or project_cfg.get("repo") or workspace
    if not repo or not Path(str(repo)).is_dir():
        return settings
    remote, needed = derive_remote(repo, project_cfg.get("remoteName"))
    settings["remoteName"] = remote or "origin"
    settings["remoteChoiceNeeded"] = needed
    if not project_cfg.get("integrationBranch"):
        settings["integrationBranch"] = default_branch(repo, remote or "origin")
    return settings


def remote_wait_reason(settings: dict) -> str:
    """Why the integration of a project waits because its remote is not chosen, or ''."""
    if settings.get("remoteChoiceNeeded"):
        return "the project has several Git remotes and none named origin: choose the Remote in the project settings"
    return ""


# ---------------------------------------------------------------- plans field

def plans_to_globs(project: dict) -> tuple:
    """(planGlobs, planExclude) of a project dict: the field `plans` (a line starting with ! excludes), else the old keys, else the default."""
    plans = project.get("plans")
    if isinstance(plans, str):
        plans = plans.splitlines()
    if plans:
        lines = [str(p).strip() for p in plans if str(p).strip()]
        return [p for p in lines if not p.startswith("!")], [p[1:].strip() for p in lines if p.startswith("!")]
    return list(project.get("planGlobs") or config.DEFAULT_PROJECT["planGlobs"]), list(project.get("planExclude") or [])


def globs_to_plans(globs, exclude) -> list:
    """The `plans` field value for plan patterns and exclusions."""
    return [str(g) for g in globs or []] + ["!" + str(e) for e in exclude or []]


# ---------------------------------------------------------------- migration of one project file

def is_claude(provider: dict) -> bool:
    """True for a provider entry that runs the Claude Code CLI."""
    return str(provider.get("protocol") or provider.get("name") or "") == "claude"


def split_tools(text) -> list:
    """A comma separated tool list as a list."""
    if isinstance(text, (list, tuple)):
        return [str(t).strip() for t in text if str(t).strip()]
    return [t.strip() for t in str(text or "").split(",") if t.strip()]


def merge_tools(base, extra) -> str:
    """The comma separated union of two tool lists, base first, no duplicates."""
    out = []
    for tool in split_tools(base) + split_tools(extra):
        if tool not in out:
            out.append(tool)
    return ",".join(out)


def push_entries(remote, branch) -> list:
    """The permission entries HaiveQueen gives the integrating Queen for pushing to the integration branch."""
    return [form.format(r=remote, b=branch) for form in PUSH_FORMS]


def migrate_data(data: dict, workspace, general: dict) -> tuple:
    """Convert the dict of an old project.toml. Returns (new dict, removed list of (key, old value, note)); `general` (the dict of config.toml) is changed:
    Owner name, Queen class, Agents per Queen, Claude tools and extra allowed commands move there. A file already in the new form comes back equal."""
    new, removed = {}, []
    old = copy.deepcopy(data)
    if old.get("repo") and not old.get("codeRepo"):
        old["codeRepo"] = old["repo"]
        removed.append(("repo", old["repo"], REMOVED_NOTES["repo"]))
        old.pop("repo")
    elif "repo" in old:
        removed.append(("repo", old.pop("repo"), REMOVED_NOTES["repo"]))
    repo = old.get("codeRepo") or str(workspace)
    for key, value in old.items():
        if key in KEPT_KEYS and key not in ("plans", "prompts"):
            new[key] = value
    if not new.get("name"):
        new["name"] = Path(str(workspace)).name
    # plans: planGlobs and planExclude merge into one field
    if "plans" in old or "planGlobs" in old or "planExclude" in old:
        globs, exclude = plans_to_globs(old)
        for key in ("planGlobs", "planExclude"):
            if key in old:
                removed.append((key, old[key], REMOVED_NOTES[key]))
        new["plans"] = old["plans"] if "plans" in old else globs_to_plans(globs, exclude)
    prompts = old.get("prompts")
    if isinstance(prompts, dict):
        if "integrator" in prompts:
            removed.append(("prompts.integrator", prompts["integrator"], "the built-in integration steps apply"))
        rest = {k: v for k, v in prompts.items() if k != "integrator"}
        if rest:
            new["prompts"] = rest
    # remote: kept only when it differs from what HaiveQueen derives
    if "remoteName" in old:
        remote, _needed = derive_remote(repo, "")
        if str(old["remoteName"]).strip() and str(old["remoteName"]).strip() != remote:
            new["remoteName"] = old["remoteName"]
        else:
            removed.append(("remoteName", old["remoteName"], "derived from the Git remotes of the repository"))
    # Preserve commands and turn the legacy implicit switches into explicit settings.
    working = working_settings(old)
    for key in ("wayOfWorking", *(step + "Mode" for step in STEPS),
                *(step + "Enabled" for step in STEP_FIELDS), "releaseFolder", "releasePostfix"):
        new[key] = working[key]
    if "compileCheckEnabled" in old:
        removed.append(("compileCheckEnabled", old["compileCheckEnabled"], "moved to checkEnabled"))
    if "publishReleases" in old:
        removed.append(("publishReleases", old["publishReleases"], "moved to releaseEnabled"))
    if "wayOfWorking" not in old:
        for key in ("buildEnabled", "deployEnabled"):
            if key in old:
                removed.append((key, old[key], "legacy switch replaced by the command-derived switch"))
    # settings that move to General and the providers
    if old.get("owner") and not str(general.get("owner") or "").strip():
        general["owner"] = old["owner"]
    for key in ("queenClass", "beesPerQueen"):
        if key in old and general.get(key) in (None, ""):
            general[key] = old[key]
    if old.get("claudeTools"):
        for provider in general.get("providers") or []:
            if is_claude(provider):
                provider["claudeTools"] = merge_tools(provider.get("claudeTools") or general.get("claudeTools") or DEFAULT_CLAUDE_TOOLS, old["claudeTools"])
    extra = extra_allowed(old.get("allowedTools"), new.get("integrationBranch") or old.get("integrationBranch") or "main", old.get("remoteName"))
    if extra:
        from .settings import split_args, join_args
        for provider in general.get("providers") or []:
            if is_claude(provider):
                rest, tools, position = split_args(provider.get("args"))
                provider["args"] = join_args(rest, tools + [t for t in extra if t not in tools], position)
    known = set(KEPT_KEYS) | set(REMOVED_NOTES) | {"repo", "remoteName", "prompts", "publishReleases"}
    handled = {r[0] for r in removed}
    for key, value in old.items():
        if key in REMOVED_NOTES and key not in KEPT_KEYS and key not in handled and key not in ("planGlobs", "planExclude"):
            removed.append((key, value, REMOVED_NOTES[key]))
        elif key not in known:
            removed.append((key, value, UNKNOWN_NOTE))
    return new, removed


def extra_allowed(entries, branch, remote) -> list:
    """The entries of a project's allowedTools that are not the push permission HaiveQueen gives the integrating Queen itself."""
    given = set()
    for r in {str(remote or "origin"), "origin"}:
        given.update(push_entries(r, branch))
    return [str(e) for e in entries or [] if str(e) not in given]


def removed_text(removed, stamp) -> str:
    """One dated section of removed-settings.md."""
    lines = ["## %s" % stamp, ""]
    for key, value, note in removed:
        lines.append("- `%s` = %r - %s." % (key, value, note))
    return "\n".join(lines) + "\n"


def write_removed(folder: Path, removed, stamp) -> None:
    """Append a dated section to removed-settings.md beside project.toml (an existing file keeps everything it has)."""
    path = Path(folder) / MIGRATION_FILE
    old = path.read_text(encoding="utf-8") if path.is_file() else "# Removed project settings\n\nValues HaiveQueen took out of this project's settings, with where their effect is now.\n"
    path.write_text(old.rstrip("\n") + "\n\n" + removed_text(removed, stamp), encoding="utf-8")


def fill_providers(general: dict) -> None:
    """Give each provider the Context limit (config.toml value, capped by the window of a Local LLM) and, for a Claude provider, the tools; fold the machine-wide values."""
    from . import servers
    machine_limit = int(general.get("contextLimitTokens") or 300000)
    machine_tools = general.get("claudeTools")
    for provider in general.get("providers") or []:
        if not provider.get("contextLimit"):
            limit = machine_limit
            window = servers.window_of(servers.server_of(provider) or {})
            provider["contextLimit"] = min(limit, window) if window else limit
        if is_claude(provider) and not provider.get("claudeTools"):
            provider["claudeTools"] = machine_tools or DEFAULT_CLAUDE_TOOLS
    general.pop("contextLimitTokens", None)
    general.pop("claudeTools", None)


def project_file(workspace) -> Path:
    """Path of the project.toml of a workspace."""
    return Path(workspace) / ".haivequeen" / "project.toml"


def store_legacy_tags(conn, workspace, data) -> None:
    """Before the plan name rules leave the project file: store the tag every plan with a Hive has today, so its branches and worktrees keep their names."""
    from . import routine, runtime
    row = conn.execute("SELECT name FROM projects WHERE workspace=?", (str(workspace),)).fetchone()
    if not row:
        return
    globs, _exclude = plans_to_globs(data)
    for (plan,) in conn.execute("SELECT plan_path FROM hives WHERE project=?", (row[0],)).fetchall():
        if not runtime.kv_get(conn, row[0], routine.TAG_KEY + plan):
            runtime.kv_set(conn, row[0], routine.TAG_KEY + plan, routine.legacy_tag(plan, globs, data.get("planStandaloneRegex") or "", data.get("planSuffixRegex") or ""))


def migrate_all(workspaces=None, now=None, conn=None) -> list:
    """Migrate every project.toml HaiveQueen knows (the projects of config.toml, or the given workspace folders); returns the migrated files.

    A file already in the new form is not touched. For each changed file the old one is kept as project.toml.bak and removed-settings.md gets a
    dated section; config.toml changes (Owner name, Queen class, Agents per Queen, provider Context limit and tools) are written first with config.toml.bak.
    """
    from .settings import save_file
    cfg_path = config.config_dir() / "config.toml"
    raw = config._load_toml(cfg_path)
    general = copy.deepcopy(raw)
    if workspaces is None:
        workspaces = [e.get("workspace") for e in raw.get("projects") or [] if e.get("workspace")]
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    plan = []
    legacy_config = "contextLimitTokens" in general or "claudeTools" in general
    for ws in workspaces:
        path = project_file(ws)
        if not path.is_file():
            continue
        data = config._load_toml(path)
        new, removed = migrate_data(data, ws, general)
        if new != data or removed:
            plan.append((ws, path, data, new, removed))
    if plan or legacy_config:
        fill_providers(general)
    if general != raw:
        save_file(cfg_path, general)
    done = []
    for ws, path, data, new, removed in plan:
        if conn is not None:
            store_legacy_tags(conn, ws, data)
        if removed:
            write_removed(path.parent, removed, stamp)
        save_file(path, new)
        done.append(path)
    return done
