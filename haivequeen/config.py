"""Configuration: machine settings (config.toml), project settings (project.toml) and folder lookup."""
import tomllib
from pathlib import Path

from haivequeen import platform as osplat
from . import filetxn

APP = "HaiveQueen"

DEFAULT_CONFIG = {
    "providers": [],
    "slots": 2,
    "weeklyLimitSources": [],
    "buildJobs": 0,
    "ramReserveMb": 2048,
    "exportDir": "",
    "projects": [],
    "retention": {"logsDays": 30, "runsDays": 30, "databaseDays": 90, "inboxDays": 30},
    "update": {"checkHours": 24, "repository": ""},
}

DEFAULT_CLAUDE_TOOLS = "Read,Write,Edit,Bash,PowerShell,Grep,Glob,Monitor,TaskStop"   # tools of a Claude agent when its provider sets none

DEFAULT_PROJECT = {
    "name": "",
    "colour": "white",
    "priority": 0,
    "planGlobs": ["docs/plans/*.md"],
    "planExclude": [],
}


def config_dir() -> Path:
    """Return the settings folder (BZZ_CONFIG_DIR overrides the per-system default, see haivequeen.platform)."""
    return osplat.config_dir()


def data_dir() -> Path:
    """Return the machine data folder (BZZ_DATA_DIR overrides the per-system default, see haivequeen.platform)."""
    return osplat.data_dir()


def db_path() -> Path:
    """Return the path of the central database file."""
    return data_dir() / "haivequeen.db"


def _merge(defaults: dict, loaded: dict) -> dict:
    """Return defaults overlaid with loaded values (nested dicts are merged key by key)."""
    out = {}
    for key, value in defaults.items():
        if isinstance(value, dict) and isinstance(loaded.get(key), dict):
            out[key] = _merge(value, loaded[key])
        else:
            out[key] = loaded.get(key, value)
    for key, value in loaded.items():
        out.setdefault(key, value)
    return out


@filetxn.coordinated
def _load_toml(path: Path) -> dict:
    """Read a TOML file; a missing file gives an empty dict."""
    contents = filetxn.read(path)
    return tomllib.loads(contents.decode("utf-8")) if contents is not None else {}


@filetxn.coordinated
def load_config() -> dict:
    """Load config.toml from the settings folder, filling in defaults."""
    from . import servers
    return servers.migrate(_merge(DEFAULT_CONFIG, _load_toml(config_dir() / "config.toml")))   # old endpoint entries are read in the new form; the file stays as it is until the owner saves


@filetxn.coordinated
def load_project(workspace) -> dict:
    """Load <workspace>/.haivequeen/project.toml, filling in defaults (name falls back to the folder name)."""
    ws = Path(workspace)
    data = _merge(DEFAULT_PROJECT, _load_toml(ws / ".haivequeen" / "project.toml"))
    if data.get("repo") and not data.get("codeRepo"):   # project files written by the v1 migration name the code repo `repo`
        data["codeRepo"] = data["repo"]
    from . import projsettings
    if data.get("plans"):   # the field `plans` (a line starting with ! excludes) wins over the old keys
        data["planGlobs"], data["planExclude"] = projsettings.plans_to_globs(data)
    if not data["name"]:
        data["name"] = ws.name
    return data

def git_credential(provider):
    """Read a provider-wide credential from the user's settings directory."""
    import json
    try:
        path = config_dir() / 'git-credentials.json'
    except RuntimeError:
        return None
    contents = filetxn.read(path)
    return json.loads(contents.decode('utf-8')).get(provider) if contents else None


@filetxn.coordinated
def save_git_credential(provider, token, method='token', origin=None):
    """Store or remove a provider credential without touching a repository."""
    import json
    if provider not in ('github', 'gitlab', 'gitea', 'bitbucket', 'azure'):
        raise ValueError('unknown Git provider')
    if token is not None and (not isinstance(token, str) or not token or any(c.isspace() for c in token)):
        raise ValueError('invalid token')
    if not isinstance(method, str) or not method:
        raise ValueError('invalid authentication method')
    path = config_dir() / 'git-credentials.json'
    contents = filetxn.read(path)
    credentials = json.loads(contents.decode('utf-8')) if contents else {}
    if token is None:
        credentials.pop(provider, None)
    else:
        credentials[provider] = {'token': token, 'method': method}
        if origin is not None:
            credentials[provider]['origin'] = origin.rstrip('/')
    filetxn.atomic_write(path, json.dumps(credentials).encode('utf-8'))
