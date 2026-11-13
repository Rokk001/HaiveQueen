"""Own LLM servers: server entries of config.toml, handing a server to a built-in CLI, the connection test and the ready check of a provider.

A server entry (`[[servers]]` in config.toml) has a name, an address (for example http://192.168.1.20:1234, without /v1), an optional API key and an
optional context window in tokens. A provider names its server in the field `server`; HaiveQueen then starts the built-in CLI with the address of the
server itself, only through the environment and arguments of the agent it starts (never in the user's own profile and with no wrapper script).
The values each CLI takes come from its official documentation (sources: docs/PROFILE.md, section "Own LLM server").
"""
from . import filetxn
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


ACCESS = ("subscription", "api", "llm")   # the access types of a provider (field `access` of its entry; absent = subscription)
# The environment variable through which each built-in CLI takes an API key. A CLI that is not listed cannot run with a single API key (OpenCode and Kilo
# Code read a key per model vendor) and is not offered for the access type API key.
API_KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "codex": "CODEX_API_KEY"}


def access_of(p) -> str:
    """The access type of a provider entry: subscription, api or llm (an entry that still names a server counts as llm)."""
    access = str(p.get("access") or "").lower()
    if access in ACCESS:
        return access
    return "llm" if p.get("server") else "subscription"


def bee_mode_of(p) -> str:
    """Return the selected Agent mode, with the existing provider default."""
    return str(p.get("beeMode") or ("subagent" if str(p.get("protocol") or p.get("name") or "") == "claude" else "process"))


def limit_source(p) -> str:
    """The limit source HaiveQueen derives from the CLI and the access type: Claude Code or Codex with Subscription reads its own usage (claude or
    codex); every other combination (API key, Local LLM, another CLI) reads none. A stored source is not used."""
    from . import cli_presets
    key = cli_presets.key_for(p)
    return key if access_of(p) == "subscription" and key in ("claude", "codex") else "none"


def settle(entry, new=False) -> None:
    """Write the derived settings into a provider entry: the Agent mode (see `bee_mode_of`) where the entry has one, and the limit source (see
    `limit_source`) where it has a limit or is new (`new`). The other settings of the limit table, the check interval, stay."""
    if "beeMode" in entry:
        entry["beeMode"] = bee_mode_of(entry)
    if "limit" in entry or new:
        limit = entry.get("limit")
        table = dict(limit) if isinstance(limit, dict) else {}
        table["source"] = limit_source(entry)
        entry["limit"] = table


def api_key_clis() -> list:
    """The keys of the built-in CLIs that can run with an API key."""
    return [k for k in CLI_KEYS if k in API_KEY_ENV]


def server_of(p):
    """The Local LLM of a provider entry as {name, address, apiKey, contextWindow} (name = provider name), or None for another access type."""
    if access_of(p) != "llm":
        return None
    return {"name": str(p.get("name") or ""), "address": clean_address(p.get("address")), "apiKey": str(p.get("apiKey") or ""), "contextWindow": window_of(p)}


def get_servers(cfg) -> list:
    """The Local LLMs of a settings dict (old [[servers]] entries and endpoints are read in the new form first) as dicts {name, address, apiKey, contextWindow},
    one per provider of access type llm; the name is the provider name."""
    return [server_of(p) for p in migrate(cfg).get("providers") or [] if access_of(p) == "llm"]


def get_server(cfg, name):
    """The Local LLM of the provider with that name or None."""
    return next((s for s in get_servers(cfg) if s["name"] == name), None)


def mask_key(key) -> str:
    """The API key as it is shown on the page: nothing for an empty key, else the same mask of fixed length (the length of a key is not shown)."""
    return MASK if str(key or "") else ""


def is_local(p) -> bool:
    """True for a provider with a Local LLM or an endpoint: it counts as local (Agents of every Plan first, a Queen only when ticked). An API key provider is not local."""
    return bool(p.get("server") or p.get("endpoint") or p.get("local") or access_of(p) == "llm")


def clear_type_values(entry) -> None:
    """Remove the values that belong to one access type only (key, address, context window) from a provider entry."""
    for field in ("apiKey", "address", "contextWindow", "server"):
        entry.pop(field, None)


def set_access(entry, access, new=False) -> None:
    """Set the access type of a provider entry. Subscription writes no `access` field. The capability ticks stay as they are (an entry from before the ticks
    keeps what it offers now); the limit source is derived again from the access type (see `settle`)."""
    from . import scheduler
    if not entry.get("capabilitiesVersion"):
        entry["capabilities"] = scheduler.provider_capabilities(entry)
        entry["capabilitiesVersion"] = 1
    entry.pop("server", None)
    if access == "subscription":
        entry.pop("access", None)
    else:
        entry["access"] = access
    settle(entry, new=new)


# ---------------------------------------------------------------- migration of old endpoint entries

def _is_codex(entry) -> bool:
    """True for a provider entry of the built-in Codex CLI (not a custom CLI)."""
    from . import cli_presets
    return cli_presets.key_for(entry) == "codex"


@filetxn.coordinated
def migrate(cfg) -> dict:
    """The settings dict in the new form: old endpoint entries and [[servers]] entries are taken over into the providers (see `fold_servers`).
    The dict that is passed in is not changed; the same dict is returned when nothing is to be migrated."""
    step = migrate_endpoints(cfg)
    return fold_servers(step)


def migrate_endpoints(cfg) -> dict:
    """A Codex CLI provider with `endpoint` becomes a provider with `server`, with a server entry made from its
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


def fold_servers(cfg) -> dict:
    """Take the [[servers]] entries over into the providers: every provider that names a server becomes a Local LLM provider (access llm) with the address, key and
    context window of that entry; an entry no provider uses becomes a Local LLM provider of the same name (a taken name gets a number) with 0 simultaneous
    agents and priority fallback, so it starts no agent. The key `servers` is gone from the result (so the next save removes it); nothing a user set is lost.
    The input is not changed; the same dict is returned when it has no `servers`."""
    if "servers" not in cfg:
        return cfg
    from . import cli_presets
    out = copy.deepcopy(cfg)
    entries = [s for s in out.pop("servers") or [] if isinstance(s, dict) and str(s.get("name") or "").strip()]
    providers = out.setdefault("providers", [])
    used = set()
    for p in providers:
        entry = next((s for s in entries if s.get("name") == p.get("server")), None)
        if entry is None:
            continue
        used.add(entry["name"])
        p.pop("server")
        p["access"] = "llm"
        fold_values(p, entry)
        _carry_test(entry["name"], p, entry)
    taken = {str(p.get("name")) for p in providers}
    for entry in entries:
        if entry["name"] in used:
            continue
        name, n = str(entry["name"]), 1
        while name in taken:
            n += 1
            name = "%s-%d" % (entry["name"], n)
        taken.add(name)
        p = {"name": name, "access": "llm", "slots": 0, "use": "fallback", "capabilities": ["Coding"], "capabilitiesVersion": 1, "nameEdited": True,
             "limit": {"source": "none"}, "classes": {}}
        cli_presets.apply(p, "claude")
        fold_values(p, entry)
        _carry_test(entry["name"], p, entry)
        providers.append(p)
    return out


@filetxn.coordinated
def _carry_test(old_name, p, entry=None) -> None:
    """Keep the last test result of a server entry for a provider of another name (the result is stored per name), so its last test and model list are not lost."""
    name = str(p.get("name") or "")
    if not name or name == old_name:
        return
    try:
        results = _read_tests()
        valid = entry is None or (results.get(old_name) or {}).get("fingerprint") == fingerprint(entry)   # a stale result (address or key changed since) is not carried
        if old_name in results and name not in results and valid:
            moved = dict(results[old_name])
            moved["fingerprint"] = fingerprint(p)
            results[name] = moved
            _write_tests(results)
    except Exception:
        pass


def fold_values(p, entry) -> None:
    """Copy address, key and context window of a server entry into a provider entry (the server entry wins, the provider named it as its server); other keys of
    the entry are kept under their own name unless the provider has them."""
    for key, value in entry.items():
        if key == "name":
            continue
        if key in ("address", "apiKey", "contextWindow"):
            p[key] = value
        else:
            p.setdefault(key, value)


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
    if prov.get("access") == "api":
        var = API_KEY_ENV.get(prov.get("protocol"))
        return {var: prov["apiKey"]} if var and prov.get("apiKey") else {}
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


@filetxn.coordinated
def _read_tests() -> dict:
    try:
        data = json.loads(filetxn.read(tests_file()) or b"{}")
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@filetxn.coordinated
def _write_tests(data) -> None:
    path = tests_file()
    filetxn.write(path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def fingerprint(server) -> str:
    """A short hash of address and API key: a kept result belongs to exactly these two values (changing one of them clears it)."""
    return hashlib.sha256(("%s\n%s" % (clean_address(server.get("address")), server.get("apiKey") or "")).encode("utf-8")).hexdigest()[:16]


def kept(server):
    """The kept result of the last test of this server, or None (no test yet, or address or API key changed since)."""
    result = _read_tests().get(server["name"])
    return result if isinstance(result, dict) and result.get("fingerprint") == fingerprint(server) else None


@filetxn.coordinated
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
    _keep_result(server["name"], result)
    return result


@filetxn.coordinated
def _keep_result(name, result):
    results = _read_tests()
    results[name] = result
    _write_tests(results)


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
    """(ready, what is missing) of a provider: with account login the login must exist; with an own server the last test must show that the server
    answers and offers the interface of the CLI; every model of a class must be in the model list of its source, unless the class entry marks it as an
    own entry (`customModel = true`, an own entry counts as available), or the list is not known."""
    server = prov.get("server")
    if prov.get("access") == "api":
        if not prov.get("apiKey"):
            return False, tr("the API key is missing")
        if prov.get("protocol") not in API_KEY_ENV:
            return False, tr("this CLI cannot run with an API key")
    elif server:
        if not clean_address(server.get("address")):
            return False, tr("the address of the Local LLM is missing")
        result = kept(server)
        label, path = INTERFACES.get(prov.get("protocol"), (prov.get("protocol"), ""))
        if not result:
            return False, tr("the connection to the Local LLM %s has not been tested yet", server["name"])
        if not result.get("reachable"):
            return False, tr("the Local LLM %s does not answer", server["name"])
        if prov.get("protocol") in INTERFACES and not (result.get("interfaces") or {}).get(prov["protocol"], {}).get("ok"):
            return False, tr("the Local LLM %s does not offer %s, which %s needs", server["name"], path, label)
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


@filetxn.coordinated
def rename_result(old, new) -> None:
    """Move the kept test result of a Local LLM to its new provider name."""
    results = _read_tests()
    if old != new and old in results:
        results[new] = results.pop(old)
        _write_tests(results)


def test_after_save(old, new, timeout=TIMEOUT_S) -> list:
    """After the settings page saved: test every Local LLM provider of the saved settings that is new, whose address or key changed, or that changed.
    Returns the names tested."""
    old_servers = {s["name"]: s for s in get_servers(old or {})}
    old_providers = {p.get("name"): p for p in (migrate(old or {})).get("providers") or []}
    tested = []
    for p in migrate(new).get("providers") or []:
        s = server_of(p)
        if not s or not s["address"]:
            continue
        before = old_servers.get(s["name"])
        if not before or fingerprint(before) != fingerprint(s) or p != old_providers.get(p.get("name")):
            run_test(s, timeout=timeout)
            tested.append(s["name"])
    return tested
