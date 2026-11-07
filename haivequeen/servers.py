"""Own LLM servers: server entries of config.toml, handing a server to a built-in CLI, the connection test and the ready check of a provider.

A server entry (`[[servers]]` in config.toml) has a name, an address (for example http://192.168.1.20:1234, without /v1), an optional API key and an
optional context window in tokens. A provider names its server in the field `server`; HaiveQueen then starts the built-in CLI with the address of the
server itself, only through the environment and arguments of the agent it starts (never in the user's own profile and with no wrapper script).
The values each CLI takes come from its official documentation (sources: docs/PROFILE.md, section "Own LLM server").
"""
import copy
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import config
from .i18n import tr

PLACEHOLDER_KEY = "lmstudio"   # handed over where a CLI needs a value although the server has no API key (as in the LM Studio documentation)
KEY_ENV = "HAIVEQUEEN_SERVER_KEY"   # variable through which Codex reads the key (the key never stands in an argument list)
TEST_FILE = "server-tests.json"
TIMEOUT_S = 5.0
CLI_KEYS = ("claude", "codex", "opencode", "kilo")
# What each built-in CLI needs from a server: (label, path of the interface, what it is).
INTERFACES = {
    "claude": ("Claude Code", "/v1/messages"),
    "codex": ("Codex CLI", "/v1/responses"),
    "opencode": ("OpenCode", "/v1/chat/completions"),
    "kilo": ("Kilo Code", "/v1/chat/completions"),
}
MASK = "********"


# ---------------------------------------------------------------- server entries

def clean_address(text) -> str:
    """The address of a server without spaces, trailing slashes and a trailing /v1 (so /v1 is added once, never twice)."""
    value = str(text or "").strip().rstrip("/")
    if value.lower().endswith("/v1"):
        value = value[:-3].rstrip("/")
    return value


def server_id(name) -> str:
    """The identifier of a server in a CLI's own configuration: the name in lower case letters, digits and hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "server"


def window_of(entry) -> int:
    """The context window of a server entry as a whole number of tokens (0 = not set)."""
    try:
        value = int(entry.get("contextWindow") or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def get_servers(cfg) -> list:
    """The server entries of a settings dict as dicts {name, address, apiKey, contextWindow} (old endpoint entries are read in the new form first).
    A server without a name or address raises ValueError."""
    out = []
    for s in (migrate(cfg).get("servers") or []):
        name = str(s.get("name") or "").strip()
        address = clean_address(s.get("address"))
        if not name:
            raise ValueError(tr("a server entry has no name"))
        if not address:
            raise ValueError(tr("server %s has no address", name))
        out.append({"name": name, "address": address, "apiKey": str(s.get("apiKey") or ""), "contextWindow": window_of(s)})
    names = [s["name"] for s in out]
    if len(set(names)) != len(names):
        raise ValueError(tr("server names must be unique"))
    return out


def get_server(cfg, name):
    """The server entry with that name or None."""
    return next((s for s in get_servers(cfg) if s["name"] == name), None)


def mask_key(key) -> str:
    """The API key as it is shown on the page: nothing for an empty key, else the same mask of fixed length (the length of a key is not shown)."""
    return MASK if str(key or "") else ""


def providers_using(cfg, name) -> list:
    """The names of the providers that use the server."""
    return [str(p.get("name")) for p in cfg.get("providers") or [] if p.get("server") == name]


def is_local(p) -> bool:
    """True for a provider with an own server or an endpoint: it counts as local (Bees of every Hive first, a Queen only when ticked)."""
    return bool(p.get("server") or p.get("endpoint") or p.get("local"))


def remove_server(cfg, name) -> None:
    """Remove the server from the settings dict; ValueError naming the providers when a provider uses it (nothing is removed then)."""
    used = providers_using(cfg, name)
    if used:
        raise ValueError(tr("Server %s is used by: %s", name, ", ".join(used)))
    cfg["servers"] = [s for s in cfg.get("servers") or [] if s.get("name") != name]
    forget(name)


def rename_server(cfg, old, new) -> None:
    """Rename the server in the settings dict and in the providers that use it (provider names stay). ValueError for an empty or taken name."""
    new = str(new or "").strip()
    if not new:
        raise ValueError(tr("a server entry has no name"))
    if new == old:
        return
    if any(s.get("name") == new for s in cfg.get("servers") or []):
        raise ValueError(tr("server names must be unique"))
    for s in cfg.get("servers") or []:
        if s.get("name") == old:
            s["name"] = new
    for p in cfg.get("providers") or []:
        if p.get("server") == old:
            p["server"] = new
    results = _read_tests()
    if old in results:
        results[new] = results.pop(old)
        _write_tests(results)


def set_source(entry, server=None, new=False) -> None:
    """Switch a provider entry between account login (server None) and an own server. The capability ticks stay as they are (an entry from before the
    ticks keeps what it offers now); the usage check is set to none only for a new provider (`new`) that gets a server, never for an existing one."""
    from . import scheduler
    if not entry.get("capabilitiesVersion"):
        entry["capabilities"] = scheduler.provider_capabilities(entry)
        entry["capabilitiesVersion"] = 1
    if server:
        entry["server"] = server
        if new:
            entry["limit"] = {"source": "none"}
    else:
        entry.pop("server", None)


# ---------------------------------------------------------------- migration of old endpoint entries

def _is_codex(entry) -> bool:
    """True for a provider entry of the built-in Codex CLI (not a custom CLI)."""
    from . import cli_presets
    return cli_presets.key_for(entry) == "codex"


def migrate(cfg) -> dict:
    """The settings dict in the new form: a Codex CLI provider with `endpoint` becomes a provider with `server`, with a server entry made from its
    baseUrl (address without /v1, name = endpoint name of the first such provider, else the provider name; a second different address with a taken name
    gets a number; the same address shares one entry). Other providers keep their endpoint untouched. The dict that is passed in is not changed; the
    same dict is returned when nothing is to be migrated."""
    providers = cfg.get("providers") or []
    todo = [p for p in providers if isinstance(p.get("endpoint"), dict) and p["endpoint"].get("baseUrl") and not p.get("server") and _is_codex(p)]
    if not todo:
        return cfg
    out = copy.deepcopy(cfg)
    servers = out.setdefault("servers", [])
    for p in out["providers"]:
        ep = p.get("endpoint")
        if not (isinstance(ep, dict) and ep.get("baseUrl") and not p.get("server") and _is_codex(p)):
            continue
        address = clean_address(ep["baseUrl"])
        found = next((s for s in servers if clean_address(s.get("address")) == address), None)
        if not found:
            base = str(ep.get("name") or p.get("name") or "server")
            name, n = base, 1
            while any(s.get("name") == name for s in servers):
                n += 1
                name = "%s-%d" % (base, n)
            found = {"name": name, "address": address}
            servers.append(found)
        p["server"] = found["name"]
        p.pop("endpoint", None)
        p.setdefault("nameEdited", True)
    return out


# ---------------------------------------------------------------- handing a server to a CLI

def cli_model(prov, model) -> str:
    """The model name as the CLI takes it: OpenCode and Kilo name a model `<provider id>/<model>`, the other CLIs take it as it is."""
    server = prov.get("server")
    if server and prov.get("protocol") in ("opencode", "kilo") and model and not str(model).startswith(server_id(server["name"]) + "/"):
        return "%s/%s" % (server_id(server["name"]), model)
    return model


def _class_models(prov) -> list:
    """The distinct model names of the classes of a provider, in class order."""
    out = []
    for cls in ("light", "medium", "heavy"):
        m = str(((prov.get("classes") or {}).get(cls) or {}).get("model") or "")
        if m and m not in out:
            out.append(m)
    return out


def _opencode_config(prov, server) -> dict:
    """The inline configuration for OpenCode and Kilo Code: an OpenAI-compatible provider with the address plus /v1, the models of the classes and
    the context limit. Both spellings of the provider block are written: `provider`/`npm`/`options` (OpenCode 1.x and Kilo Code) and `providers`/`package`/`settings`
    (current OpenCode documentation); a CLI reads the one it knows."""
    window = window_of(server)
    models = {}
    for m in _class_models(prov):
        entry = {"name": m}
        if window:
            entry["limit"] = {"context": window, "output": min(32000, max(1, window // 2))}
        models[m] = entry
    pid, key, url = server_id(server["name"]), server.get("apiKey") or PLACEHOLDER_KEY, server["address"] + "/v1"
    config_ = {"provider": {pid: {"npm": "@ai-sdk/openai-compatible", "name": server["name"], "options": {"baseURL": url, "apiKey": key}, "models": models}}}
    if prov.get("protocol") == "opencode":
        config_["providers"] = {pid: {"name": server["name"], "package": "@opencode/ai/providers/openai-compatible",
                                      "settings": {"baseURL": url, "apiKey": key}, "models": copy.deepcopy(models)}}
    return config_


def environment(prov) -> dict:
    """The variables a provider with an own server adds to the environment of its CLI (empty without a server; only the agent's environment, never a profile file)."""
    server = prov.get("server")
    if not server:
        return {}
    proto, key, window = prov.get("protocol"), server.get("apiKey") or PLACEHOLDER_KEY, window_of(server)
    if proto == "claude":
        env = {"ANTHROPIC_BASE_URL": server["address"], "ANTHROPIC_AUTH_TOKEN": key, "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}
        if window:
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(window)
        return env
    if proto == "codex":
        return {KEY_ENV: key}
    if proto in ("opencode", "kilo"):
        return {("OPENCODE" if proto == "opencode" else "KILO") + "_CONFIG_CONTENT": json.dumps(_opencode_config(prov, server), ensure_ascii=False)}
    return {}


def drop_variables(prov) -> list:
    """Variables of the user's environment that must not reach a server (a real Anthropic key would otherwise be sent to it)."""
    return ["ANTHROPIC_API_KEY"] if prov.get("server") and prov.get("protocol") == "claude" else []


def arguments(prov) -> list:
    """The arguments a provider with an own server adds to the command line: for Codex a model provider with the address plus /v1 and the context
    window (`-c key=value`); the other CLIs take the server through the environment."""
    server = prov.get("server")
    if not server or prov.get("protocol") != "codex":
        return []
    pid = server_id(server["name"])
    args = ["-c", "model_provider=" + pid, "-c", "model_providers.%s.name=%s" % (pid, server["name"]),
            "-c", "model_providers.%s.base_url=%s/v1" % (pid, server["address"]), "-c", "model_providers.%s.wire_api=responses" % pid,
            "-c", "model_providers.%s.env_key=%s" % (pid, KEY_ENV)]
    if window_of(server):
        args += ["-c", "model_context_window=%d" % window_of(server)]
    return args


# ---------------------------------------------------------------- kept connection test results

def tests_file():
    """The file with the kept results of the last test of every server (machine data folder, so it survives a restart and is not part of config.toml)."""
    return (_pinned.folder or config.data_dir()) / TEST_FILE


class _Pin(threading.local):
    folder = None


_pinned = _Pin()


def in_thread(func):
    """Return a function for a background thread that keeps the results in the data folder of this call ( whatever happens to the environment later)."""
    folder = config.data_dir()

    def run():
        _pinned.folder = folder
        func()
    return run


def _read_tests() -> dict:
    try:
        data = json.loads(tests_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_tests(data) -> None:
    path = tests_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def fingerprint(server) -> str:
    """A short hash of address and API key: a kept result belongs to exactly these two values (changing one of them clears it)."""
    return hashlib.sha256(("%s\n%s" % (clean_address(server.get("address")), server.get("apiKey") or "")).encode("utf-8")).hexdigest()[:16]


def kept(server):
    """The kept result of the last test of this server, or None (no test yet, or address or API key changed since)."""
    result = _read_tests().get(server["name"])
    return result if isinstance(result, dict) and result.get("fingerprint") == fingerprint(server) else None


def forget(name) -> None:
    """Clear the kept result of a server."""
    results = _read_tests()
    if name in results:
        del results[name]
        _write_tests(results)


def _request(url, key, body=None, timeout=TIMEOUT_S):
    """(status, text) of one request; status None when the server does not answer."""
    headers = {"Authorization": "Bearer " + key, "x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode("utf-8"), headers=headers, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(1 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(1 << 16).decode("utf-8", "replace")
        except OSError:
            return exc.code, ""
    except (OSError, ValueError, urllib.error.URLError):
        return None, ""


def _model_ids(text) -> list:
    """The model ids of an OpenAI-style (`data`) or plain list answer."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list) and isinstance(data, dict):
        items = data.get("models")
    out = []
    for item in items if isinstance(items, list) else []:
        ident = item.get("id") or item.get("key") or item.get("name") if isinstance(item, dict) else item
        if isinstance(ident, str) and ident and ident not in out:
            out.append(ident)
    return out


def run_test(server, timeout=TIMEOUT_S, now=None) -> dict:
    """Test the connection of a server and keep the result: does the address answer, which models does it list, and for each built-in CLI does it offer
    the interface that CLI needs (Claude Code: /v1/messages, Codex: /v1/responses, OpenCode and Kilo Code: /v1/chat/completions). An interface counts as
    offered when its path answers anything but 404, 405 or 501 to an empty request (no model is loaded by the test). Returns the kept result."""
    key = server.get("apiKey") or PLACEHOLDER_KEY
    base = clean_address(server["address"])
    status, text = _request(base + "/v1/models", key, timeout=timeout)
    result = {"time": datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y-%m-%d %H:%M:%S"), "fingerprint": fingerprint(server),
              "reachable": status is not None, "status": status, "models": [], "interfaces": {}}
    if status is None:
        result["lines"] = [tr("The server does not answer at %s", base)]
    else:
        if status == 200:
            result["models"] = _model_ids(text)
        elif status in (401, 403):
            result["lines"] = [tr("The server answers but refuses the API key")]
        for cli, (label, path) in INTERFACES.items():
            code, _ = _request(base + path, key, body={}, timeout=timeout)
            ok = code is not None and code not in (404, 405, 501)
            result["interfaces"][cli] = {"ok": ok, "path": path}
        lines = result.get("lines", [])
        for cli, (label, path) in INTERFACES.items():
            if result["interfaces"][cli]["ok"]:
                lines.append(tr("%s: usable (%s)", label, path))
            else:
                lines.append(tr("%s: not usable, the server does not offer %s", label, path))
        result["lines"] = lines
    results = _read_tests()
    results[server["name"]] = result
    _write_tests(results)
    return result


def summary(server) -> str:
    """The text of the server page for the kept result: the time and one line per CLI; a hint when there is no result."""
    result = kept(server)
    if not result:
        return tr("Not tested yet")
    return "\n".join([tr("Tested at %s", result["time"])] + list(result.get("lines") or []))


# ---------------------------------------------------------------- ready check

def known_models(prov):
    """The models a provider may choose from, or None when they are not known: the model list of its server (last test); with account login the list of
    the CLI from the file `cli-models.json` of the data folder when the owner put one there (the shipped list only fills the choice on the provider page)."""
    server = prov.get("server")
    if server:
        result = kept(server)
        return list(result["models"]) if result and result.get("models") else None
    from . import cli_presets
    preset = cli_presets.PRESETS.get(prov.get("protocol"))
    if not preset or preset["command"] != prov.get("command"):
        return None   # a custom CLI offers only its own entry
    return cli_presets.model_list(prov["protocol"], shipped=False) or None   # only the list of the owner: the shipped list fills the choice on the page


def readiness(prov, has_login) -> tuple:
    """(ready, what is missing) of a provider (P-13): with account login the login must exist; with an own server the last test must show that the server
    answers and offers the interface of the CLI; every model of a class must be in the model list of its source, unless the class entry marks it as an
    own entry (`customModel = true`, an own entry counts as available), or the list is not known."""
    server = prov.get("server")
    if server:
        result = kept(server)
        label, path = INTERFACES.get(prov.get("protocol"), (prov.get("protocol"), ""))
        if not result:
            return False, tr("the connection to server %s has not been tested yet", server["name"])
        if not result.get("reachable"):
            return False, tr("server %s does not answer", server["name"])
        if prov.get("protocol") in INTERFACES and not (result.get("interfaces") or {}).get(prov["protocol"], {}).get("ok"):
            return False, tr("server %s does not offer %s, which %s needs", server["name"], path, label)
    elif not has_login:
        return False, tr("the login is missing")
    available = known_models(prov)
    if available is not None:
        for cls in ("light", "medium", "heavy"):
            entry = (prov.get("classes") or {}).get(cls) or {}
            model = str(entry.get("model") or "")
            if model and model not in available and not entry.get("customModel"):
                return False, tr("model %s is not in the model list", model)
    return True, ""


# ---------------------------------------------------------------- when the test runs

def needs_retest(prov) -> bool:
    """True when a provider is not ready because of the connection test (no result, server not answering or interface missing): the test is repeated once per round."""
    server = prov.get("server")
    if not server:
        return False
    result = kept(server)
    return not (result and result.get("reachable") and (result.get("interfaces") or {}).get(prov.get("protocol"), {}).get("ok", True))


def test_used(providers, only=None, timeout=TIMEOUT_S) -> list:
    """Test every server that a provider in `providers` uses (once each; only those named in `only` when given). Returns the names tested."""
    done = []
    for p in providers:
        server = p.get("server")
        if server and server["name"] not in done and (only is None or server["name"] in only):
            run_test(server, timeout=timeout)
            done.append(server["name"])
    return done


def retest_not_ready(providers, timeout=TIMEOUT_S) -> list:
    """Once per round: test again the servers of providers that are not ready because of the test."""
    names = {p["server"]["name"] for p in providers if needs_retest(p)}
    return test_used(providers, only=names, timeout=timeout)


def refresh_readiness(providers, has_login=None) -> None:
    """Recompute `login` (ready) and `notReady` of the providers with an own server in place (after a test; the list of a running loop is built once)."""
    for p in providers:
        if p.get("server"):
            ok, why = readiness(p, True)
            p["login"], p["notReady"] = ok, why


def test_after_save(old, new, timeout=TIMEOUT_S) -> list:
    """After the settings window saved: test every server of the saved settings that is new, whose address or key changed, or that a changed
    provider uses. Returns the names tested."""
    old_servers = {s["name"]: s for s in get_servers(old or {})}
    old_providers = {p.get("name"): p for p in (old or {}).get("providers") or []}
    names = set()
    for s in get_servers(new):
        before = old_servers.get(s["name"])
        if not before or fingerprint(before) != fingerprint(s):
            names.add(s["name"])
    for p in new.get("providers") or []:
        if p.get("server") and p != old_providers.get(p.get("name")):
            names.add(p["server"])
    tested = []
    for s in get_servers(new):
        if s["name"] in names:
            run_test(s, timeout=timeout)
            tested.append(s["name"])
    return tested
