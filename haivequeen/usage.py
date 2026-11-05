"""Provider quota windows and usage refresh."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from haivequeen import platform as osplat
from haivequeen import localtime
from haivequeen import profile
from haivequeen import runtime

GLOBAL_PROJECT = "*"        # kv project value for machine-wide state
CLAUDE_USAGE_KEY = "usage-claude"
CLAUDE_CHECK_KEY = "usage-claude-last-check"
CACHE_VERSION = 1
DAY_ZERO = date(2020, 1, 1).toordinal()
CHUNK = 4 << 20

OUT_TAG = b'"output_tokens"'
ASSISTANT_TAG = b'"type":"assistant"'
TURN_CTX_TAG = b'"type":"turn_context"'
EVENT_MSG_TAG = b'"type":"event_msg"'
TOKEN_COUNT_TAG = b'"token_count"'


# ---------------------------------------------------------------- paths

def home_dir() -> Path:
    """Return the base of the CLI log and settings folders: the HaiveQueen profile, never the user's home folder."""
    return profile.profile_dir()




def codex_root() -> Path:
    """Return the folder with the Codex session logs (in the profile)."""
    return home_dir() / "codex" / "sessions"






# ---------------------------------------------------------------- small helpers

def day_of(local: date) -> int:
    """Return the day number (days since 2020-01-01) of a local date."""
    return local.toordinal() - DAY_ZERO


def parse_ts(text):
    """Return (day number in local time, epoch seconds) of an ISO timestamp, or None if unparsable.

    A timestamp without offset counts as UTC.
    """
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        epoch = d.timestamp()
        return day_of(datetime.fromtimestamp(epoch).date()), epoch
    except (ValueError, OverflowError, OSError):
        return None












def format_reset_time(epoch) -> str:
    """Convert epoch seconds to local HH:mm (today) or dd.MM. HH:mm; empty for no value."""
    if not epoch:
        return ""
    d = datetime.fromtimestamp(int(epoch))
    return localtime.time(d) if d.date() == datetime.now().date() else localtime.date_time(d)


# ---------------------------------------------------------------- scanner state and cache

class ScanState:
    """In-memory scanner state: model table, per-file offsets (Claude, Codex) and per-message records."""

    def __init__(self):
        """Create an empty state."""
        self.models = []          # [{"name", "codex", "min"}] min = oldest epoch seconds or 0
        self.index = {}           # "x:name" / "a:name" -> model index
        self.claude = {}          # path -> file state
        self.codex = {}
        self.ids = {}             # message id -> [model, day, in, out]
        self.dirty = False

    def model_idx(self, name: str, codex: bool) -> int:
        """Return the index of a model in the table, adding it when new."""
        k = ("x:" if codex else "a:") + name
        if k in self.index:
            return self.index[k]
        self.models.append({"name": name, "codex": codex, "min": 0})
        self.index[k] = len(self.models) - 1
        self.dirty = True
        return self.index[k]

    def note_time(self, idx: int, epoch: float) -> bool:
        """Lower the oldest-time of a model to epoch; returns True when it changed."""
        cur = self.models[idx]["min"]
        if cur == 0 or epoch < cur:
            self.models[idx]["min"] = epoch
            return True
        return False








# ---------------------------------------------------------------- line handlers







# ---------------------------------------------------------------- scan





# ---------------------------------------------------------------- 5h / 7d windows and weekly limits

# Read the canonical quota snapshot without exposing malformed cached data.
def _claude_snapshot(conn, project=GLOBAL_PROJECT):
    """Return the saved quota object, or an empty object when unavailable."""
    try:
        value = json.loads(runtime.kv_get(conn, project, CLAUDE_USAGE_KEY) or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


# Expose a failed authoritative refresh independently of retained historical percentages.
def claude_refresh_failed(conn, project=GLOBAL_PROJECT):
    """Return whether current Claude quota could not be verified."""
    return bool(_claude_snapshot(conn, project).get('refresh_error'))


# Why the newest Claude usage check failed.
def claude_refresh_error(conn, project=GLOBAL_PROJECT) -> str:
    """The text stored with the failed check (what went wrong), or an empty string when the newest check worked."""
    return str(_claude_snapshot(conn, project).get('refresh_error') or '')


RETAIN_HOURS = 6   # a verified Claude reading younger than this still counts for starts and ranking while the newest check failed


# The last verified Claude reading while a failed check must not block every start.
def retained_claude(conn, project=GLOBAL_PROJECT):
    """The last verified reading (the snapshot without its error) when the newest check failed but that reading is younger than RETAIN_HOURS and has a window; else None."""
    snap = _claude_snapshot(conn, project)
    if not snap.get('refresh_error') or not (snap.get('fiveHour') or snap.get('sevenDay')):
        return None
    try:
        saved = datetime.fromisoformat(str(snap.get('saved') or ''))
    except ValueError:
        return None
    if saved.tzinfo is None:
        saved = saved.astimezone()
    if (datetime.now(timezone.utc) - saved).total_seconds() > RETAIN_HOURS * 3600:
        return None
    return {k: v for k, v in snap.items() if k != 'refresh_error'}


# The weekly part of the retained reading.
def retained_weekly(conn, project=GLOBAL_PROJECT):
    """(used percent, reset epoch or None) of the weekly window of the retained reading, or None when there is none or its window has already reset."""
    kept = retained_claude(conn, project)
    window = _claude_window(kept.get('sevenDay'), True, int(datetime.now(timezone.utc).timestamp())) if kept else None
    return (window['pct'], window.get('at')) if window and window.get('pct') is not None else None


USAGE_INTERVAL = 60              # seconds between usage checks of a model provider
USAGE_UNREACHABLE_INTERVAL = 600   # seconds between usage checks of a provider whose last check failed (not reachable)


# Choose how often the usage status of a model provider is asked for.
def usage_interval(unreachable) -> int:
    """Seconds between two usage checks of a provider: USAGE_UNREACHABLE_INTERVAL when its last check failed, else USAGE_INTERVAL.

    The rule is the same for every model provider (Claude, Codex, ...)."""
    return USAGE_UNREACHABLE_INTERVAL if unreachable else USAGE_INTERVAL


# Fetch bounded metadata from the configured CLI without starting a model turn.
def refresh_claude_usage(conn, providers, run=None, force=False, min_interval=None):
    """Refresh global Claude quota; return True only for a verified new snapshot.

    `min_interval` is the shortest time between two checks; by default usage_interval decides: one minute, ten minutes after a failed check
    (Claude not reachable)."""
    if run is None and (os.environ.get('BZZ_DATA_DIR') or os.environ.get('BZZ_RUNTIME_OVERRIDE')):
        return False
    provider = next((p for p in providers if (p.get('limit', {}).get('source') if isinstance(p.get('limit'), dict) else p.get('limit')) == 'claude'), None)
    if provider is None:
        return False
    old = _claude_snapshot(conn)
    if min_interval is None:
        min_interval = usage_interval(bool(old.get('refresh_error')))
    now = datetime.now(timezone.utc)
    try:
        checked = datetime.fromisoformat(runtime.kv_get(conn, GLOBAL_PROJECT, CLAUDE_CHECK_KEY) or old.get('checked_at') or '').astimezone(timezone.utc)
        if not force and 0 <= (now - checked).total_seconds() < min_interval:
            return False
    except (ValueError, TypeError):
        pass
    checked_at = now.isoformat()
    try:
        executable = shutil.which(str(provider.get('command') or 'claude'))
        if not executable:
            raise ValueError('missing CLI')
        argv = [executable, '-p', '/usage', '--output-format', 'stream-json', '--verbose',
                '--no-session-persistence', '--setting-sources', '', '--settings', '{"disableAllHooks":true}',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '']
        child_env = profile.environment({**provider, 'protocol': 'claude'})
        child_env.pop('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', None)
        kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.DEVNULL,
                  'text': True, 'encoding': 'utf-8', 'errors': 'replace', 'timeout': 20, 'env': child_env}
        if osplat.is_windows():
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        result = (run or subprocess.run)(argv, **kwargs)
        if result.returncode != 0:
            # Only the exit code is kept: the CLI's own output may hold secrets and must not reach the status.
            raise ValueError('CLI failed (exit %s)' % result.returncode)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        results = [event for event in events if isinstance(event, dict) and event.get('type') == 'result']
        final = results[-1] if results else {}
        if (final.get('subtype') != 'success' or final.get('is_error') is not False
                or type(final.get('num_turns')) is not int or final['num_turns'] != 0
                or type(final.get('total_cost_usd')) not in (int, float) or final['total_cost_usd'] != 0):
            raise ValueError('not metadata only')
        reports = [event.get('usage_report') for event in events if isinstance(event, dict) and event.get('type') == 'assistant' and isinstance(event.get('usage_report'), dict)]
        limits = (reports[-1].get('rate_limits') or {}).get('limits') if reports else None
        if not isinstance(limits, list):
            raise ValueError('missing quotas')
        value = {'saved': checked_at, 'checked_at': checked_at, 'source': 'claude-cli:/usage', 'fiveHour': None, 'sevenDay': None}
        for row in limits:
            if not isinstance(row, dict) or row.get('kind') not in ('session', 'weekly_all'):
                continue
            if isinstance(row['percent'], bool):
                raise ValueError('invalid percentage')
            percent = float(row['percent'])
            reset = None
            if row['resets_at'] is not None:
                reset_at = datetime.fromisoformat(row['resets_at'])
                if reset_at.tzinfo is None:
                    raise ValueError('ambiguous reset time')
                reset = int(reset_at.timestamp())
            if not 0 <= percent <= 100:
                raise ValueError('invalid percentage')
            value['fiveHour' if row['kind'] == 'session' else 'sevenDay'] = {'utilization': percent / 100, 'resetsAt': reset}
        if value['fiveHour'] is None and value['sevenDay'] is None:
            raise ValueError('missing global quotas')
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        value = {**old, 'checked_at': checked_at, 'refresh_error': 'Claude usage refresh failed: %s' % ((str(exc) or type(exc).__name__)[:160])}
        runtime.kv_set(conn, GLOBAL_PROJECT, CLAUDE_USAGE_KEY, json.dumps(value))
        runtime.kv_set(conn, GLOBAL_PROJECT, CLAUDE_CHECK_KEY, checked_at)
        return False
    runtime.kv_set(conn, GLOBAL_PROJECT, CLAUDE_USAGE_KEY, json.dumps(value))
    runtime.kv_set(conn, GLOBAL_PROJECT, CLAUDE_CHECK_KEY, checked_at)
    return True


def save_claude_usage(conn, event, project: str = GLOBAL_PROJECT) -> bool:
    """Remember the newest Claude usage windows of a rate_limit_event in kv (key usage-claude); False if none."""
    info = event.get("rate_limit_info") if isinstance(event, dict) else None
    w = info.get("unifiedWindows") if isinstance(info, dict) else None
    if not w:
        return False
    value = {"saved": datetime.now().astimezone().isoformat(), "fiveHour": w.get("five_hour"),
             "sevenDay": w.get("seven_day")}
    runtime.kv_set(conn, project, CLAUDE_USAGE_KEY, json.dumps(value))
    return True


# Format verified percentages without inventing an unknown reset time.
def _claude_window(w, with_at: bool, now: int):
    """Return a verified percentage with its known reset, an empty reset, or a new-window marker."""
    if not w:
        return None
    if w.get("resetsAt") is None:
        out = {"pct": round(100 * float(w["utilization"])), "reset": ""}
        if with_at:
            out["at"] = None
        return out
    if int(w["resetsAt"]) > now:
        out = {"pct": round(100 * float(w["utilization"])), "reset": format_reset_time(w["resetsAt"])}
        if with_at:
            out["at"] = int(w["resetsAt"])
        return out
    return {"pct": None, "reset": "new window"}


# Hide historical quota readings after an unsuccessful authoritative refresh.
def get_claude_usage(conn, project: str = GLOBAL_PROJECT) -> dict:
    """Return Claude's 5h/7d usage {five_hour, seven_day} each {pct, reset[, at]} or None when unknown.

    A window that has already reset gives pct None and reset 'new window'.
    """
    res = {"five_hour": None, "seven_day": None}
    try:
        raw = runtime.kv_get(conn, project, CLAUDE_USAGE_KEY)
        if not raw or not raw.strip():
            return res
        j = json.loads(raw)
        if j.get("refresh_error"):
            return res
        now = int(datetime.now(timezone.utc).timestamp())
        res["five_hour"] = _claude_window(j.get("fiveHour"), False, now)
        res["seven_day"] = _claude_window(j.get("sevenDay"), True, now)
    except (ValueError, KeyError, TypeError):
        pass
    return res


def get_codex_usage(codex_dir=None) -> dict:
    """Return Codex's 5h/7d usage {five_hour, seven_day} each {pct, reset, at} or None.

    Read from the last rate_limits block of the newest session logs; the windows are told apart by
    window_minutes (300 = 5h, 10080 = week).
    """
    res = {"five_hour": None, "seven_day": None}
    root = Path(codex_dir) if codex_dir else codex_root()
    try:
        files = sorted((p for p in root.rglob("*.jsonl") if p.is_file()), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:3]
        for f in files:
            with open(f, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                n = min(size, 400 * 1024)
                fh.seek(size - n)
                buf = fh.read(n)
            lines = [ln for ln in buf.decode("utf-8", errors="replace").split("\n") if '"rate_limits":{' in ln]
            if not lines:
                continue
            rl = json.loads(lines[-1]).get("payload", {}).get("rate_limits")
            if not rl:
                continue
            now = int(datetime.now(timezone.utc).timestamp())
            for w in (rl.get("primary"), rl.get("secondary")):
                if not w or int(w["resets_at"]) <= now:
                    continue
                v = {"pct": round(float(w["used_percent"])), "reset": format_reset_time(w["resets_at"]),
                     "at": int(w["resets_at"])}
                if int(w.get("window_minutes") or 0) <= 400:
                    res["five_hour"] = v
                else:
                    res["seven_day"] = v
            break
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        pass
    return res


CODEX_USAGE_KEY = "usage-codex"
_codex_refresh = {}   # codex folder -> time of the last read, so the logs are read at most once per interval
_codex_unreachable = {}   # codex folder -> whether the last read found no usage window


def refresh_codex_usage(conn, codex_dir=None, project: str = GLOBAL_PROJECT, min_interval: float = None, force: bool = False) -> bool:
    """Write Codex's 5h/7d windows from the session logs into kv (key usage-codex, same form as usage-claude); True when written.

    Cheap: the logs are read at most once per `min_interval` seconds per folder; by default usage_interval decides (one minute,
    ten minutes after a read that found no window: Codex not reachable). Nothing is written when no window is known
    (the old value stays). Without an explicit folder it does nothing in a test runtime (BZZ_DATA_DIR set). Only reads the profile.
    """
    if codex_dir is None and os.environ.get("BZZ_DATA_DIR"):
        return False
    root = str(Path(codex_dir) if codex_dir else codex_root())
    if min_interval is None:
        min_interval = usage_interval(_codex_unreachable.get(root, False))
    nowt = time.monotonic()
    last = _codex_refresh.get(root)
    if not force and last is not None and nowt - last < min_interval:
        return False
    _codex_refresh[root] = nowt
    u = get_codex_usage(codex_dir)
    value = {"saved": datetime.now().astimezone().isoformat()}
    found = False
    for key, name in (("fiveHour", "five_hour"), ("sevenDay", "seven_day")):
        w = u[name]
        if w:
            value[key] = {"utilization": w["pct"] / 100.0, "resetsAt": w["at"]}
            found = True
    _codex_unreachable[root] = not found
    if not found:
        return False
    runtime.kv_set(conn, project, CODEX_USAGE_KEY, json.dumps(value))
    return True


def format_usage(u) -> str:
    """Return one display text for a usage value ('27% (until 12:30)' or 'n/a')."""
    if u is None or u.get("pct") is None:
        return "n/a"
    return f"{u['pct']}% (until {u['reset']})"


# Treat an unverified Claude quota as unavailable while retaining unknown usage.
def get_provider_limit(conn, provider: dict, project: str = GLOBAL_PROJECT, read_pc=None, codex_dir=None) -> dict:
    """Return the weekly limit of a provider as {used_pct, free_pct, hours, reset_epoch}.

    The source is provider['limit']['source'] (claude | codex | none). free_pct is 100 minus the used percent,
    hours the time until the reset. No source, no reading or an unknown value counts as 100 % free with a
    reset in 168 h (coordinator value). Tests: BZZ_PROVIDER_LIMITS_FILE = {"<provider>":{"usedPercent":80,
    "resetHours":24}}. read_pc=None reads this PC unless BZZ_DATA_DIR is set (test runtime).
    """
    name = provider.get("name")
    limit = provider.get("limit")
    source = limit.get("source") if isinstance(limit, dict) else limit
    used, hours, at = None, None, None
    lf = os.environ.get("BZZ_PROVIDER_LIMITS_FILE")
    if read_pc is None:
        read_pc = not os.environ.get("BZZ_DATA_DIR")
    if lf and Path(lf).is_file():
        t = json.loads(Path(lf).read_text(encoding="utf-8-sig"))
        if name in t:
            used, hours = t[name].get("usedPercent"), t[name].get("resetHours")
    elif source == "claude" and claude_refresh_failed(conn, project):
        kept = retained_weekly(conn, project)   # the last verified weekly reading while it is recent; without one the quota counts as unavailable
        if kept is None:
            return {"used_pct": None, "free_pct": 0.0, "hours": 168.0, "reset_epoch": None}
        used, at = kept
        if at:
            hours = (at - datetime.now(timezone.utc).timestamp()) / 3600
    elif read_pc and source in ("claude", "codex"):
        u = (get_claude_usage(conn, project) if source == "claude" else get_codex_usage(codex_dir))["seven_day"]
        if u and u.get("pct") is not None:
            used = u["pct"]
            if u.get("at"):
                at = u["at"]
                hours = (u["at"] - datetime.now(timezone.utc).timestamp()) / 3600
    if used is None:
        return {"used_pct": None, "free_pct": 100.0, "hours": 168.0, "reset_epoch": None}
    return {"used_pct": float(used), "free_pct": max(0.0, 100.0 - float(used)),
            "hours": float(hours) if hours is not None else 168.0, "reset_epoch": at}


# Block provider starts when Claude quota refresh fails without inventing usage.
def limit_windows(conn, provider: dict, project: str = GLOBAL_PROJECT, read_pc=None, codex_dir=None) -> list:
    """Usage windows of a provider as the LIMITS display has them: [{used_pct, reset_epoch}] for the 5h and the weekly window (reset_epoch None
    when unknown); windows that already reset are left out. Tests: an entry of BZZ_PROVIDER_LIMITS_FILE may carry "windows": [{"usedPercent",
    "resetHours"}, ...]; without it the plain usedPercent / resetHours of the entry is the one window."""
    name = provider.get("name")
    limit = provider.get("limit")
    source = limit.get("source") if isinstance(limit, dict) else limit
    now = datetime.now(timezone.utc).timestamp()
    lf = os.environ.get("BZZ_PROVIDER_LIMITS_FILE")
    if read_pc is None:
        read_pc = not os.environ.get("BZZ_DATA_DIR")
    out = []
    if lf and Path(lf).is_file():
        entry = json.loads(Path(lf).read_text(encoding="utf-8-sig")).get(name) or {}
        wins = entry.get("windows") or ([{"usedPercent": entry.get("usedPercent"), "resetHours": entry.get("resetHours")}] if entry.get("usedPercent") is not None else [])
        for w in wins:
            hours = w.get("resetHours")
            out.append({"used_pct": float(w["usedPercent"]), "reset_epoch": now + float(hours) * 3600 if hours is not None else None})
    elif source == "claude" and claude_refresh_failed(conn, project):
        kept = retained_claude(conn, project) or {}   # a reading that is still recent counts; without one nothing is known and nothing starts
        for w in (kept.get("fiveHour"), kept.get("sevenDay")):
            if w and (w.get("resetsAt") is None or int(w["resetsAt"]) > now):
                out.append({"used_pct": 100 * float(w["utilization"]), "reset_epoch": int(w["resetsAt"]) if w.get("resetsAt") is not None else None})
        return out or [{"used_pct": 100, "reset_epoch": None, "unknown": True}]
    elif read_pc and source == "claude":
        try:
            j = json.loads(runtime.kv_get(conn, project, CLAUDE_USAGE_KEY) or "{}")
        except ValueError:
            j = {}
        for w in (j.get("fiveHour"), j.get("sevenDay")):
            if w and (w.get("resetsAt") is None or int(w["resetsAt"]) > now):
                out.append({"used_pct": 100 * float(w["utilization"]), "reset_epoch": int(w["resetsAt"]) if w.get("resetsAt") is not None else None})
    elif read_pc and source == "codex":
        for w in (get_codex_usage(codex_dir) or {}).values():
            if w and w.get("pct") is not None:
                out.append({"used_pct": float(w["pct"]), "reset_epoch": w.get("at")})
    return out


def provider_limits(conn, providers, project: str = GLOBAL_PROJECT, read_pc=None, codex_dir=None) -> dict:
    """Return {provider name: get_provider_limit(...)} for a list of provider dicts (the scheduler's source)."""
    return {p["name"]: get_provider_limit(conn, p, project, read_pc, codex_dir) for p in providers}
