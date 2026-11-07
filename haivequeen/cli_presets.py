"""The CLIs a provider can be set to in the settings window. Choosing one sets every value HaiveQueen needs to start it; the owner can change the values afterwards.

Only CLIs that really run are listed: HaiveQueen reads their output (see `profile.FORMATS`), knows their profile variables, login command and login file
(`profile.SPECS`) and has a test for each. The values come from the official documentation or the official repository of each CLI (sources: docs/PROFILE.md).
The preset labels are product names and stay as they are in every language; only the custom entry is translated, and the logic works on the keys.
"""
from .i18n import N_, tr

CUSTOM = "custom"   # key of the entry for a CLI that is not in the list
CUSTOM_LABEL = N_("Custom CLI")
CUSTOM_KEYS = ("profileEnv", "loginCommand", "loginFiles", "outputFormat")   # entry fields of a custom CLI; a built-in CLI brings its own values

PRESETS = {
    "claude": {
        "label": "Claude Code", "protocol": "claude", "command": "claude", "beeMode": "subagent",
        "args": ["--output-format", "stream-json", "--verbose", "--model", "{model}", "--permission-mode", "auto", "--effort", "{effort}", "--add-dir", "{repo}",
                 "--add-dir", "{tools}", "--disable-slash-commands", "--strict-mcp-config", "--tools", "{claudeTools}", "--setting-sources", "",
                 "--append-system-prompt-file", "{rules}"],
    },
    "codex": {
        "label": "Codex CLI", "protocol": "codex", "command": "codex", "beeMode": "process",
        "args": ["exec", "--json", "--skip-git-repo-check", "-m", "{model}", "-c", "model_reasoning_effort={effort}", "--dangerously-bypass-approvals-and-sandbox",
                 "--ignore-user-config", "-C", "{cwd}"],
    },
    "opencode": {
        "label": "OpenCode", "protocol": "opencode", "command": "opencode", "beeMode": "process",
        "args": ["run", "--format", "json", "--auto", "--model", "{model}", "--dir", "{cwd}"],
    },
    "kilo": {
        "label": "Kilo Code", "protocol": "kilo", "command": "kilo", "beeMode": "process",
        "args": ["run", "--format", "json", "--auto", "--model", "{model}", "--dir", "{cwd}"],
    },
}


def labels() -> list:
    """The labels of the choice list in display order, the custom entry last."""
    return [p["label"] for p in PRESETS.values()] + [tr(CUSTOM_LABEL)]


def key_of_label(label) -> str:
    """The key of a choice label (the custom key for Custom CLI or an unknown label)."""
    return next((k for k, p in PRESETS.items() if p["label"] == label), CUSTOM)


def label_of(key) -> str:
    """The label of a choice key."""
    return PRESETS[key]["label"] if key in PRESETS else tr(CUSTOM_LABEL)


def key_for(entry) -> str:
    """The key of the CLI a provider entry is set to: the custom key when the entry carries the fields of an own CLI, else the preset whose protocol it has."""
    if any(field in entry for field in CUSTOM_KEYS):
        return CUSTOM
    proto = str(entry.get("protocol") or entry.get("name") or "")
    return next((k for k, p in PRESETS.items() if p["protocol"] == proto), CUSTOM)


def apply(entry, key) -> None:
    """Set the entry to the CLI `key`: protocol, command, arguments and Bee mode, and remove the custom-CLI fields (a built-in CLI brings its own).
    The custom key changes nothing. Provider name, slots, use, classes and capabilities stay as they are."""
    preset = PRESETS.get(key)
    if not preset:
        return
    for field in CUSTOM_KEYS:
        entry.pop(field, None)
    entry.update(protocol=preset["protocol"], command=preset["command"], args=list(preset["args"]), beeMode=preset["beeMode"])


# ---------------------------------------------------------------- models and effort values of each CLI (data file)

MODELS_FILE = "cli-models.json"   # the shipped file (official documentation, with its source per CLI); a file of the same name in the data folder replaces the entry of a CLI


def _models_entry(protocol, shipped=True) -> dict:
    """{models, efforts, source} of a CLI from the data folder (a list there means only the models) or else, with `shipped`, from the shipped file; empty for an unknown CLI."""
    import json
    from pathlib import Path
    from . import config
    for path in (config.data_dir() / MODELS_FILE, Path(__file__).with_name(MODELS_FILE))[:2 if shipped else 1]:
        try:
            entry = json.loads(path.read_text(encoding="utf-8")).get(protocol)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(entry, list):
            return {"models": [str(m) for m in entry], "efforts": []}
        if isinstance(entry, dict):
            return entry
    return {}


def model_list(protocol, shipped=True) -> list:
    """The models of a built-in CLI according to its documentation (empty when the documentation has no fixed list); with `shipped` False only a list
    the owner put in the data folder (the ready check uses that one, so a model the documentation does not list yet never stops a working provider)."""
    return [str(m) for m in _models_entry(protocol, shipped).get("models") or []]


def effort_list(protocol) -> list:
    """The effort values a built-in CLI accepts according to its documentation (empty when none are documented)."""
    return [str(e) for e in _models_entry(protocol).get("efforts") or []]
