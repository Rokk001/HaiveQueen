"""Shared provider labels and readiness helpers for browser settings."""

from . import cli_presets
from .i18n import N_

CLASSES = ("light", "medium", "heavy")
ACCESS = (("subscription", N_("Subscription")), ("api", N_("API key")), ("llm", N_("Local LLM")))   # (key, label) of the access types, in the order of the dropdown
TYPE_FIELDS = ("apiKey", "address", "contextWindow")   # the entry values that belong to one access type only
# (label, explanation) of every field of the two pages, by the key the page uses.
TEXT = {
    "enabled": (N_("Enabled"), N_("A disabled provider gets no new work and shows nowhere in the dashboard; its settings stay.")),
    "name": (N_("Name"), N_("How this provider is called in HaiveQueen; it follows the CLI and the access type until you change it yourself.")),
    "cli": (N_("CLI"), N_("The program HaiveQueen starts for the agents of this provider.")),
    "protocol": (N_("Protocol"), N_("How HaiveQueen talks to your own CLI and reads its output: claude, codex, opencode or kilo.")),
    "command": (N_("Command"), N_("The command that starts your own CLI.")),
    "outputFormat": (N_("Output format"), N_("Which output format your own CLI writes: claude, codex or opencode.")),
    "loginCommand": (N_("Login command"), N_("The command that makes the login of your own CLI.")),
    "loginFiles": (N_("Login files"), N_("The login files of your own CLI, relative to its profile folder, one per line.")),
    "profileEnv": (N_("Profile variables"), N_("Variables that point your own CLI to its profile folder; {home} stands for that folder.")),
    "access": (N_("Access type"), N_("How this provider reaches its models: with the login of a subscription, with an API key, or with a local LLM of your own.")),
    "login": (N_("Login"), N_("The login of the HaiveQueen profile of this CLI; use the Login button when it is missing.")),
    "light": (N_("Light"), N_("Model and effort for small tasks; leave the model empty and this provider does not offer this class.")),
    "medium": (N_("Medium"), N_("Model and effort for normal tasks; leave the model empty and this provider does not offer this class.")),
    "heavy": (N_("Heavy"), N_("Model and effort for the hardest tasks; leave the model empty and this provider does not offer this class.")),
    "contextLimit": (N_("Context limit"), N_("The context size in tokens at which an agent of this provider is asked to hand off; a limit of a model entry wins.")),
    "benchmark": (N_("Benchmark"), N_("Measures how much context an agent of this provider can use; only for a saved provider that is ready.")),
    "slots": (N_("Simultaneous agents"), N_("How many agents of this provider may work at the same time.")),
    "use": (N_("Priority"), N_("A preferred provider gets work first; a fallback provider only when the preferred ones are busy or not available.")),
    "capabilities": (N_("Capabilities"), N_("What this provider may be used for; a task only goes to a provider that has the capability it needs.")),
    "beeMode": (N_("Agent mode"), N_("Subagent is available for every provider: the Queen runs Agents inside her process when supported, otherwise as separate processes; Process: every Agent is a separate process and goes to the provider with the best reserve.")),
    "allowedTools": (N_("Commands allowed without asking"), N_("Commands agents may run without asking, one per line.")),
    "args": (N_("Arguments"), N_("The arguments HaiveQueen gives to the CLI, one per line; {model}, {effort} and the like are filled in at the start.")),
    "own": (N_("Own settings"), N_("Other settings of this provider exactly as they stand in config.toml; a new setting is written as typed.")),
    "address": (N_("Address"), N_("Where the local LLM answers, for example http://192.168.1.20:1234, without /v1.")),
    "apiKey": (N_("API key"), N_("The key of the local LLM; optional, shown masked and kept only in config.toml of this machine.")),
    "providerKey": (N_("API key"), N_("Your API key; shown masked, kept only in config.toml of this machine and handed only to the agents HaiveQueen starts.")),
    "contextWindow": (N_("Context window (tokens)"), N_("How many tokens the model of the local LLM can hold; empty means nothing is handed to the CLI.")),
    "test": (N_("Test connection"), N_("Checks the address, reads the model list and tests the interface each CLI needs; the result is kept.")),
}
SHARED_LOGIN = N_("This login is shared by all providers with this CLI.")
HANDLED = {"claudeTools", "contextLimit", "enabled", "name", "nameEdited", "access", "apiKey", "address", "contextWindow", "protocol", "command", "server", "slots", "use", "capabilities", "capabilitiesVersion", "beeMode", "allowedTools", "args",
           "limit", "classes", "profileEnv", "loginCommand", "loginFiles", "outputFormat"}


def provider_state(data) -> tuple:
    """(ready, what is missing) of the only provider of `data` as the loop evaluates it now (login or last connection test, models); reads files, so not on the UI thread."""
    from . import scheduler
    try:
        resolved = scheduler.get_providers(data)[0]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, str(exc)
    return bool(resolved["login"]), resolved["notReady"]


def saved_provider(config_path, entry) -> bool:
    """True when `entry` is in config.toml exactly as given; reads the file, so not on the UI thread."""
    from . import settings
    try:
        saved = settings.load_migrated(config_path)
    except Exception:
        return False
    return any(p == entry for p in saved.get("providers") or [])


def cli_presets_protocols() -> tuple:
    """The protocols a custom CLI can name."""
    return tuple(p["protocol"] for p in cli_presets.PRESETS.values())
