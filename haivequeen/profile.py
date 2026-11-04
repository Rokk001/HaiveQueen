"""The HaiveQueen profile: one folder in the data folder that every agent of every provider works with.

Each provider CLI is started so that it reads its user files (settings, instructions, memory, login) only from here, never from the
owner's private folders. The folder holds only what a CLI needs to run: its login (made once by the owner) and the few settings
HaiveQueen writes. Memory is switched off, session logs older than a week are removed. Nothing is ever copied from a private folder.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import config, db, runtime
from . import platform as osplat

GLOBAL = "*"
KEEP_DAYS = 8   # session logs stay this long (the usage table reads them), then they are removed
NO_CHECK_ENV = "BZZ_NO_LOGIN_CHECK"   # tests only: every provider counts as logged in

# Per protocol: the environment that points the CLI to its folder ({home} = <profile>/<protocol>), the login files (relative to {home}),
# the settings files HaiveQueen writes (text), the session log folders (relative to {home}) and the command that makes the login.
SPECS = {
    "claude": {
        "env": {"CLAUDE_CONFIG_DIR": "{home}", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
        "login": [".credentials.json"],
        "settings": {"settings.json": json.dumps({"autoMemoryEnabled": False}, indent=1) + "\n"},
        "sessions": ["projects"],
        "loginArgs": "auth login",
    },
    "codex": {
        "env": {"CODEX_HOME": "{home}"},
        "login": ["auth.json"],
        "settings": {"config.toml": '[history]\npersistence = "none"\n\n[features]\nmemories = false\n'},
        "sessions": ["sessions"],
        "loginArgs": "login",
    },
    "opencode": {
        "env": {"OPENCODE_CONFIG_DIR": "{home}/config", "XDG_CONFIG_HOME": "{home}/config", "XDG_DATA_HOME": "{home}/data",
                "XDG_STATE_HOME": "{home}/state", "XDG_CACHE_HOME": "{home}/cache", "OPENCODE_DISABLE_CLAUDE_CODE": "1"},
        "login": ["data/opencode/auth.json"],
        "settings": {},
        "sessions": ["data/opencode/storage"],
        "loginArgs": "auth login",
    },
    # Kilo Code (official docs: `kilo run`, `kilo auth login`; official source packages/core/src/global.ts: XDG folders below "kilo", KILO_CONFIG_DIR moves the
    # config folder, credentials in <data>/auth.json, sessions in <data>/kilo.db which must never be pruned, logs in <data>/log).
    "kilo": {
        "env": {"KILO_CONFIG_DIR": "{home}/config", "XDG_CONFIG_HOME": "{home}/config", "XDG_DATA_HOME": "{home}/data",
                "XDG_STATE_HOME": "{home}/state", "XDG_CACHE_HOME": "{home}/cache"},
        "login": ["data/kilo/auth.json"],
        "settings": {},
        "sessions": ["data/kilo/log"],
        "loginArgs": "auth login",
    },
}

# The output formats the agent runner can read, and which one each built-in protocol writes. Kilo Code is a fork of OpenCode and writes the same JSONL events.
FORMAT_NAMES = ("claude", "codex", "opencode")
FORMATS = {"claude": "claude", "codex": "codex", "opencode": "opencode", "kilo": "opencode"}


def check_protocol(name, proto, declared=None) -> str:
    """Return the output format of a provider entry: the one of a built-in protocol, else the `outputFormat` the entry declares.
    Raises ValueError with a clear message when a custom CLI names no output format HaiveQueen understands."""
    fmt = FORMATS.get(str(proto)) or (str(declared) if str(declared) in FORMAT_NAMES else "")
    if not fmt:
        raise ValueError("provider %s: protocol '%s' is not supported. A custom CLI is accepted only when it writes one of the output formats HaiveQueen "
                         "understands; set outputFormat to one of: %s" % (name, proto, ", ".join(FORMAT_NAMES)))
    return fmt


def profile_dir() -> Path:
    """Return the profile folder (inside the HaiveQueen data folder)."""
    return config.data_dir() / "profile"


def custom_fields(p) -> dict:
    """Return the three values of a provider entry of config.toml that describe the profile of a CLI that is not built in
    (`profileEnv`, `loginCommand`, `loginFiles`), with empty defaults."""
    return {"profileEnv": dict(p.get("profileEnv") or {}), "loginCommand": str(p.get("loginCommand") or ""),
            "loginFiles": [str(f) for f in (p.get("loginFiles") or [])]}


def custom_spec(prov):
    """Return the profile description that a provider entry brings for a CLI that is not built in (`profileEnv`, `loginFiles`, `loginCommand`),
    or None when the entry has no protocol name, no variables or no login files."""
    env, files = (prov or {}).get("profileEnv"), (prov or {}).get("loginFiles")
    if not str((prov or {}).get("protocol") or "") or not isinstance(env, dict) or not env or not isinstance(files, list) or not files:
        return None
    return {"env": {str(k): str(v) for k, v in env.items()}, "login": [str(f) for f in files], "settings": {}, "sessions": [], "loginArgs": "",
            "loginCommand": str(prov.get("loginCommand") or "").strip()}


def spec(prov):
    """Return the profile description of a provider's protocol (a built-in CLI first, else the values of the entry itself), or None when the CLI cannot be kept to the profile."""
    return SPECS.get(str((prov or {}).get("protocol") or "")) or custom_spec(prov)


def provider_home(prov) -> Path:
    """Return the folder of a provider's CLI inside the profile."""
    return profile_dir() / re.sub(r"[^a-z0-9]+", "-", str(prov.get("protocol") or "").lower()).strip("-")


def confined(prov) -> bool:
    """True when the provider's CLI can be kept to the profile."""
    return spec(prov) is not None


def environment(prov, base=None) -> dict:
    """Return the environment for the provider's CLI: `base` with the variables that point it to the profile folder."""
    env = dict(os.environ if base is None else base)
    s = spec(prov)
    if s:
        home = str(provider_home(prov))
        for key, value in s["env"].items():
            env[key] = value.replace("{home}", home)
    return env


def prepare(prov, now=None) -> bool:
    """Create the provider's profile folder with its settings and remove old session logs; False when the CLI cannot be kept to the profile."""
    s = spec(prov)
    if not s:
        return False
    home = provider_home(prov)
    home.mkdir(parents=True, exist_ok=True)
    for name, text in s["settings"].items():
        path = home / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
    limit = (now if now is not None else time.time()) - KEEP_DAYS * 86400
    for sub in s["sessions"]:
        for f in (home / sub).rglob("*") if (home / sub).is_dir() else []:
            try:
                if f.is_file() and f.stat().st_mtime < limit:
                    f.unlink()
            except OSError:
                pass
    return True


def sessions_dir(protocol: str) -> Path:
    """Return the session log folder of a protocol inside the profile."""
    return provider_home({"protocol": protocol}) / SPECS[protocol]["sessions"][0]


def has_login(prov) -> bool:
    """True when the provider has its login in the profile (a provider that cannot be kept to the profile has none)."""
    if os.environ.get(NO_CHECK_ENV):
        return True
    s = spec(prov)
    return bool(s) and any((provider_home(prov) / f).is_file() for f in s["login"])


def login_command(prov) -> str:
    """Return the command line that makes the provider's login ('' when the CLI cannot be kept to the profile or an own CLI has no login command)."""
    s = spec(prov)
    if not s:
        return ""
    if "loginCommand" in s:
        return s["loginCommand"]
    return str(prov.get("command") or prov.get("protocol")) + " " + s["loginArgs"]


def login_text(prov) -> str:
    """Return the one-line instruction that tells the owner how to log in to the provider in the profile."""
    s = spec(prov)
    if not s:
        return "Provider %s cannot be kept to the HaiveQueen profile." % prov.get("name")
    env = "; ".join("%s=%s" % (k, v.replace("{home}", str(provider_home(prov)))) for k, v in s["env"].items() if "{home}" in v)
    return ("Provider %s needs its login in the HaiveQueen profile (once). Run `%s` with the environment %s. "
            "Its packages wait until then, all others go on." % (prov.get("name"), login_command(prov) or "its login command", env))


# Terminal programs tried on Linux, each with the argument that precedes the command to run.
TERMINALS = (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"), ("xterm", "-e"))


def login_run(prov, popen=subprocess.Popen):
    """Open a visible terminal window in which the profile variables are set and the provider's login command runs; the window stays open afterwards.
    Returns the started process, or None when the CLI cannot be kept to the profile, has no login command or no terminal program is found."""
    command = login_command(prov)
    if not command or not prepare(prov):
        return None
    env = environment(prov)
    if osplat.is_windows():
        return popen(["cmd.exe", "/k", command], env=env, creationflags=0x00000010)   # CREATE_NEW_CONSOLE
    exports = "".join("export %s=%s; " % (k, shlex.quote(env[k])) for k in spec(prov)["env"])   # a terminal server may not hand on the environment
    for terminal, flag in TERMINALS:
        if shutil.which(terminal):
            return popen([terminal, flag, "bash", "-c", exports + command + "; exec bash"], env=env)
    return None


def notify_missing(conn, providers) -> list:
    """Tell the owner once per provider (global event) that its login is missing; the mark is cleared when the login exists. Returns the names told now."""
    told = []
    for p in providers:
        key = "login-notice-" + p["name"]
        if has_login(p):
            if runtime.kv_get(conn, GLOBAL, key):
                runtime.kv_set(conn, GLOBAL, key, "")
            continue
        if p.get("slots", 1) <= 0 or runtime.kv_get(conn, GLOBAL, key):
            continue
        with db.transaction(conn):
            db.add_event(conn, GLOBAL, "warning", login_text(p))
        runtime.kv_set(conn, GLOBAL, key, "told")
        told.append(p["name"])
    return told


# ---------------------------------------------------------------- what Queen and Bee load

RULE_FILES = ("AGENTS.md", "CLAUDE.md")   # the instruction files of a repository that count as project rules (nothing else does, the README never)
OWN_FILES = {"claude": ("CLAUDE.md",), "codex": ("AGENTS.md",), "opencode": ("AGENTS.md",)}   # per protocol: the instruction files its CLI loads by itself from its folder
UPSTREAM_KEY = "upstreamRepo"   # setting of the Apiary (project.toml): folder of the upstream repository whose instruction files are project rules too


def tracked(repo, name) -> bool:
    """True when `name` is a file checked in to the git repository at `repo` (an untracked or internal workspace file is not)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", name], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and (Path(repo) / name).is_file()


def project_rules(settings, repo, protocol=None, cwd=None) -> dict:
    """The complete project rules: the instruction files checked in to the project repository `repo` plus those of the upstream repository when the
    Apiary's settings name one (`upstreamRepo`); without that setting there are none. Returns {"inline": [(label, text)], "byProvider": [label]}.
    A file that the provider loads by itself from `cwd` (checked in there) is listed under byProvider and not copied again; files with the same text count once."""
    sources = [("", Path(repo))] if repo else []
    up = str((settings or {}).get(UPSTREAM_KEY) or "").strip()
    if up:
        sources.append(("upstream ", Path(up)))
    inline, by_provider, seen = [], [], set()
    for prefix, root in sources:
        for name in RULE_FILES:
            if not tracked(root, name):
                continue
            text = (root / name).read_text(encoding="utf-8-sig").strip()
            label = prefix + name
            if not text or text in seen:
                continue
            seen.add(text)
            if not prefix and cwd and name in OWN_FILES.get(str(protocol or ""), ()) and tracked(cwd, name):
                by_provider.append(label)
            else:
                inline.append((label, text))
    return {"inline": inline, "byProvider": by_provider}


def rules_text(rules) -> str:
    """The project rules as one text for the system prompt or the prompt file; empty when nothing is to be copied."""
    if not rules["inline"]:
        return ""
    return "# Project rules\n\n" + "\n\n".join("## %s\n\n%s" % (label, text) for label, text in rules["inline"]) + "\n"


def section(text, heading_part) -> str:
    """The markdown section whose heading contains `heading_part` (up to the next heading of the same or a higher level), or ''."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^(#+)\s", ln)
        if m and heading_part.lower() in ln.lower():
            end = len(lines)
            for j in range(i + 1, len(lines)):
                n = re.match(r"^(#+)\s", lines[j])
                if n and len(n.group(1)) <= len(m.group(1)):
                    end = j
                    break
            return "\n".join(lines[i:end]).strip()
    return ""


def queen_load(conn, project, workspace, plan, predecessor=None) -> str:
    """What a Queen loads besides her role file and the project rules: the plan, the plan's state file, the handoff of her predecessor (if any), and the
    decisions and the owner answers of her Apiary. Nothing else."""
    from . import plans
    base = Path(workspace)
    plan_file = base / plan
    state_file = base / plans.state_file_path(plan)
    parts = []

    def read(path):
        try:
            return path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            return ""
    state = read(state_file)
    handoff = section(state, "handoff " + str(predecessor)) if predecessor and state else ""
    if handoff:
        state = state.replace(handoff, "").strip()
    parts.append("# Plan %s\n\n%s" % (plan, read(plan_file)))
    parts.append("# State file %s\n\n%s" % (state_file.name, state or "(none yet)"))
    if predecessor:
        parts.append("# Handoff of your predecessor %s\n\n%s" % (predecessor, handoff or "(none written)"))
    decisions = runtime.list_decisions(conn, project, active_only=True)
    parts.append("# Decisions of this Apiary\n\n" + ("\n".join("- #%s%s: %s" % (d["id"], " (plan %s)" % d["plan_path"] if d.get("plan_path") else "", d["text"]) for d in decisions) or "(none)"))
    answered = [q for q in runtime.list_questions(conn, project, status="answered")]
    parts.append("# Owner answers of this Apiary\n\n" + ("\n".join("- #%s%s: %s -> %s" % (q["id"], " (plan %s)" % q["plan_path"] if q.get("plan_path") else "", q["question"], q["answer"]) for q in answered) or "(none)"))
    return "\n\n".join(parts) + "\n"
