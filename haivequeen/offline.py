"""Offline providers: a provider whose agent ended with a 5xx or a connection error is offline until a short check of it succeeds.

The state lives in the global kv key `provider-offline` ({provider name: {since, reason, checked}}), so the agent runner (another process), the loop and the
dashboard see the same state. An offline provider gets no new Queen and no new Bee and no retry (`apply_state` sets its slots to 0 in the provider list, the
same way a disabled provider is treated); agents that run keep running. Every `providerRecheckMinutes` (config.toml, default 5) HaiveQueen checks an offline
provider: a Local LLM with its connection test, a Subscription or API key provider with a short call of its CLI; when that works the provider is online at once.
A rate limit does not make a provider offline (it keeps its own handling).
"""
import json
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta

from . import localtime, profile, runtime, servers

KEY = "provider-offline"
GLOBAL = "*"
CLI_TIMEOUT_S = 60
QUESTION = "Reply with the word OK."


def _iso(moment) -> str:
    """ISO time to the second."""
    return moment.isoformat(timespec="seconds")


def _parse(text):
    """The datetime of an ISO text, or None."""
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def read(conn) -> dict:
    """{provider name: {since, reason, checked}} of the providers that are offline now."""
    try:
        data = json.loads(runtime.kv_get(conn, GLOBAL, KEY) or "{}")
    except (ValueError, sqlite3.Error):
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _write(conn, data) -> None:
    """Store the offline state."""
    runtime.kv_set(conn, GLOBAL, KEY, json.dumps(data, ensure_ascii=False))


def mark(conn, name, reason="", now=None) -> None:
    """Set the provider `name` offline (the time of the first mark stays, so the hover text says since when)."""
    now = now or datetime.now()
    data = read(conn)
    entry = data.get(name)
    if entry is None:
        entry = data[name] = {"since": _iso(now), "checked": _iso(now)}
    entry["reason"] = " ".join(str(reason or "").split())[:200]
    _write(conn, data)


def clear(conn, name) -> None:
    """Set the provider `name` online again."""
    data = read(conn)
    if data.pop(name, None) is not None:
        _write(conn, data)


def since_text(since) -> str:
    """The time an offline state began as a short local text (time today, else date and time)."""
    moment = _parse(since)
    if moment is None:
        return str(since or "")
    return localtime.time(moment) if moment.date() == datetime.now().date() else localtime.date_time(moment)


def apply_state(providers, conn) -> list:
    """Set, in place, what the scheduler may use of each provider: a disabled provider and an offline provider have 0 slots (the configured number is kept
    in `configuredSlots` and comes back when the provider is online or enabled again); `offline` holds the time since when a provider is offline, else ''."""
    data = read(conn) if conn is not None else {}
    for p in providers:
        configured = p.setdefault("configuredSlots", p.get("slots", 0))
        since = str((data.get(p["name"]) or {}).get("since") or "")
        p["offline"] = since if p.get("enabled", True) else ""
        p["unavailable"] = "" if p.get("enabled", True) and not since else ("disabled" if not p.get("enabled", True) else "offline")
        p["slots"] = 0 if p["unavailable"] else configured
    return providers


def _model_of(p) -> str:
    """The model of the first class that has one."""
    for cls in ("medium", "light", "heavy"):
        model = str(((p.get("classes") or {}).get(cls) or {}).get("model") or "")
        if model:
            return model
    return ""


def run_cli(p) -> bool:
    """A short call of the CLI of a provider (a one-word question, nothing else): True when it ends with exit code 0 within the time limit."""
    model = _model_of(p)
    if p.get("protocol") == "claude":
        args = ["-p", QUESTION, "--max-turns", "1"] + (["--model", model] if model else [])
    elif p.get("protocol") == "codex":
        args = ["exec", "--skip-git-repo-check"] + (["-m", model] if model else []) + [QUESTION]
    else:
        args = ["--version"]
    command = shutil.which(str(p.get("command") or "")) or str(p.get("command") or "")
    try:
        done = subprocess.run([command] + args, env=profile.environment(p), cwd=tempfile.gettempdir(), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=CLI_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return done.returncode == 0


def probe(p) -> bool:
    """True when the provider answers again: a Local LLM passes its connection test (it answers and offers the interface its CLI needs), another provider
    answers a short call of its CLI."""
    if p.get("server"):
        try:
            servers.run_test(p["server"])
        except Exception:
            return False
        return not servers.needs_retest(p)
    return run_cli(p)


def recheck(conn, providers, minutes, now=None) -> list:
    """Check the offline providers among `providers` whose last check is `minutes` or more ago; the ones that answer are online at once. Returns their names."""
    now = now or datetime.now()
    data = read(conn)
    back, changed = [], False
    for p in providers:
        entry = data.get(p["name"])
        if entry is None or not p.get("enabled", True):
            continue
        last = _parse(entry.get("checked")) or _parse(entry.get("since"))
        if last is not None and now - last < timedelta(minutes=float(minutes)):
            continue
        if probe(p):
            del data[p["name"]]
            back.append(p["name"])
        else:
            entry["checked"] = _iso(now)
        changed = True
    if changed:
        _write(conn, data)
    return back
