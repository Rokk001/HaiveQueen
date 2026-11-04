"""The HaiveQueen profile: one folder in the data folder that every agent of every provider works with.

Each provider CLI is started so that it reads its user files (settings, instructions, memory, login) only from here, never from the
owner's private folders. The folder holds only what a CLI needs to run: its login (made once by the owner) and the few settings
HaiveQueen writes. Memory is switched off, session logs older than a week are removed. Nothing is ever copied from a private folder.
"""
import json
import os
import re
import time
from pathlib import Path

from . import config, db, runtime

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
}


def profile_dir() -> Path:
    """Return the profile folder (inside the HaiveQueen data folder)."""
    return config.data_dir() / "profile"


def spec(prov):
    """Return the profile description of a provider's protocol, or None when the CLI cannot be kept to the profile."""
    return SPECS.get(str((prov or {}).get("protocol") or ""))


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


def login_text(prov) -> str:
    """Return the one-line instruction that tells the owner how to log in to the provider in the profile."""
    s = spec(prov)
    if not s:
        return "Provider %s cannot be kept to the HaiveQueen profile." % prov.get("name")
    env = "; ".join("%s=%s" % (k, v.replace("{home}", str(provider_home(prov)))) for k, v in s["env"].items() if "{home}" in v)
    return ("Provider %s needs its login in the HaiveQueen profile (once). Run `%s` with the environment %s. "
            "Its packages wait until then, all others go on." % (prov.get("name"), str(prov.get("command") or prov.get("protocol")) + " " + s["loginArgs"], env))


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
