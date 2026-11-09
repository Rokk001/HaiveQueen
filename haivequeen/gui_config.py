"""Editor for the TOML settings in the graphical window: config.toml of the machine and the project.toml of every Project.

This file holds the data layer (read, split the allowed tools, write TOML, check, save with a backup); the window is `ConfigWindow` below.
TOML Kit preserves comments and layout when an existing settings file is saved.
"""
from . import filetxn
import json
import math
import os
import re
import tomllib
from collections import Counter
from pathlib import Path
import tomlkit

from . import gui_data, localtime
from .gui_provider import ProviderPages
from .i18n import N_, decimal, tr

ALLOWED_FLAG = "--allowedTools"   # the Claude Code argument whose value is the comma separated list of allowed tools
CHOICES = {"protocol": ("claude", "codex", "opencode", "kilo"), "outputFormat": ("claude", "codex", "opencode"), "use": ("preferred", "fallback"), "beeMode": ("subagent", "process")}   # fields that offer a fixed choice
CAPABILITIES = ("Coding", "Queen", "Build", "Deploy", "Image generation", "3D modelling", "Audio generation", "Video generation", "Vision", "Browser automation", "Web research", "GPU workloads", "Confidential data")
CLASSES = ("light", "medium", "heavy")
# The block "Build" of the General page: exactly these three fields, each with one sentence of explanation (key in the buildJobs table, label, explanation).
BUILD_TEXT_WIDTH = 470   # pixels of an explanation in the block "Build": what stays free beside the field in the window size below, in every language
BUILD_FIELDS = (
    ("cores", N_("CPU cores per build"), N_("How many of your processor cores builds may use in total; a build uses fewer when the free RAM is short.")),
    ("parallel", N_("Parallel builds"), N_("How many targets the build command builds at the same time (the number after --parallel); each target gets the cores divided by this number.")),
    ("minFreeRamGB", N_("Minimum free RAM before a build"), N_("A build only starts while at least this many GB of RAM are free, otherwise it waits.")),
)


# The settings of the Apiary page, each with the value that applies without an entry (for an Apiary from before the switches deploy stays on, see apiary_settings).
APIARY_DEFAULTS = (("ownerTopics", []), ("ownerCheckText", ""), ("compileCheckEnabled", True), ("buildEnabled", True), ("deployEnabled", False),
                   ("integrationBranch", "main"), ("remoteName", "origin"), ("worktreeLocation", ""), ("deleteFinishedWorktrees", True), ("deleteIntegratedBranches", False),
                   ("publishReleases", False), ("nextVersion", ""))
RELEASE_KEYS = ("publishReleases", "nextVersion")   # offered only for an Apiary whose remote is on GitHub
SETTING_LABELS = {
    "ownerTopics": N_("Topics that always go to the owner (one per line)"), "ownerCheckText": N_("Owner-check question (empty: default text)"),
    "compileCheckEnabled": N_("Run the compile check"), "buildEnabled": N_("Run the build"), "deployEnabled": N_("Run the deploy"),
    "integrationBranch": N_("Integration branch"), "remoteName": N_("Remote name"), "worktreeLocation": N_("Worktree location (empty: beside the repository)"),
    "deleteFinishedWorktrees": N_("Delete finished worktrees automatically"), "deleteIntegratedBranches": N_("Delete integrated branches (local and remote)"),
    "publishReleases": N_("Publish release at integration"), "nextVersion": N_("Next version (empty: count the last digit up)"),
    # Owner decision 741 (3): every field of the Apiary page has an understandable label; the internal key names stay in project.toml only.
    "name": N_("Name"), "repository": N_("Repository"), "plansFolder": N_("Plans folder (empty: docs/plans of the repository)"),
    "colour": N_("Colour"), "priority": N_("Priority"), "owner": N_("Owner name"), "language": N_("Language of owner texts (empty: General language)"), "escalateAfter": N_("Failed attempts before a stronger model (empty: General value)"), "allowedTools": N_("Commands allowed without asking"),
    "planGlobs": N_("Plan files (patterns, one per line)"), "planExclude": N_("Files that are not plans (patterns, one per line)"),
    "ignoredBuildOutput": N_("Build output to ignore (one per line)"), "buildFlowDoc": N_("Build instructions document"),
    "integratorInstructions": N_("Extra instructions for the integration"), "buildProcessNames": N_("Build program names (one per line)"),
    "compileCheck": N_("Compile check command"), "claudeTools": N_("Tools available to Claude agents"), "protectedWorktrees": N_("Protected worktrees (one per line)"),
    "onEvent": N_("Command after each event"), "onEvent.command": N_("Command"), "onEvent.workDir": N_("Working folder"), "onEvent.timeoutSeconds": N_("Time limit (seconds)"),
    "views": N_("Generated overviews"), "views.queue": N_("Queue overview file"), "views.questions": N_("Open questions file"), "views.decisions": N_("Decisions file"),
    "prompts": N_("Own prompt texts"), "prompts.integrator": N_("Instructions of the integrating Queen"),
}
# One line of explanation per field of the Apiary page (owner decision 741 (3)).
SETTING_HELP = {
    "name": N_("The name of the Project in HaiveQueen; new worktree names start with it."),
    "repository": N_("The Git repository of the code; releases, version, branches and worktrees always use it."),
    "plansFolder": N_("Only for plans kept outside the repository; this folder also holds the settings of the Project."),
    "colour": N_("The colour of the Project in the main window."), "priority": N_("Projects with a higher number get free places first."),
    "owner": N_("How the Queens and Agents call you in their texts."), "language": N_("The language of the texts HaiveQueen writes to you for this Project."),
    "ownerTopics": N_("Questions about these topics are never answered automatically."), "ownerCheckText": N_("The question HaiveQueen asks you when a plan is integrated."),
    "compileCheckEnabled": N_("Check that the code compiles and the tests pass before integration."), "buildEnabled": N_("Build the product after integration."),
    "deployEnabled": N_("Deploy the built product after integration."), "integrationBranch": N_("The branch finished plans are merged into."),
    "remoteName": N_("The Git remote HaiveQueen pushes to and fetches from."), "worktreeLocation": N_("The folder that holds the worktrees of the Agents; a relative path counts from the repository."),
    "deleteFinishedWorktrees": N_("Remove a worktree once its work is integrated and nothing is lost."),
    "deleteIntegratedBranches": N_("Remove plan branches once they are integrated and nothing is lost."),
    "publishReleases": N_("Publish a GitHub release of the repository at each integration."), "nextVersion": N_("The version of the next release, for example 2.1.0."),
    "planGlobs": N_("Where the plans are found, relative to the folder with the settings."), "planExclude": N_("Files matching a plan pattern that are not plans."),
    "ignoredBuildOutput": N_("Files the build writes that do not count as changes."), "buildFlowDoc": N_("A document that describes how the product is built."),
    "integratorInstructions": N_("Text added to the instructions of the integrating Queen."), "buildProcessNames": N_("Running programs that show a build is still busy."),
    "compileCheck": N_("The command that compiles the code and runs the tests."), "claudeTools": N_("The tools available to a Claude agent, separated by commas."),
    "protectedWorktrees": N_("Worktrees that are never removed automatically."),
    "onEvent.command": N_("A program started after each event, for example to sync files."), "onEvent.workDir": N_("The folder the command runs in."),
    "onEvent.timeoutSeconds": N_("The command is stopped after this many seconds."), "views.queue": N_("A Markdown file that shows the queue."),
    "views.questions": N_("A Markdown file that shows the open questions."), "views.decisions": N_("A Markdown file that shows the decisions."),
    "prompts.integrator": N_("Replaces the built-in instructions of the Queen that integrates a plan."),
}
GENERAL_LABELS = {"language": N_("Interface language"), "slots": N_("Agent slots"), "roundMinutes": N_("Round length (minutes)"), "escalateAfter": N_("Failed attempts before a stronger model")}
GENERAL_HELP = {"language": N_("The language of the HaiveQueen interface."), "slots": N_("The number of agents that may run at once."), "roundMinutes": N_("The time between coordinator rounds in minutes."), "escalateAfter": N_("After this many failed attempts of an Agent, HaiveQueen uses the next stronger model class.")}
SETTING_HELP.update({"escalateAfter": N_("Overrides the General value for this Project."), "allowedTools": N_("Commands agents of this Project may run without asking.")})
OTHER_HELP = N_("A further setting of this Project, as written in its settings file.")   # the explanation of a setting the page has no own text for
# Owner decision 741: the main part of the Apiary page, the collapsed section Advanced and the keys the page never shows as fields of their own.
MAIN_KEYS = ("colour", "priority", "owner", "language", "escalateAfter") + tuple(key for key, _default in APIARY_DEFAULTS)
ADVANCED_DEFAULTS = (("planGlobs", ["docs/plans/*.md"]), ("planExclude", []), ("ignoredBuildOutput", []), ("buildFlowDoc", ""), ("integratorInstructions", ""),
                     ("buildProcessNames", []), ("compileCheck", ""), ("claudeTools", ""), ("allowedTools", []))
HIDDEN_KEYS = ("name", "workspace", "preferredProvider", "codeRepo", "repo", "worktreePrefix", "roundMinutes")   # name and folders are the fields Name, Repository and Plans folder; the prefix follows the name
# Owner decision 742: a field is as wide as its kind of value needs, never the full window width.
SHORT_WIDTH, PATH_WIDTH, LABEL_WIDTH = 20, 60, 34   # characters: short values, paths and commands (they scroll inside), the label column
SHORT_KEYS = frozenset(("name", "colour", "color", "priority", "integrationBranch", "remoteName", "nextVersion", "worktreePrefix", "owner", "language", "model", "effort",
                        "slots", "protocol", "outputFormat", "use", "beeMode", "queenClass", "source", "hives", "roundMinutes", "timeoutSeconds", "checkHours", "version", "branch"))


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
    """A copy of the settings of a project file with every setting of the Project page present; legacy_deploy: a Project from before the switches, whose deploy stays on without an entry."""
    out = dict(shown)
    if legacy_deploy:
        out.setdefault("deployEnabled", True)
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
    n_cores = whole(cores_text, N_("CPU cores per build"), 1, cores)
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
    if set(data.get("capabilities") or []) != set(scheduler.provider_capabilities(old)) or not use_kept:
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


# The window of the editor.
class ConfigWindow(ProviderPages):
    """A Toplevel with three tabs: General (the machine settings), Providers and Projects; the files config.toml and project.toml stay in the background."""

    TABS = (("general", N_("General")), ("providers", N_("Providers")), ("apiaries", N_("Projects")))

    # Build the window for the settings files of `conn`.
    def __init__(self, root, conn, config_dir, mono="Consolas", on_saved=None):
        """Open the editor on `root`; `config_dir` holds config.toml, `mono` is the font family, `on_saved` is called after a successful save."""
        import tkinter as tk
        self.tk, self.root, self.conn, self.mono, self.on_saved = tk, root, conn, mono, on_saved
        from . import gui_dialog   # imported here: gui_dialog imports gui, which imports this module only on demand
        self.dialog = gui_dialog
        self.config_path = Path(config_dir) / "config.toml"
        self.data, self.node, self.getters, self.entries, self.visible, self.position, self.rows = {}, ("general", None), [], [], [], None, {}
        self.apiaries, self.initial_values, self.initial_build = [], {}, None
        self.build_vars, self.fresh = None, []   # fresh: the providers added in this window and not saved yet
        self.type_stash = []   # [(provider entry, values of the access types not shown)]: kept while the window is open
        self.ready_label = self.test_button = self.result_label = None
        self.advanced_open = False   # the section Advanced of the Apiary page starts collapsed and stays as the owner left it while the window is open
        self.win = tk.Toplevel(root)
        import tkinter.font
        self.char_px = max(1, tkinter.font.Font(root=self.win, family=mono, size=10).measure("0"))   # one character of the field font
        self.win.title(tr("HaiveQueen settings"))
        self.win.geometry("1100x700")
        self.win.minsize(640, 420)
        top = tk.Frame(self.win)
        top.pack(fill="x", padx=8, pady=6)
        self.tab_var = tk.StringVar(value="general")
        self.tab_buttons = {}
        for key, label in self.TABS:
            button = tk.Radiobutton(top, text=tr(label), value=key, variable=self.tab_var, indicatoron=False, width=12, command=lambda k=key: self.pick_tab(k))
            button.pack(side="left", padx=(0, 2))
            self.tab_buttons[key] = button
        from .gui import COL
        self.col = COL   # the dashboard colours, also for the status texts
        self.status = tk.Label(top, anchor="w", fg=COL["red"])
        self.status.pack(side="left", padx=12, fill="x", expand=True)
        body = tk.Frame(self.win)
        body.pack(fill="both", expand=True, padx=8)
        self.side = tk.Frame(body)
        self.side.pack(side="left", fill="y")
        self.nodes = tk.Listbox(self.side, width=28, exportselection=False, font=(mono, 10))
        self.nodes.pack(fill="y", expand=True)
        self.nodes.bind("<<ListboxSelect>>", self.pick_node)
        self.add_button = tk.Button(self.side, command=self.add_entry)
        self.add_button.pack(fill="x", pady=1)
        tk.Button(self.side, text=tr("Remove entry"), command=self.remove_node).pack(fill="x", pady=1)
        canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas = canvas
        scroll = self.dialog.scrollbar(body, canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.form = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.form, anchor="nw")
        self.form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", self.wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        foot = tk.Frame(self.win)
        foot.pack(fill="x", side="bottom", padx=8, pady=6, before=body)   # packed before the body so the shrinking body never pushes it out
        bottom = tk.Frame(foot)
        bottom.pack(fill="x")
        self.bottom_buttons = []
        for text, command in (("Save", self.save), ("Revert", self.reload), ("Open folder", self.open_folder), ("Close", self.win.destroy)):
            button = tk.Button(bottom, text=tr(text), width=12, command=command)
            button.pack(side="left", padx=4)
            self.bottom_buttons.append(button)
        self.hint = tr("Changes apply to the running HaiveQueen without a restart. The old file is kept as <name>.bak.")
        self.bottom_note = tk.Label(foot, text=self.hint, fg=COL["dim"], justify="left", anchor="w")
        self.bottom_note.pack(fill="x", pady=(6, 0))   # its own row below the buttons over the full width, wraps to as many lines as needed
        self.bottom_note.bind("<Configure>", lambda e: self.bottom_note.configure(wraplength=max(120, e.width)))
        self.reload()

    def wheel(self, event):
        """The mouse wheel scrolls the page (also over a combobox, which never changes its value by the wheel)."""
        delta = getattr(event, "delta", 0) or {4: 120, 5: -120}.get(getattr(event, "num", 0), 0)
        self.canvas.yview_scroll(int(-delta / 120) or (-1 if delta > 0 else 1 if delta < 0 else 0), "units")

    def show_hint(self):
        """The bottom line shows the standing hint again (after the message Saved.)."""
        self.bottom_note.config(text=self.hint, fg=self.col["dim"])

    # Is the shown page a page of config.toml (General or a provider)?
    def machine(self) -> bool:
        """True on the General tab and on a provider page; False on a Project page."""
        return self.node[0] != "apiaries"

    def registered(self) -> dict:
        """{name: (workspace, legacy_deploy)} of the Projects in the database."""
        return {r[0]: (r[1], bool(r[2])) for r in self.conn.execute("SELECT name, workspace, legacy_deploy FROM projects")}

    # The Apiaries of the tab Apiaries.
    def load_apiaries(self):
        """One record per Project: every entry of config.toml, then the Projects registered only in the database whose folder exists."""
        registered = self.registered()
        self.apiaries = [self.apiary_record(entry, registered) for entry in self.data.get("projects") or []]
        listed = {a["name"] for a in self.apiaries}
        for name in sorted(registered):
            if name not in listed and registered[name][0] and Path(registered[name][0]).is_dir():
                self.apiaries.append(self.apiary_record(None, registered, name))
        self.migrate_allowed_tools()

    def migrate_allowed_tools(self):
        """Stage provider grants for branches unique to one existing Project."""
        for provider in self.data.get("providers") or []:
            rest, tools, position = split_args(provider.get("args"))
            keep = []
            for tool in tools:
                matches = [a for a in self.apiaries
                           if re.search(r"(?<![\w./-])" + re.escape(str(a["file"].get("integrationBranch") or "main")) + r"(?![\w./-])", tool)]
                if len(matches) != 1 or not matches[0]["exists"]:
                    keep.append(tool)
                    continue
                target = matches[0]["file"].setdefault("allowedTools", [])
                if not isinstance(target, list):
                    keep.append(tool)
                    continue
                if tool not in target:
                    target.append(tool)
            if keep != tools:
                provider["args"] = join_args(rest, keep, position)

    def apiary_record(self, entry, registered, name=None):
        """The record of one Project: its entry in config.toml (None for a Project only in the database), its name, the workspace whose project.toml it uses
        (the registered one wins over the one typed in config.toml), the settings of that file, a copy to see changes, and whether it is a Project from before the switches."""
        name = name if entry is None else str(entry.get("name") or "")
        row = registered.get(name)
        workspace = str(row[0] if row and row[0] else (entry or {}).get("workspace") or "")
        path = Path(workspace) / ".haivequeen" / "project.toml" if workspace else None
        data = load_raw(path) if path else {}
        return {"entry": entry, "name": name, "workspace": workspace, "path": path, "file": data, "saved": json.dumps(data, sort_keys=True),
                "legacy": bool(row and row[1]), "exists": bool(path and path.is_file())}

    # Read the files again and show their parts.
    def reload(self):
        """Drop unsaved changes: read config.toml and the project.toml of every Project and show the General tab."""
        self.fresh, self.type_stash = [], []
        self.data = load_migrated(self.config_path)   # old endpoint entries in the new form; the file changes only when saved
        self.load_apiaries()
        self.status.config(text="")
        self.node = ("general", None)
        self.fill_nodes()
        self.select(0)

    # List the parts of the settings.
    def fill_nodes(self):
        """Rebuild the list of all pages (General, every provider, every Project) and show the pages of the current tab on the left."""
        listed = self.data.get("projects") or []
        if [a["entry"] for a in self.apiaries if a["entry"] is not None] != listed:
            registered = self.registered()   # entries added to or removed from config.toml outside the buttons
            old = {id(a["entry"]): a for a in self.apiaries if a["entry"] is not None}
            self.apiaries = [old.get(id(e)) or self.apiary_record(e, registered) for e in listed] + [a for a in self.apiaries if a["entry"] is None]
        self.entries = ([("general", None)] + [("providers", i) for i in range(len(self.data.get("providers") or []))]
                        + [("apiaries", k) for k in range(len(self.apiaries))])
        self.show_tab(self.node[0])

    def label(self, node) -> str:
        """The text of a page in the left list."""
        kind, i = node
        if kind == "providers":
            entry = self.data[kind][i]
            return tr("%s (disabled)", entry.get("name", "?")) if entry.get("enabled", True) is False else str(entry.get("name", "?"))
        if kind == "apiaries":
            return self.apiaries[i]["name"] or "?"
        return tr("General")

    def show_tab(self, tab):
        """Show the pages of `tab` in the left list (hidden on the General tab) and the matching Add button."""
        self.tab_var.set(tab)
        self.visible = [node for node in self.entries if node[0] == tab]
        self.refresh_list()
        if tab == "general":
            self.side.pack_forget()
        else:
            self.add_button.config(text=tr({"providers": "Add provider"}.get(tab, "Add Project")))
            self.side.pack(side="left", fill="y", before=self.canvas)

    def refresh_list(self):
        """Write the texts of the list on the left again (a name or the switch Enabled changed); a disabled provider stays in it, dimmed, with "(disabled)"."""
        self.nodes.delete(0, "end")
        for k, node in enumerate(self.visible):
            self.nodes.insert("end", self.label(node))
            if node[0] == "providers" and self.data["providers"][node[1]].get("enabled", True) is False:
                self.nodes.itemconfigure(k, fg=self.col["dim"])
        if self.node in self.visible:
            self.nodes.selection_set(self.visible.index(self.node))

    # A tab was clicked.
    def pick_tab(self, tab):
        """Keep the typed values of the shown page, then show the first page of `tab` (an empty page when the tab has none)."""
        if not self.collect():
            self.tab_var.set(self.node[0])
            return
        self.show_tab(tab)
        if self.visible:
            self.select(self.entries.index(self.visible[0]))
        else:
            self.node = (tab, None)
            self.clear_form()

    # Show one part.
    def select(self, row):
        """Show page `row` of the list of all pages (0 is General, then the providers, then the Projects), switching to its tab."""
        self.node = self.entries[row]
        self.show_tab(self.node[0])
        self.nodes.selection_clear(0, "end")
        self.nodes.selection_set(self.visible.index(self.node))
        self.build_form()

    # Row clicked in the list.
    def pick_node(self, _event):
        """Keep the typed values of the old part, then show the clicked part; a wrong value stays on screen with its message."""
        picked = self.nodes.curselection()
        if not picked:
            return
        if not self.collect():
            self.nodes.selection_clear(0, "end")
            if self.node in self.visible:
                self.nodes.selection_set(self.visible.index(self.node))
            return
        self.node = self.visible[picked[0]]
        self.build_form()

    # The dict a part edits.
    def target(self) -> dict:
        """The dict of the shown part: the top level for General, the provider entry, or for a Project {"entry": its entry in config.toml, "file": its project.toml}."""
        kind, i = self.node
        if kind == "general":
            return self.data
        if kind == "apiaries":
            apiary = self.apiaries[i]
            return {"entry": apiary["entry"] if apiary["entry"] is not None else {}, "file": apiary["file"]}
        return self.data[kind][i]

    def clear_form(self):
        """Remove every row of the right side."""
        for child in self.form.winfo_children():
            child.destroy()
        self.getters, self.initial_values, self.initial_build = [], {}, None
        self.rows = {}   # {path: frame of its editor} of the shown page
        self.build_vars = None   # the three variables of the block "Build" while the General page is shown
        self.login_status = self.login_button = self.benchmark_button = self.benchmark_status = None   # benchmark_status stays None: no text on the page
        self.ready_label = self.test_button = self.result_label = None
        self.sections, self.notes, self.note_sources, self.frame_of = [], {}, {}, {}

    # Build the form of the shown part.
    def build_form(self):
        """Fill the right side with the page of the shown part: General (one row per setting), a provider (the form of its access type) or a Project (its entry of
        config.toml and all settings of its project.toml on one page)."""
        self.clear_form()
        self.show_hint()
        self.refresh_list()
        kind, i = self.node
        self.position = None
        if kind != "general" and i is None:
            return
        if kind == "apiaries":
            self.apiary_form()
            return
        if kind == "providers":
            self.provider_form()
            self.initial_build = None
            return
        data = self.target()
        shown = {k: v for k, v in data.items() if k not in ("providers", "projects", "servers")}
        shown.pop("buildJobs", None)   # the build settings are the block "Build" below, never the raw table
        shown.pop("ramReserveMb", None)   # machine-only, settable in config.toml only; not offered here, the stored value stays untouched on save
        self.build_block()
        self.bzz_row()
        shown.pop("preferredProvider", None)
        shown.setdefault("language", "English")
        shown.setdefault("slots", 2)
        shown.setdefault("roundMinutes", 5)
        shown.setdefault("escalateAfter", 3)
        main = {k: shown.pop(k) for k in ("language", "slots", "roundMinutes", "escalateAfter") if k in shown}
        self.fields(self.form, main, ())
        box = self.advanced_section()
        self.fields(box, shown, ())
        self.add_row(box, ())
        self.initial_values = {path: getter() for path, getter, _like in self.getters}
        self.initial_build = tuple(v.get() for v in self.build_vars) if self.build_vars else None

    def apiary_form(self):
        """The page of one Project (owner decision 741): Name, Repository and Plans folder, the settings a normal user needs, each with a label and one line of
        explanation, and the collapsed section Advanced with the other settings of its project.toml; every setting once."""
        apiary = self.apiaries[self.node[1]]
        entry = apiary["entry"]
        if entry is not None:
            self.adopt_workspace(apiary)
            self.fields(self.form, {"name": str(entry.get("name") or "")}, ("entry",))
        else:
            holder, cell = self.field_row(self.form, ("entry", "name"))
            self.tk.Label(holder, text=apiary["name"], anchor="w", font=(self.mono, 10)).pack(side="left")
            self.explain(cell, ("entry", "name"))
        repository, plans = apiary_place(apiary["workspace"], apiary["file"])
        for key, value in (("repository", repository), ("plansFolder", plans)):
            holder, cell = self.field_row(self.form, ("place", key))
            var = self.tk.StringVar(value=value)
            entry_widget = self.tk.Entry(holder, textvariable=var, width=PATH_WIDTH, font=(self.mono, 10))
            entry_widget.pack(side="left")
            entry_widget.bind("<FocusOut>", self.workspace_entered, add="+")   # the settings of an existing project.toml appear when the folder is entered
            entry_widget.bind("<Return>", self.workspace_entered, add="+")
            self.explain(cell, ("place", key))
            self.getters.append((("place", key), var.get, value))
        shown = {k: v for k, v in apiary["file"].items() if k not in HIDDEN_KEYS}   # the name is that of config.toml, the folders are Repository and Plans folder (no setting twice)
        shown.setdefault("language", "")
        shown.setdefault("escalateAfter", "")
        shown = release_fields(apiary_settings(shown, apiary["legacy"]), self.github_repository(shown))
        main = {key: shown.pop(key) for key in MAIN_KEYS if key in shown}
        for key, default in ADVANCED_DEFAULTS:
            shown.setdefault(key, list(default) if isinstance(default, list) else default)
        advanced = {key: shown.pop(key) for key, _default in ADVANCED_DEFAULTS}
        advanced.update(shown)
        self.fields(self.form, main, ("file",))
        box = self.advanced_section()
        if entry is not None:
            self.fields(box, {k: v for k, v in entry.items() if k not in ("name", "workspace")}, ("entry",))   # other values of its entry in config.toml
        self.fields(box, advanced, ("file",))
        self.add_row(box, ("file",))
        self.initial_values = {path: getter() for path, getter, _like in self.getters}

    def advanced_section(self):
        """The section Advanced below the main settings of a Project page, collapsed until the owner opens it; returns the frame its fields go into."""
        tk = self.tk
        head = tk.Frame(self.form)
        head.pack(fill="x", pady=(10, 2), anchor="w")
        box = tk.Frame(self.form)

        def show():
            """Show or hide the fields and the arrow of the heading."""
            self.advanced_button.config(text=("▾ " if self.advanced_open else "▸ ") + tr("Advanced"))
            if self.advanced_open:
                box.pack(fill="x", anchor="w", after=head)
            else:
                box.pack_forget()

        def toggle():
            """Open or close the section."""
            self.advanced_open = not self.advanced_open
            show()
        self.advanced_button = tk.Button(head, command=toggle, relief="flat", anchor="w", font=(self.mono, 10, "bold"))
        self.advanced_button.pack(side="left")
        self.advanced_box = box
        show()
        return box

    def apply_place(self, apiary, repository, plans):
        """Write the typed Repository and Plans folder of `project` into its settings: the folder of project.toml (the plans folder, else the repository) and
        codeRepo (only with a separate plans folder). The folder of project.toml of a registered Project stays; returns a problem text or None."""
        repository, plans = str(repository).strip(), str(plans).strip()
        if plans and not repository:
            return tr("Repository: the folder of the Git repository is expected")
        home = plans or repository
        if apiary["name"] in self.registered():
            if not home or not same_dir(home, apiary["workspace"]):
                return tr("The settings of this Project are kept in %s: enter that folder as plans folder or, when the plans are in the repository, as repository", apiary["workspace"])
        elif apiary["entry"] is not None:
            apiary["entry"]["workspace"] = home
        data = apiary["file"]
        current = str(data.get("codeRepo") or data.get("repo") or "")
        code = repository if plans and not same_dir(repository, plans) else ""
        if not code:
            data.pop("codeRepo", None)
            data.pop("repo", None)
        elif not current or not same_dir(code, current):
            data["codeRepo"] = code
            data.pop("repo", None)
        return None

    def field_row(self, parent, path):
        """A row with the label of `path` in the label column; returns (frame for the editor, column frame below which the explanation goes)."""
        tk = self.tk
        row = tk.Frame(parent)
        row.pack(fill="x", pady=2, anchor="w")
        tk.Label(row, text=self.text_of(path), width=LABEL_WIDTH, anchor="nw", justify="left", wraplength=LABEL_WIDTH * self.char_px, font=(self.mono, 10)).pack(side="left", anchor="n")
        cell = tk.Frame(row)
        cell.pack(side="left", anchor="n")
        holder = tk.Frame(cell)
        holder.pack(anchor="w")
        self.rows[path] = holder
        return holder, cell

    def text_of(self, path) -> str:
        """The label of a setting: on a Project page its understandable label (the words of the key for a setting HaiveQueen has no label for), else the key."""
        name = ".".join(path[1:])
        if self.node[0] == "apiaries" and path[:1] in (("file",), ("entry",), ("place",)):
            return tr(SETTING_LABELS[name]) if name in SETTING_LABELS else readable(path[-1])
        if self.node[0] == "general":
            return tr(GENERAL_LABELS.get(path[-1], readable(path[-1])))
        return str(path[-1])

    def explain(self, cell, path):
        """The one line of explanation of a setting of a Project page below its field."""
        name = ".".join(path[1:])
        if self.node[0] in ("apiaries", "general"):
            help_text = SETTING_HELP.get(name, OTHER_HELP) if self.node[0] == "apiaries" else GENERAL_HELP.get(path[-1], OTHER_HELP)
            self.tk.Label(cell, text=tr(help_text), anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9),
                          wraplength=PATH_WIDTH * self.char_px).pack(anchor="w")

    def adopt_workspace(self, apiary) -> bool:
        """For a Project not registered yet whose workspace in config.toml was changed: take the project.toml of that workspace (if any) as its settings
        and lay the unsaved edits of the page over them, so neither the edits nor the unchanged values of the file are lost. True when the record changed."""
        entry = apiary["entry"]
        if entry is None or apiary["name"] in self.registered():
            return False
        typed = str(entry.get("workspace") or "")
        if typed == apiary["workspace"]:
            return False
        edits = edits_of(json.loads(apiary["saved"]), apiary["file"])
        record = self.apiary_record(entry, {})
        record["file"] = with_edits(record["file"], edits)
        apiary.update(record)
        return True

    # The workspace of an Apiary page was typed.
    def workspace_entered(self, _event=None):
        """Keep the typed values and, when the typed workspace already holds a project.toml (or no longer does), show its settings at once."""
        if self.node[0] != "apiaries" or self.node[1] is None:
            return
        apiary = self.apiaries[self.node[1]]
        if not self.collect():
            return
        saved = apiary["saved"]
        if self.adopt_workspace(apiary) and apiary["saved"] != saved:
            self.build_form()

    def github_repository(self, shown):
        """owner/name of the GitHub remote of the repository of the shown Project (its remoteName), or None; never that of a separate plans folder."""
        from . import publish
        from types import SimpleNamespace
        apiary = self.apiaries[self.node[1]]
        settings = dict(apiary.get("file") or {})   # the page hides codeRepo from `shown`; a caller may still pass it there
        settings.update({key: shown[key] for key in ("codeRepo", "repo") if shown.get(key)})
        repository = apiary_place(apiary.get("workspace"), settings)[0]
        if not repository:
            return None
        try:
            return publish.github_repository(SimpleNamespace(workspace=Path(repository), code_repo=Path(repository), settings={"remoteName": shown.get("remoteName") or "origin"}))
        except Exception:
            return None

    def bzz_row(self):
        """Offer profile installation and show the result for each CLI."""
        row = self.tk.Frame(self.form)
        row.pack(fill="x", pady=4)
        status = self.tk.Label(row, anchor="w", justify="left")
        def install():
            from . import bzz_install
            result = bzz_install.install()
            status.config(text="\n".join("%s: %s" % (tr(name), tr(message)) for name, message in result.items()))
        self.tk.Button(row, text=tr("Install /bzz"), command=install).pack(anchor="w")
        status.pack(anchor="w")

    # The block "Build" of the General page.
    def build_block(self):
        """Add the block "Build" at the top of the General page: CPU cores per build (a choice from 1 to the cores of the machine), Parallel builds and
        Minimum free RAM before a build (GB), each with one sentence of explanation."""
        tk = self.tk
        cores = machine_cores()
        shown = build_values(self.data, cores)
        box = tk.LabelFrame(self.form, text=tr("Build"), padx=6, pady=4)
        box.pack(fill="x", pady=(0, 10), anchor="w")
        cores_var, parallel_var, ram_var = tk.StringVar(value=str(shown["cores"])), tk.StringVar(value=str(shown["parallel"])), tk.StringVar(value=decimal("%g" % shown["minFreeRamGB"]))
        self.build_vars = (cores_var, parallel_var, ram_var)
        self.build_notes = []
        for (key, label, explanation), var in zip(BUILD_FIELDS, self.build_vars):
            item = tk.Frame(box)
            item.pack(fill="x", pady=2, anchor="w")
            row = tk.Frame(item)
            row.pack(fill="x", anchor="w")
            tk.Label(row, text=tr(label), width=LABEL_WIDTH, anchor="nw", justify="left", wraplength=LABEL_WIDTH * self.char_px, font=(self.mono, 10)).pack(side="left", anchor="n")
            if key == "cores":
                self.dialog.option_menu(row, var, [str(n) for n in range(1, cores + 1)], self.mono).pack(side="left")
            else:
                tk.Entry(row, textvariable=var, width=8, font=(self.mono, 10)).pack(side="left")
            note = tk.Label(item, text=tr(explanation), anchor="w", justify="left", fg=self.col["dim"], font=(self.mono, 9), wraplength=300)
            note.pack(fill="x", anchor="w", padx=(4, 0))   # under the field, wrapped to the width that is available (never cut off)
            item.bind("<Configure>", lambda e, n=note: n.configure(wraplength=max(120, e.width - 12)))
            self.build_notes.append(note)

    # The provider of the shown page in the form the profile functions read.
    def login_provider(self) -> dict:
        """The shown provider entry as the profile functions need it (name, protocol, command and the own-CLI values), with the defaults config.toml applies."""
        from . import profile
        raw = self.target()
        name = str(raw.get("name") or "")
        proto = str(raw.get("protocol") or name)
        return {"name": name, "protocol": proto, "command": str(raw.get("command") or proto), **profile.custom_fields(raw)}

    # Show whether the shown provider has its login in the profile.
    def refresh_login(self):
        """Read the login state again from the profile folder and show it; does nothing on a page without a login row."""
        from . import profile
        if self.login_status is None:
            return
        present = profile.has_login(self.login_provider())
        self.login_status.config(text=tr("Login present") if present else tr("Login missing"), fg=self.col["green"] if present else self.col["red"])
        self.refresh_state()

    # The Login button.
    def login_click(self):
        """After a safety question open the terminal window for the login of the shown provider; the state is read again when that window is closed."""
        from . import profile
        if not self.collect():
            return
        prov = self.login_provider()
        if not profile.login_command(prov):
            self.dialog.info(self.win, "HaiveQueen", tr("No login is set up for this provider. For a CLI that is not built in, set the profile variables, "
                                                "the login command and the login files on this page."), self.mono)
            return
        if not self.dialog.ask(self.win, "HaiveQueen", tr("Open a terminal window and run `%s` for provider %s in the HaiveQueen profile?", profile.login_command(prov), prov["name"]), self.mono):
            return
        proc = profile.login_run(prov)
        if proc is None:
            self.dialog.error(self.win, "HaiveQueen", tr("The login window could not be opened (no terminal program found)."), self.mono)
            return
        self.watch_login(proc)

    # The Benchmark Start Context button.
    def benchmark_click(self):
        """Open the benchmark window for the selected provider."""
        from . import gui_benchmark
        if not self.collect():
            return
        self.refresh_state()
        if self.benchmark_button is None or str(self.benchmark_button.cget("state")) == "disabled":
            return
        name = self.login_provider()["name"]
        old = getattr(self, "benchmark_win", None)
        if old is not None and old.win.winfo_exists():
            old.win.destroy()
        self.benchmark_win = gui_benchmark.BenchmarkWindow(self.root, self.conn, name, self.mono, dict(self.data))

    # Refresh the state when the terminal window is closed.
    def watch_login(self, proc):
        """Check every 2 seconds whether the login window is still open; when it is closed, read the login state again."""
        def check():
            """Re-arm the timer while the window is open, else refresh."""
            try:
                if not self.win.winfo_exists():
                    return
                if proc.poll() is None:
                    self.win.after(2000, check)
                else:
                    self.refresh_login()
            except self.tk.TclError:
                pass
        self.win.after(2000, check)

    # Rows for the keys of a dict.
    def fields(self, parent, data, path):
        """Add one labelled editor per key of `data` below `parent`; `path` names the nesting for the collected values."""
        tk = self.tk
        for key, value in data.items():
            if isinstance(value, dict):
                row = tk.Frame(parent)
                row.pack(fill="x", pady=2, anchor="w")
                box = tk.LabelFrame(row, text=self.text_of(path + (key,)), padx=6, pady=4)
                box.pack(anchor="w")   # as wide as its fields need (owner decision 742)
                self.fields(box, value, path + (key,))
                self.add_row(box, path + (key,))
                continue
            holder, cell = self.field_row(parent, path + (key,))
            self.getters.append((path + (key,), self.editor(holder, key, value), value))
            self.explain(cell, path + (key,))

    # One editor widget for a value; returns the function that reads it back.
    def editor(self, parent, key, value, choices=None):
        """Make the editor for `value` (check box, choice from `choices` or CHOICES, line, one entry per line, or JSON for anything else) and return its getter."""
        tk = self.tk
        if key == "capabilities" and self.node[0] == "providers":
            box = tk.Frame(parent)
            box.pack(side="left", anchor="w")
            choices = list(dict.fromkeys(CAPABILITIES + tuple(value)))
            variables = [(name, tk.BooleanVar(value=name in value)) for name in choices]
            labels = {"Coding": "Coding (including tests, docs and review)", "Queen": "Queen (plan cutting and coordination)", "Build": "Build (with this machine’s tools)", "Vision": "Vision (understand images and screenshots)", "Confidential data": "Confidential data (local models only)"}
            for name, var in variables:
                tk.Checkbutton(box, text=tr(labels.get(name, name)), variable=var, anchor="w").pack(anchor="w")
            custom = tk.StringVar()
            row = tk.Frame(box)
            row.pack(anchor="w")
            tk.Label(row, text=tr("Add own")).pack(side="left")
            tk.Entry(row, textvariable=custom, width=SHORT_WIDTH, font=(self.mono, 10)).pack(side="left")
            return lambda: [name for name, var in variables if var.get()] + [name.strip() for name in custom.get().split(",") if name.strip()]
        if isinstance(value, bool):
            var = tk.BooleanVar(value=value)
            tk.Checkbutton(parent, variable=var).pack(side="left")
            return var.get
        if key == "language" and self.machine() and self.node[0] == "general":
            var = tk.StringVar(value=value)
            self.dialog.option_menu(parent, var, list(dict.fromkeys(("English", "German", value))), self.mono).pack(side="left")
            return var.get
        if isinstance(value, str) and (choices or key in CHOICES):
            var = tk.StringVar(value=value)
            self.dialog.option_menu(parent, var, list(dict.fromkeys(tuple(choices or CHOICES[key]) + (value,))), self.mono).pack(side="left")
            return var.get
        if isinstance(value, (int, float, str)):
            var = tk.StringVar(value=str(value))
            tk.Entry(parent, textvariable=var, width=field_width(key, value), font=(self.mono, 10)).pack(side="left")   # a longer value scrolls inside the field
            return var.get
        text = tk.Text(parent, height=min(max(len(value) if isinstance(value, list) else 3, 2), 14), width=PATH_WIDTH, font=(self.mono, 10), wrap="none")
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            text.insert("1.0", "\n".join(value))
            text.pack(side="left")
            return lambda: text.get("1.0", "end-1c").split("\n") if text.get("1.0", "end-1c") else []
        text.insert("1.0", json.dumps(value))
        text.pack(side="left")
        def read_json():
            """The typed JSON value; a wrong one raises ValueError with the message."""
            try:
                return json.loads(text.get("1.0", "end"))
            except ValueError:
                raise ValueError(tr("valid JSON is expected"))
        return read_json

    # A row to add one setting.
    def add_row(self, parent, path):
        """Add a small `new setting / value / Add` row below `parent` that creates a setting in the dict at `path` (text, number or true/false)."""
        tk = self.tk
        row = tk.Frame(parent)
        row.pack(fill="x", pady=(6, 2), anchor="w")
        key, value = tk.StringVar(), tk.StringVar()
        tk.Label(row, text=tr("new setting"), width=LABEL_WIDTH, anchor="w", fg=self.col["dim"], font=(self.mono, 10)).pack(side="left")
        tk.Entry(row, textvariable=key, width=SHORT_WIDTH, font=(self.mono, 10)).pack(side="left")
        tk.Entry(row, textvariable=value, width=40, font=(self.mono, 10)).pack(side="left", padx=4)

        def add():
            """Create the setting and redraw the form."""
            if not key.get().strip() or not self.collect():
                return
            target = self.target()
            for step in path:
                target = target[step]
            target[key.get().strip()] = parse_typed(value.get())
            self.build_form()
        tk.Button(row, text=tr("Add"), command=add).pack(side="left")

    # Read the typed values back into the data.
    def collect(self) -> bool:
        """Write every editor value of the shown part into `self.data`; show the first wrong value in the status line and return False."""
        data = self.target()
        values = {}
        current = {}
        where = ()
        try:
            for path, getter, like in self.getters:
                where = path
                raw = getter()
                current[path] = raw
                values[path] = raw if (self.node[0] == "apiaries" and path == ("file", "escalateAfter")) or isinstance(raw, (bool, list, dict)) or isinstance(like, (bool, list)) else parse_typed(raw, like)
        except (ValueError, TypeError) as exc:
            self.status.config(text="%s: %s" % (self.text_of(where) if self.node[0] == "apiaries" else ".".join(where), exc), fg=self.col["red"])
            return False
        if self.build_vars is not None and tuple(v.get() for v in self.build_vars) != self.initial_build:
            try:
                apply_build(self.data, *[v.get() for v in self.build_vars], cores=machine_cores())
            except ValueError as exc:
                self.status.config(text=str(exc), fg=self.col["red"])
                return False
        place = (("place", "repository"), ("place", "plansFolder"))
        if self.node[0] == "apiaries" and place[0] in current and any(current[p] != self.initial_values.get(p) for p in place):
            problem = self.apply_place(self.apiaries[self.node[1]], current[place[0]], current[place[1]])
            if problem:
                self.status.config(text=problem, fg=self.col["red"])
                return False
            data = self.target()   # the entry of an Apiary only in the database stays a fresh dict
        old_entry = dict(data) if self.node[0] == "providers" else None
        for path, raw in values.items():
            if (self.node[0] == "providers" and path in (("allowedTools",), ("args",))) or path[:1] == ("place",) or current[path] == self.initial_values[path]:
                continue
            if self.node[0] == "providers" and path in (("apiKey",), ("contextWindow",)):
                continue   # handled below: the key is masked, the window is a whole number
            target = data
            for step in path[:-1]:
                target = target.setdefault(step, {})
            if self.node[0] == "apiaries" and path in (("file", "language"), ("file", "escalateAfter")) and not str(raw).strip():
                target.pop(path[-1], None)
            elif self.node[0] == "apiaries" and path == ("file", "escalateAfter"):
                try:
                    target[path[-1]] = int(raw)
                except (ValueError, TypeError):
                    self.status.config(text=tr("a number is expected"), fg=self.col["red"])
                    return False
            else:
                target[path[-1]] = raw
        if self.node[0] == "providers" and not self.collect_access(data, current):
            return False
        if self.node[0] == "providers":
            self.collect_models(data, current)
            if current.get(("name",)) != self.initial_values.get(("name",)):
                data["nameEdited"] = True   # a name typed by the user no longer follows CLI and source
            if any(current.get((key,)) != self.initial_values.get((key,)) for key in ("capabilities", "use")):
                keep_legacy_entry(data, old_entry)
            rest = values.get(("args",), [])
            tools = values.get(("allowedTools",), [])
            if rest != self.initial_values.get(("args",)) or tools != self.initial_values.get(("allowedTools",)):
                args = join_args(rest, tools, self.position)
                if tools == self.initial_values.get(("allowedTools",)) and self.position is not None:
                    pair = old_entry["args"][self.position:self.position + 2]
                    if tools:
                        args[self.position + 1] = pair[1]
                    else:
                        pos = min(self.position, len(args))
                        args[pos:pos] = pair
                data["args"] = args
                _, _, self.position = split_args(args)
        self.initial_values = current
        self.initial_build = tuple(v.get() for v in self.build_vars) if self.build_vars else None
        self.status.config(text="")
        if self.node[0] == "providers":
            self.refresh_state()
        return True

    # The values of the shown provider that need more than a copy.
    def collect_access(self, data, current) -> bool:
        """Apply the API key (the mask means unchanged; empty removes it) and the context window (whole tokens, empty removes it) of the provider form and move the
        kept test result along with a renamed Local LLM; show the first wrong value in the status line and return False."""
        from . import servers
        try:
            window = str(current.get(("contextWindow",), "")).strip()
            if ("contextWindow",) in current and window != self.initial_values.get(("contextWindow",)):
                if not window:
                    data.pop("contextWindow", None)
                elif not window.isdigit() or int(window) <= 0:
                    raise ValueError("contextWindow: " + tr("a number is expected"))
                else:
                    data["contextWindow"] = int(window)
            key = current.get(("apiKey",), "")
            if ("apiKey",) in current and key != self.initial_values.get(("apiKey",)):
                if not key:
                    data.pop("apiKey", None)
                elif key != servers.MASK:
                    data["apiKey"] = key
        except ValueError as exc:
            self.status.config(text=str(exc), fg=self.col["red"])
            return False
        old_name, name = self.initial_values.get(("name",)), str(current.get(("name",), "")).strip()
        if servers.access_of(data) == "llm" and old_name and name != old_name:
            servers.rename_result(old_name, name)
        return True

    # The model rows of the shown provider.
    def collect_models(self, data, current):
        """A model that was changed and is not in the list of the source is an own entry (customModel); a class given a model gets an effort when it has none."""
        for cls in ("light", "medium", "heavy"):
            model = str(current.get(("classes", cls, "model"), "") or "")
            if model == self.initial_values.get(("classes", cls, "model")) and current.get(("classes", cls, "effort")) == self.initial_values.get(("classes", cls, "effort")):
                continue
            entry = (data.get("classes") or {}).get(cls)
            if not isinstance(entry, dict) or not model:
                continue
            if not entry.get("effort"):
                entry["effort"] = "medium" if "medium" in self.effort_values or not self.effort_values else self.effort_values[0]
            if model != self.initial_values.get(("classes", cls, "model")) and not self.offered_own_only:
                if model in self.offered_models:
                    entry.pop("customModel", None)
                else:
                    entry["customModel"] = True

    # The Add button of the left list.
    def add_entry(self):
        """Add a provider on the tab Providers, a Project on the tab Projects."""
        if self.tab_var.get() == "providers":
            self.add_provider()
        elif self.tab_var.get() == "apiaries":
            self.add_apiary()

    # Add an Apiary.
    def add_apiary(self):
        """Append a new Project (name and workspace in config.toml; its project.toml is created at Save) and show it."""
        if self.collect():
            self.data.setdefault("projects", []).append({"name": "new-project", "workspace": ""})
            self.fill_nodes()
            self.select(self.entries.index(("apiaries", len(self.data["projects"]) - 1)))

    # Remove the shown provider or Apiary.
    def remove_node(self):
        """Delete the shown provider or the entry of the shown Project in config.toml (not from the file until Save; the project.toml stays)."""
        kind, i = self.node
        if kind == "general" or i is None or (kind == "apiaries" and self.apiaries[i]["entry"] is None):
            return
        from . import gui_dialog
        question = {"providers": "Remove this provider from the settings?"}.get(kind, "Remove this Project from the settings?")
        if gui_dialog.ask(self.win, "HaiveQueen", tr(question), self.mono):
            if kind == "providers":
                del self.data["providers"][i]
            else:
                entry = self.apiaries.pop(i)["entry"]
                self.data["projects"] = [e for e in self.data["projects"] if e is not entry]
            self.fill_nodes()
            self.select(0)

    def apiary_writes(self) -> tuple:
        """([(project, path of its project.toml)] to write at Save, [problems]): a Project whose settings changed or that has no project.toml yet, in an existing workspace."""
        writes, problems = [], []
        registered = self.registered()
        for apiary in self.apiaries:
            row = registered.get(apiary["name"])
            workspace = str(row[0] if row and row[0] else (apiary["entry"] or {}).get("workspace") or "").strip()
            path = Path(workspace) / ".haivequeen" / "project.toml" if workspace and Path(workspace).is_dir() else None
            changed = json.dumps(apiary["file"], sort_keys=True) != apiary["saved"]
            if path is None:
                if changed:
                    problems.append(tr("%s: the repository or plans folder does not exist", apiary["name"]))
                continue
            if changed or not path.is_file():
                if not path.is_file():   # owner decision 741 (2): the worktree names of a new Apiary start with its name; an existing project.toml keeps its prefix
                    apiary["file"].setdefault("worktreePrefix", worktree_prefix((apiary["entry"] or {}).get("name") or apiary["name"]))
                problems += ["%s: %s" % (apiary["name"], text) for text in check(apiary["file"], machine=False)]
                writes.append((apiary, path))
        return writes, problems

    # Check and write the files.
    @filetxn.coordinated
    def save(self):
        """Collect the shown part, check everything, write config.toml and the project.toml of every changed or new Project and tell the main window; a problem is shown and nothing is written."""
        if not self.collect():
            return
        from . import servers
        for entry in self.data.get("providers") or []:
            servers.settle(entry)   # a provider that cannot run embedded Bees is saved as process
        for apiary in self.apiaries:
            self.adopt_workspace(apiary)   # a workspace typed just before Save: its project.toml is the base, never replaced by the edits alone
        writes, problems = self.apiary_writes()
        problems = check(self.data, True) + problems
        if problems:
            self.status.config(text="; ".join(problems)[:300], fg=self.col["red"])
            return
        source = getattr(self.data, "source", None)
        if source and source != (str(Path(self.config_path).resolve()), filetxn.read(self.config_path)):
            self.status.config(text=tr("Settings changed since loading; reopen settings before saving."), fg=self.col["red"])
            return
        from . import init
        old_data = load_migrated(self.config_path)
        init.register_listed(self.conn, self.data.get("projects"))   # an Apiary added on this tab is an Apiary from now on
        for apiary, path in writes:
            save_project_file(self.conn, path, apiary["file"])
            apiary.update(path=path, workspace=str(path.parent.parent), saved=json.dumps(apiary["file"], sort_keys=True), exists=True)
        # Write provider removals last: a failed project write must not discard grants.
        save_file(self.config_path, self.data)
        self.test_servers(old_data)
        self.status.config(text="")
        self.bottom_note.config(text=tr("Saved."), fg=self.col["green"])   # in the bottom line beside the buttons, until another page is shown
        self.fresh = []
        self.refresh_state()
        if self.on_saved:
            self.on_saved()

    # Test the servers the save touched.
    def test_servers(self, old_data):
        """After a save: test, in the background, every Local LLM provider that is new or changed; the result is kept for the provider form."""
        import threading
        from . import servers
        new_data = self.data
        threading.Thread(target=servers.in_thread(lambda: servers.test_after_save(old_data, new_data)), daemon=True).start()

    # Open the settings folder.
    def open_folder(self):
        """Open the folder of the shown settings in the file manager: that of the project.toml on a Project page, else that of config.toml."""
        folder = self.config_path.parent
        if self.node[0] == "apiaries" and self.node[1] is not None and self.apiaries[self.node[1]]["path"] and self.apiaries[self.node[1]]["path"].parent.is_dir():
            folder = self.apiaries[self.node[1]]["path"].parent
        if os.name == "nt":
            os.startfile(str(folder))
