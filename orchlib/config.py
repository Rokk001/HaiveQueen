"""Configuration: machine settings (config.toml), project settings (project.toml) and folder lookup."""
import tomllib
from pathlib import Path

from orchlib import platform as osplat

APP = "agent-orchestrator"

DEFAULT_CONFIG = {
    "providers": [],
    "slots": 2,
    "weeklyLimitSources": [],
    "buildJobs": 0,
    "ramReserveMb": 2048,
    "exportDir": "",
    "projects": [],
}

DEFAULT_PROJECT = {
    "name": "",
    "colour": "white",
    "priority": 0,
    "planGlobs": ["docs/plans/*.md"],
    "views": {"queue": "", "questions": "", "decisions": ""},
}


def config_dir() -> Path:
    """Return the settings folder (ORCH_CONFIG_DIR overrides the per-system default, see orchlib.platform)."""
    return osplat.config_dir()


def data_dir() -> Path:
    """Return the machine data folder (ORCH_DATA_DIR overrides the per-system default, see orchlib.platform)."""
    return osplat.data_dir()


def db_path() -> Path:
    """Return the path of the central database file."""
    return data_dir() / "orchestrator.db"


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


def _load_toml(path: Path) -> dict:
    """Read a TOML file; a missing file gives an empty dict."""
    if not path.is_file():
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def load_config() -> dict:
    """Load config.toml from the settings folder, filling in defaults."""
    return _merge(DEFAULT_CONFIG, _load_toml(config_dir() / "config.toml"))


def load_project(workspace) -> dict:
    """Load <workspace>/.orchestrator/project.toml, filling in defaults (name falls back to the folder name)."""
    ws = Path(workspace)
    data = _merge(DEFAULT_PROJECT, _load_toml(ws / ".orchestrator" / "project.toml"))
    if not data["name"]:
        data["name"] = ws.name
    return data
