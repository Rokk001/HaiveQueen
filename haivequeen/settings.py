"""Shared settings validation and comment-preserving TOML persistence."""
from . import filetxn
import json
import math
import os
import re
import tomllib
from collections import Counter
from pathlib import Path
import tomlkit

from .i18n import N_, tr

ALLOWED_FLAG = "--allowedTools"   # the Claude Code argument whose value is the comma separated list of allowed tools
CHOICES = {"protocol": ("claude", "codex", "opencode", "kilo"), "outputFormat": ("claude", "codex", "opencode"), "queenClass": ("light", "medium", "heavy"), "diagnosisClass": ("light", "medium", "heavy"), "use": ("preferred", "fallback"), "beeMode": ("subagent", "process")}   # fields that offer a fixed choice
CAPABILITIES = ("Coding", "Queen", "Build", "Deploy", "Image generation", "3D modelling", "Audio generation", "Video generation", "Vision", "Browser automation", "Web research", "GPU workloads", "Confidential data")
CLASSES = ("light", "medium", "heavy")
# The block "Build" of the General page: exactly these three fields, each with one sentence of explanation (key in the buildJobs table, label, explanation).
BUILD_TEXT_WIDTH = 470   # pixels of an explanation in the block "Build": what stays free beside the field in the window size below, in every language
BUILD_FIELDS = (
    ("cores", N_("Total CPU budget for builds and checks"), N_("How many of your processor cores builds may use in total; a build uses fewer when the free RAM is short.")),
    ("parallel", N_("Parallel builds"), N_("How many targets the build command builds at the same time (the number after --parallel); each target gets the cores divided by this number.")),
    ("minFreeRamGB", N_("Minimum free RAM before a build"), N_("A build only starts while at least this many GB of RAM are free, otherwise it waits.")),
)


# The settings of the Apiary page, each with the value that applies without an entry (for an Apiary from before the switches deploy stays on, see apiary_settings).
APIARY_DEFAULTS = (("ownerTopics", []), ("ownerCheckText", ""),
                   ("integrationBranch", "main"), ("remoteName", "origin"), ("worktreeLocation", ""), ("deleteFinishedWorktrees", True), ("deleteIntegratedBranches", False),
                   ("publishReleases", False), ("nextVersion", ""))
RELEASE_KEYS = ("publishReleases", "nextVersion")   # offered only for an Apiary whose remote is on GitHub
SETTING_LABELS = {
    "ownerTopics": N_("Topics that always go to the owner (one per line)"), "ownerCheckText": N_("Owner-check question (empty: default text)"),
    "integrationBranch": N_("Integration branch"), "remoteName": N_("Remote"), "worktreeLocation": N_("Worktree location (empty: beside the repository)"),
    "deleteFinishedWorktrees": N_("Delete finished worktrees automatically"), "deleteIntegratedBranches": N_("Delete integrated branches (local and remote)"),
    "publishReleases": N_("Publish release at integration"), "nextVersion": N_("Next version (empty: count the last digit up)"),
    # Owner decision 741 (3): every field of the Apiary page has an understandable label; the internal key names stay in project.toml only.
    "name": N_("Name"), "repository": N_("Repository"), "plansFolder": N_("Plans folder (empty: the repository)"), "plans": N_("Plan files (patterns, one per line)"),
    "upstreamRules": N_("Use the rules of the upstream repository"),
    "colour": N_("Colour"), "priority": N_("Priority"), "owner": N_("Owner name"), "language": N_("Language of owner texts (empty: General language)"), "escalateAfter": N_("Failed attempts before a stronger model (empty: General value)"), "allowedTools": N_("Commands allowed without asking"),
    "planGlobs": N_("Plan files (patterns, one per line)"), "planExclude": N_("Files that are not plans (patterns, one per line)"),
    "ignoredBuildOutput": N_("Build output to ignore (one per line)"), "buildProcessNames": N_("Build program names (one per line)"),
    "compileCheck": N_("Compile check command (empty: no compile check)"), "buildCommand": N_("Build command (empty: no build)"),
    "deployCommand": N_("Deploy command (empty: no deploy)"), "claudeTools": N_("Tools available to Claude agents"), "protectedWorktrees": N_("Protected worktrees (one per line)"),
    "onEvent": N_("Command after each event"), "onEvent.command": N_("Command"), "onEvent.workDir": N_("Working folder"), "onEvent.timeoutSeconds": N_("Time limit (seconds)"),
    "views": N_("Generated overviews"), "views.queue": N_("Queue overview file"), "views.questions": N_("Open questions file"), "views.decisions": N_("Decisions file"),
    "prompts": N_("Own prompt texts"), "prompts.integrator": N_("Instructions of the integrating Queen"),
}
# One line of explanation per field of the Apiary page (owner decision 741 (3)).
SETTING_HELP = {
    "name": N_("The name of the Project in HaiveQueen; new worktree names start with it."),
    "repository": N_("The Git repository of the code; releases, version, branches and worktrees always use it."),
    "plansFolder": N_("The folder that holds the plans and the settings of the Project; empty: the repository."),
    "plans": N_("Where the plans are found, relative to the plans folder; a line starting with ! excludes files."),
    "colour": N_("The colour of the Project in the main window."), "priority": N_("Projects with a higher number get free places first."),
    "owner": N_("How the Queens and Agents call you in their texts."), "language": N_("The language of the texts HaiveQueen writes to you for this Project."),
    "ownerTopics": N_("Questions about these topics are never answered automatically."), "ownerCheckText": N_("The question HaiveQueen asks you when a plan is integrated."),
    "integrationBranch": N_("The branch finished plans are merged into; empty: the default branch of the remote, else main."),
    "remoteName": N_("The Git remote HaiveQueen pushes to and fetches from; choose it because the repository has several and none is named origin."), "worktreeLocation": N_("The folder that holds the worktrees of the Agents; a relative path counts from the repository."),
    "deleteFinishedWorktrees": N_("Remove a worktree once its work is integrated and nothing is lost."),
    "deleteIntegratedBranches": N_("Remove plan branches once they are integrated and nothing is lost."),
    "publishReleases": N_("Publish a GitHub release of the repository at each integration."), "nextVersion": N_("The version of the next release, for example 2.1.0."),
    "planGlobs": N_("Where the plans are found, relative to the folder with the settings."), "planExclude": N_("Files matching a plan pattern that are not plans."),
    "ignoredBuildOutput": N_("Files the build writes that do not count as changes."), "buildProcessNames": N_("Running programs that show a build is still busy."),
    "compileCheck": N_("The command that compiles the code and runs the tests before integration; empty: no compile check."),
    "buildCommand": N_("The command that builds the product after the check, run in the worktree of the plan; empty: no build."),
    "deployCommand": N_("The command that deploys the product after the push, run in the worktree of the plan; empty: no deploy."), "claudeTools": N_("The tools available to a Claude agent, separated by commas."),
    "protectedWorktrees": N_("Worktrees that are never removed automatically."),
    "onEvent.command": N_("A program started after each event, for example to sync files."), "onEvent.workDir": N_("The folder the command runs in."),
    "onEvent.timeoutSeconds": N_("The command is stopped after this many seconds."),
    "upstreamRules": N_("Reads AGENTS.md and CLAUDE.md of the Git remote named upstream (last fetch) or of the upstream folder."), "views.queue": N_("A Markdown file that shows the queue."),
    "views.questions": N_("A Markdown file that shows the open questions."), "views.decisions": N_("A Markdown file that shows the decisions."),
    "prompts.integrator": N_("Replaces the built-in instructions of the Queen that integrates a plan."),
}
GENERAL_LABELS = {"language": N_("Interface language"), "slots": N_("Agent slots"), "roundMinutes": N_("Round length (minutes)"), "escalateAfter": N_("Failed attempts before a stronger model"),
                  "owner": N_("Owner name"), "queenClass": N_("Queen class"), "diagnosisClass": N_("Question / diagnosis agent class"), "beesPerQueen": N_("Agents per Queen"),
                  "maxConcurrentCompileChecks": N_("Max concurrent compile checks")}
GENERAL_HELP = {"language": N_("The language of the HaiveQueen interface."), "slots": N_("The number of agents that may run at once."), "roundMinutes": N_("The time between coordinator rounds in minutes."), "escalateAfter": N_("After this many failed attempts of an Agent, HaiveQueen uses the next stronger model class."),
                "owner": N_("How the Queens and Agents call you in their texts."), "queenClass": N_("The model class of the Queens (light, medium or heavy)."),
                "diagnosisClass": N_("The class used to review technical questions; model and effort come from the provider settings for this class."),
                "beesPerQueen": N_("How many Agents a Queen may have running at the same time."),
                "maxConcurrentCompileChecks": N_("The most compile checks that run at the same time on this computer, across all projects and Queens, also for several plans of one project.")}
SETTING_HELP.update({"escalateAfter": N_("Overrides the General value for this Project."), "allowedTools": N_("Commands agents of this Project may run without asking.")})
OTHER_HELP = N_("A further setting of this Project, as written in its settings file.")   # the explanation of a setting the page has no own text for
GENERAL_OTHER_HELP = N_("A further HaiveQueen setting, as written in config.toml.")   # the same on the General page: these keys belong to HaiveQueen, not to a Project
ADVANCED_LABELS = {"hives": N_("Plans")}   # labels of keys in General -> Advanced (the key in config.toml stays the same)
# Owner decision 741: the main part of the Apiary page, the collapsed section Advanced and the keys the page never shows as fields of their own.
MAIN_KEYS = ("colour", "priority", "owner", "language", "escalateAfter") + tuple(key for key, _default in APIARY_DEFAULTS)
ADVANCED_DEFAULTS = (("planGlobs", ["docs/plans/*.md"]), ("planExclude", []), ("ignoredBuildOutput", []),
                     ("buildProcessNames", []), ("compileCheck", ""), ("claudeTools", ""), ("allowedTools", []))
HIDDEN_KEYS = ("name", "workspace", "preferredProvider", "codeRepo", "repo", "worktreePrefix", "roundMinutes")   # name and folders are the fields Name, Repository and Plans folder; the prefix follows the name
# Owner decision 742: a field is as wide as its kind of value needs, never the full window width.
SHORT_WIDTH, PATH_WIDTH, LABEL_WIDTH = 20, 60, 34   # characters: short values, paths and commands (they scroll inside), the label column
SHORT_KEYS = frozenset(("name", "colour", "color", "priority", "integrationBranch", "remoteName", "nextVersion", "worktreePrefix", "owner", "language", "model", "effort",
                        "slots", "protocol", "outputFormat", "use", "beeMode", "queenClass", "diagnosisClass", "source", "hives", "roundMinutes", "timeoutSeconds", "checkHours", "version", "branch"))


def field_width(key, value) -> int:
    """The width in characters of the line editor of `key`: short for numbers, names, branches, remotes, priorities, colours and versions, else that of a path."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return SHORT_WIDTH
    return SHORT_WIDTH if key in SHORT_KEYS else PATH_WIDTH


def readable(key) -> str:
    """A label made of the words of a key HaiveQueen has no label for: planSuffixRegex gives "Plan suffix regex" (owner decision 741 (3): no raw key names)."""
    words = re.findall(r"[A-Z]{2,}(?=[A-Z][a-z]|\b|[0-9_-])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", str(key))
    if not words:
        return str(key)
    text = " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in words)
    return text[:1].upper() + text[1:]


def same_dir(a, b) -> bool:
    """True when both texts name the same folder (case and separators ignored)."""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


# Owner decision 741 (1): the folders of an Apiary as the page shows them.
def apiary_place(workspace, settings) -> tuple:
    """(repository, plans folder) of a Project: the repository is codeRepo (or the old repo) of its project.toml, else the folder of the settings;
    the plans folder is the folder of the settings when it is not the repository, else empty (the plans are in the repository)."""
    code = str(settings.get("codeRepo") or settings.get("repo") or "")
    workspace = str(workspace or "")
    if code and workspace and not same_dir(code, workspace):
        return code, workspace
    return code or workspace, ""


def worktree_prefix(name) -> str:
    """The name prefix of new worktrees derived from the Project name (owner decision 741 (2)): letters, digits, '.', '_' and '-', others become '-'."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "")).strip("-.") or "wt"


# The values the Apiary page shows.
def apiary_settings(shown, legacy_deploy=False):
    """A copy of the settings of a project file with every setting of the Project page present."""
    out = dict(shown)   # legacy_deploy has no effect any more: the switches are gone
    for key, default in APIARY_DEFAULTS:
        out.setdefault(key, list(default) if isinstance(default, list) else default)
    return out


# Hide the release settings of an Apiary whose remote is not on GitHub.
def release_fields(shown, repository):
    """`shown` without publishReleases and nextVersion when `repository` (owner/name of the GitHub remote) is None (only for a remote on GitHub)."""
    if not repository:
        for key in RELEASE_KEYS:
            shown.pop(key, None)
    return shown


# The number of logical cores of this machine.
def machine_cores() -> int:
    """The number of logical cores (the largest choice of "CPU cores per build")."""
    from . import platform as osplat
    return osplat.logical_cores()


# The values the block "Build" shows for a settings dict.
def build_values(data, cores=None) -> dict:
    """The shown values {cores, parallel, minFreeRamGB} of the block "Build": the core count that applies now (an old coreShare, `fixed` or number gives the
    count it gives today, a count above the cores of the machine is shown as the cores of the machine, none gives two thirds rounded up), the parallel value
    (default 1) and the minimum free RAM in GB (default 2)."""
    from . import buildjobs
    cores = cores or machine_cores()
    raw = data.get("buildJobs")
    bj = buildjobs.settings_from(raw if raw is not None else 0)
    return {"cores": buildjobs.core_limit(bj, cores), "parallel": bj["Parallel"], "minFreeRamGB": bj["MinFreeGB"]}


# Write the three values of the block "Build" into a settings dict.
def apply_build(data, cores_text, parallel_text, ram_text, cores=None):
    """Check the three typed values and write them into `data["buildJobs"]`: the core count (a whole number from 1 to the cores of the machine), the
    parallel value (a whole number from 1) and the minimum free RAM in GB (a number above 0). The old keys coreShare and fixed and an old number
    buildJobs are removed (the new core count replaces them); the other keys of the table stay. A wrong value raises ValueError with the message."""
    cores = cores or machine_cores()

    def whole(text, label, low, high=None):
        """A whole number between low and high."""
        try:
            value = int(str(text).strip())
        except ValueError:
            raise ValueError(tr("%s: a whole number is expected", tr(label)))
        if value < low or (high is not None and value > high):
            raise ValueError(tr("%s: a whole number from %d to %d is expected", tr(label), low, high) if high is not None else tr("%s: a whole number from %d up is expected", tr(label), low))
        return value
    n_cores = whole(cores_text, N_("Total CPU budget for builds and checks"), 1, cores)
    parallel = whole(parallel_text, N_("Parallel builds"), 1)
    try:
        ram = float(str(ram_text).strip().replace(",", "."))
    except ValueError:
        raise ValueError(tr("Minimum free RAM before a build: a number is expected"))
    if not math.isfinite(ram) or ram <= 0:
        raise ValueError(tr("Minimum free RAM before a build: a number above 0 is expected"))
    old = data.get("buildJobs")
    table = {k: v for k, v in old.items() if k not in ("coreShare", "fixed", "cores", "parallel", "minFreeRamGB")} if isinstance(old, dict) else {}
    table.update({"cores": n_cores, "parallel": parallel, "minFreeRamGB": int(ram) if ram == int(ram) else ram})
    data["buildJobs"] = table


# Read a TOML file into a dict.
@filetxn.coordinated
def load_raw(path) -> dict:
    """The parsed content of the TOML file `path`; a missing file gives an empty dict."""
    contents = filetxn.read(path)
    data = LoadedSettings(tomllib.loads(contents.decode("utf-8")) if contents is not None else {})
    data.source = (str(Path(path).resolve()), contents)
    return data


# Read config.toml in the new form (old endpoint entries migrated); the file itself is not changed.
@filetxn.coordinated
def load_migrated(path) -> dict:
    """The parsed content of the TOML file `path` in the new form (old endpoint and [[servers]] entries taken over into the providers); the file stays as it is until the owner saves."""
    from . import servers
    raw = load_raw(path)
    data = LoadedSettings(servers.migrate(raw))
    data.source = raw.source
    return data


# Cut a comma separated tool list into its entries.
def split_tools(text) -> list:
    """Split `Bash(git merge *),Read` at the commas outside parentheses; empty entries are dropped and entries are trimmed."""
    out, depth, current = [], 0, ""
    for ch in str(text or ""):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return [item.strip() for item in out if item.strip()]


# Separate the allowed tools from the other arguments of a provider.
def split_args(args) -> tuple:
    """(other arguments, allowed tools, position): the `--allowedTools <list>` pair is taken out of `args`; `position` is where it stood (None when absent)."""
    args = [str(a) for a in (args or [])]
    if ALLOWED_FLAG in args:
        i = args.index(ALLOWED_FLAG)
        if i + 1 < len(args):
            return args[:i] + args[i + 2:], split_tools(args[i + 1]), i
    return args, [], None


# Put the allowed tools back into the argument list.
def join_args(rest, tools, position=None) -> list:
    """The argument list with `--allowedTools <comma separated tools>` inserted at `position` (at the end when unknown); no tools means no flag."""
    out = list(rest)
    if tools:
        i = len(out) if position is None else min(position, len(out))
        out[i:i] = [ALLOWED_FLAG, ",".join(tools)]
    return out


# TOML text of a key.
def _key(key) -> str:
    """The key as bare TOML key when it only has letters, digits, `_` and `-`, else as quoted string."""
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


# TOML text of a value that stays on one line.
def _value(value) -> str:
    """One-line TOML text of a bool, int, float, string, list or dict (a dict becomes an inline table)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(tr("a number must be finite"))
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join("%s = %s" % (_key(k), _value(v)) for k, v in value.items()) + " }" if value else "{}"
    raise ValueError(tr("cannot write %r as TOML", value))


# A dict that must become its own [table] because it holds other dicts.
def _is_table(value) -> bool:
    """True for a dict with at least one dict value."""
    return isinstance(value, dict) and any(isinstance(v, dict) for v in value.values())


# A list of dicts that must become [[array of tables]].
def _is_array(value) -> bool:
    """True for a non-empty list whose entries are all dicts."""
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


# Write one table and everything below it.
def _table(lines, path, data):
    """Append the key lines of `data`, then its sub-tables and arrays of tables, with the header names built from `path`."""
    for key, value in data.items():
        if not _is_table(value) and not _is_array(value):
            lines.append("%s = %s" % (_key(key), _value(value)))
    for key, value in data.items():
        if _is_table(value):
            lines += ["", "[%s]" % ".".join(_key(p) for p in path + [key])]
            _table(lines, path + [key], value)
    for key, value in data.items():
        if _is_array(value):
            for item in value:
                lines += ["", "[[%s]]" % ".".join(_key(p) for p in path + [key])]
                _table(lines, path + [key], item)


# TOML text of a whole settings dict.
def dumps(data) -> str:
    """TOML text of `data`: scalars first, then tables, then arrays of tables; reading it back with tomllib gives `data` again."""
    lines = []
    _table(lines, [], data)
    return "\n".join(lines).lstrip("\n") + "\n"


# Look for problems before a file is written.
def check(data, machine=True) -> list:
    """List of problem texts: the text must read back unchanged; a machine config must also pass the checks the loop makes when it reloads settings."""
    problems = []
    try:
        if tomllib.loads(dumps(data)) != data:
            problems.append(tr("the settings cannot be written as TOML without changing them"))
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        problems.append(str(exc))
    for key, label in (("remoteName", "Remote name"), ("integrationBranch", "Integration branch")):
        if data.get(key) and not re.fullmatch(r"[\w][\w./-]*", str(data[key])):
            problems.append(tr("%s: letters, digits and . / - _ are expected, starting with a letter or digit", tr(label)))
    limit = data.get("maxConcurrentCompileChecks")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        problems.append(tr("%s: a whole number from %d up is expected", tr(N_("Max concurrent compile checks")), 1))
    if str(data.get("nextVersion") or "").strip():
        from . import releases
        try:
            releases.next_version(str(data["nextVersion"]).strip())
        except ValueError:
            problems.append(tr("Next version: three numbers like 2.1.0 are expected"))
    update = data.get("update")
    if update is not None:
        hours = update.get("checkHours", 24) if isinstance(update, dict) else None
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours < 0:
            problems.append(tr("update.checkHours: a number of hours from 0 up is expected (0 = no daily check)"))
    if machine and not problems:
        from . import scheduler
        try:
            scheduler.get_providers(data)
            int(data.get("hives", 2))
            minutes = float(data.get("roundMinutes", 5))
            if not math.isfinite(minutes) or minutes <= 0:
                problems.append(tr("roundMinutes must be greater than zero"))
            names = [p.get("name") for p in data.get("providers") or []]
            if len(set(names)) != len(names):
                problems.append(tr("provider names must be unique"))
            from . import servers
            servers.get_servers(data)
        except (ValueError, TypeError, AttributeError) as exc:
            problems.append(str(exc))
    return problems


# Whether the values of a provider page are a selection of the owner.
def keep_legacy_entry(data, old):
    """A provider from before the ticks stays one until the owner changes its ticks or its old use capability-only: then the ticks are the selection of the owner. Showing and saving the page changes nothing."""
    from . import scheduler
    if old.get("capabilitiesVersion"):
        return
    use_kept = old.get("use") != "capability-only" or data.get("use") == "preferred"
    if set(data.get("capabilities") or []) != set(scheduler.declared_capabilities(old)) or not use_kept:
        data["capabilitiesVersion"] = 1
        return
    for key in ("capabilities", "use"):
        if key in old:
            data[key] = old[key]
        else:
            data.pop(key, None)


class LoadedSettings(dict):
    """Settings with an optimistic revision, including edits made outside the app."""


# Write a settings file and keep the old one.
@filetxn.coordinated
def save_file(path, data):
    """Update a TOML document, preserving its formatting and comments where values remain."""
    path = Path(path)
    document = tomlkit.document()
    original = ""
    contents = filetxn.read(path)
    source = getattr(data, "source", None)
    if source and source != (str(path.resolve()), contents):
        raise ValueError("settings changed since loading; reload before saving")
    if contents is not None:
        original = contents.decode("utf-8").replace("\r\n", "\n")
        document = tomlkit.parse(original)
    _update_document(document, data)
    result = tomlkit.dumps(document)
    remaining = Counter(_comments(result))
    for comment in _comments(original):
        if remaining[comment]:
            remaining[comment] -= 1
        else:
            result += "\n" + comment + "\n"
    if tomllib.loads(result) != data:
        raise ValueError("settings could not be written without changing their values")
    if contents is not None:
        filetxn.write(path.with_name(path.name + ".bak"), contents)
    encoded = result.encode("utf-8")
    filetxn.write(path, encoded)
    if source:
        data.source = (str(path.resolve()), encoded)


@filetxn.coordinated
def save_project_file(conn, path, data):
    """Save a project and capture the release boundary while integration writes are locked."""
    from . import config, db, publish
    path = Path(path)
    previous = config._load_toml(path)
    enabling = bool(data.get("publishReleases")) and not bool(previous.get("publishReleases"))
    if not enabling:
        save_file(path, data)
        return
    project = conn.execute("SELECT name FROM projects WHERE workspace=?", (str(path.parent.parent),)).fetchone()
    if not project:
        raise ValueError("project is not registered")
    name = project[0]
    with db.transaction(conn):
        # Keep the original boundary on re-enable: eligible integrations may
        # still be awaiting discovery, while disabled ones are excluded when
        # their queue rows are recorded.
        if not conn.execute("SELECT 1 FROM kv WHERE project=? AND key=? AND value != ''",
                            (name, publish.SINCE_KEY)).fetchone():
            before = [row[0] for row in conn.execute("SELECT id FROM queue WHERE project=? AND status='integrated'", (name,))]
            conn.execute(publish._UPSERT, (name, publish.SINCE_KEY, db.now(), db.now()))
            conn.execute(publish._UPSERT, (name, publish.BEFORE_KEY, json.dumps(before), db.now()))
        save_file(path, data)


def _comments(text):
    """Return TOML comments outside quoted strings, including multiline strings."""
    comments, quote, multiline, escaped = [], None, False, False
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and c == "\\":
                escaped = True
            elif multiline and text.startswith(quote * 3, i):
                # TOML permits one or two literal quotes just before the closing
                # delimiter. Consume the whole 3-5 quote run, not just three.
                end = i + 3
                while end < len(text) and text[end] == quote:
                    end += 1
                quote, multiline = None, False
                i = end - 1
            elif not multiline and c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            multiline = text.startswith(c * 3, i)
            if multiline:
                i += 2
        elif c == "#":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            comments.append(text[i:end].rstrip("\r"))
            i = end - 1
        i += 1
    return comments


def _update_document(document, data):
    """Change retained tables in place so their comments and layout survive."""
    from tomlkit.items import AoT, InlineTable, Table
    for key in list(document):
        if key not in data:
            del document[key]
    for key, value in data.items():
        if key not in document:
            document[key] = value
            continue
        old = document[key]
        if isinstance(value, dict) and isinstance(old, (Table, InlineTable)):
            _update_document(old, value)
        elif isinstance(value, list) and isinstance(old, AoT) and all(isinstance(v, dict) for v in value):
            # Provider/project names (or ids) identify entries even when the
            # settings editor removes or reorders them. Never transfer their
            # comments to a different named entry by position.
            identity = next((field for field in ("name", "id")
                             if all(isinstance(entry.get(field), str) for entry in [*old, *value])
                             and len({entry[field] for entry in old}) == len(old)
                             and len({entry[field] for entry in value}) == len(value)), None)
            if identity:
                entries = {entry[identity]: entry for entry in old}
                updated_array = tomlkit.aot()
                for updated in value:
                    entry = entries.get(updated[identity])
                    if entry is None:
                        entry = tomlkit.table()
                    _update_document(entry, updated)
                    updated_array.append(entry)
                document[key] = updated_array
            elif len(old) == len(value):
                for entry, updated in zip(old, value):
                    _update_document(entry, updated)
            elif old.unwrap() != value:
                document[key] = value
        elif (old.unwrap() if hasattr(old, "unwrap") else old) != value:
            document[key] = value


def edits_of(base, data) -> dict:
    """The settings of `data` that differ from `base` (nested tables compared key by key): the unsaved edits of a page."""
    edits = {}
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            inner = edits_of(base[key], value)
            if inner:
                edits[key] = inner
        elif key not in base or base[key] != value:
            edits[key] = value
    return edits


def with_edits(base, edits) -> dict:
    """A copy of `base` with `edits` laid over it (nested tables merged key by key)."""
    merged = json.loads(json.dumps(base))
    for key, value in edits.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = with_edits(merged[key], value)
        else:
            merged[key] = value
    return merged


# Convert text typed into the editor to the right type.
def parse_typed(text, like=None):
    """Convert `text` to the type of `like` (bool, int, float, str); without a model value guess: true/false, whole number, decimal, else text."""
    text = str(text).strip()
    if like is not None and not isinstance(like, str):
        if isinstance(like, bool):
            if text.lower() in ("true", "false"):
                return text.lower() == "true"
            raise ValueError(tr("true or false expected"))
        try:
            return type(like)(text)
        except ValueError:
            raise ValueError(tr("a number is expected"))
    if like is None:
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        for cast in (int, float):
            try:
                return cast(text)
            except ValueError:
                pass
    return text
