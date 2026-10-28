"""Token usage of Claude Code and Codex sessions, 5h/7d windows and weekly-limit sources.

Port of usage.ps1 and zst-usage.py. The logs are read incrementally (byte offset per file, cache file
usage-cache.bin in the cache folder). get_usage_table() returns the table as plain data for the dashboard;
the window functions feed the scheduler (weekly limit per provider).

Deviations from the PowerShell version (see the step notes): the cache is zlib-compressed JSON instead of a
.NET binary file; timestamps are epoch seconds instead of .NET ticks; retention texts are English; the Codex
zst helper is this module itself, started with a Python that has compression.zstd (3.14) when the running
Python has none.
"""
import json
import os
import re
import subprocess
import sys
import time
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

from haivequeen import platform as osplat
from haivequeen import runtime

GLOBAL_PROJECT = "*"        # kv project value for machine-wide state
CLAUDE_USAGE_KEY = "usage-claude"
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
    """Return the user's home folder (the per-OS base of the CLI log folders)."""
    return Path.home()


def claude_root() -> Path:
    """Return the folder with the Claude Code session logs (~/.claude/projects)."""
    return home_dir() / ".claude" / "projects"


def codex_root() -> Path:
    """Return the folder with the Codex session logs (~/.codex/sessions)."""
    return home_dir() / ".codex" / "sessions"


def claude_settings_path() -> Path:
    """Return the Claude Code settings file (read only, never written)."""
    return home_dir() / ".claude" / "settings.json"


def default_cache_dir() -> Path:
    """Return the usage cache folder inside the machine data folder."""
    return osplat.data_dir() / "usage-cache"


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


def norm_claude(name):
    """Normalise a Claude model name (strip bracket and date suffixes); None for synthetic or empty names."""
    if not name:
        return None
    name = re.sub(r"\[.*\]$", "", name)
    name = re.sub(r"-\d{8}$", "", name)
    if not name or "synthetic" in name.lower():
        return None
    return name


def _num(obj, key) -> int:
    """Return an integer property of a JSON object, 0 if missing or not an integer."""
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return 0


def _title(word: str) -> str:
    """Return the word with its first letter upper-cased."""
    return word[:1].upper() + word[1:]


def model_label(key: str, codex: bool) -> str:
    """Return the display label of a model key (type, short name, version, no effort)."""
    if codex:
        m = re.match(r"^gpt-(\d+(?:\.\d+)?)-(.+)$", key, re.I)
        if m:
            name = " ".join(_title(p) for p in m.group(2).split("-") if p)
            return f"Codex {name} {m.group(1)}"
        m = re.match(r"^gpt-(\d+(?:\.\d+)?)$", key, re.I)
        if m:
            return f"Codex {m.group(1)}"
        return f"Codex {key}"
    m = re.match(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?$", key, re.I)
    if m:
        v = m.group(2) + ("." + m.group(3) if m.group(3) else "")
        n = m.group(1)[:3]
        return "Claude " + n[:1].upper() + n[1:] + " " + v
    m = re.match(r"^claude-(\d+)(?:-(\d{1,2}))?-([a-z]+)$", key, re.I)
    if m:
        v = m.group(1) + ("." + m.group(2) if m.group(2) else "")
        n = m.group(3)
        return "Claude " + n[:1].upper() + n[1:3] + " " + v
    if key.lower().startswith("claude"):
        return f"Claude {key}"
    return key


def format_date(d: datetime) -> str:
    """Format a date for the retention texts (dd.MM.yyyy)."""
    return d.strftime("%d.%m.%Y")


def format_reset_time(epoch) -> str:
    """Convert epoch seconds to local HH:mm (today) or dd.MM. HH:mm; empty for no value."""
    if not epoch:
        return ""
    d = datetime.fromtimestamp(int(epoch))
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%d.%m. %H:%M")


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


def _new_file() -> dict:
    """Return an empty per-file state."""
    return {"size": -1, "mtime": 0, "offset": 0, "model": -1, "days": []}


def load_cache(path) -> ScanState:
    """Load the cache file; an empty state if it is missing, damaged or of another version."""
    st = ScanState()
    p = Path(path)
    if not p.is_file():
        return st
    try:
        data = json.loads(zlib.decompress(p.read_bytes()).decode("utf-8"))
        if data.get("version") != CACHE_VERSION:
            return ScanState()
        st.models = [{"name": m["name"], "codex": bool(m["codex"]), "min": float(m["min"])} for m in data["models"]]
        for i, m in enumerate(st.models):
            st.index[("x:" if m["codex"] else "a:") + m["name"]] = i
        st.claude = {k: dict(v) for k, v in data["claude"].items()}
        st.codex = {k: dict(v) for k, v in data["codex"].items()}
        st.ids = {k: list(v) for k, v in data["ids"].items()}
    except Exception:
        return ScanState()
    return st


def save_cache(st: ScanState, path) -> None:
    """Write the cache atomically (temporary file, then replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CACHE_VERSION, "models": st.models, "claude": st.claude, "codex": st.codex, "ids": st.ids}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8")))
    os.replace(tmp, p)


# ---------------------------------------------------------------- line handlers

def read_lines(path, offset: int, on_line):
    """Call on_line(bytes) for every complete line from offset on.

    Returns (offset after the last complete line, file length, bytes read).
    """
    pos, total = offset, 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        carry = b""
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            buf = carry + chunk
            start = 0
            while True:
                nl = buf.find(b"\n", start)
                if nl < 0:
                    break
                on_line(buf[start:nl])
                start = nl + 1
            pos += start
            carry = buf[start:]
        length = os.fstat(fh.fileno()).st_size
    return pos, length, total


def claude_line(line: bytes, st: ScanState) -> None:
    """Handle one Claude log line: usage of assistant messages per message id (highest output wins)."""
    if OUT_TAG not in line or ASSISTANT_TAG not in line:
        return
    try:
        root = json.loads(line)
        if root.get("type") != "assistant":
            return
        msg = root.get("message")
        if not isinstance(msg, dict):
            return
        mid, model, usage, ts = msg.get("id"), msg.get("model"), msg.get("usage"), root.get("timestamp")
        if not isinstance(mid, str) or not isinstance(model, str) or not isinstance(usage, dict) \
                or not isinstance(ts, str):
            return
        name = norm_claude(model)
        if name is None:
            return
        parsed = parse_ts(ts)
        if parsed is None:
            return
        day, epoch = parsed
        rec = [st.model_idx(name, False), day,
               _num(usage, "input_tokens") + _num(usage, "cache_creation_input_tokens")
               + _num(usage, "cache_read_input_tokens"),
               _num(usage, "output_tokens")]
        old = st.ids.get(mid)
        if old is None or rec[3] >= old[3]:
            st.ids[mid] = rec
            st.dirty = True
        if st.note_time(rec[0], epoch):
            st.dirty = True
    except (ValueError, AttributeError):
        pass


def codex_line(line: bytes, st: ScanState, fs: dict) -> None:
    """Handle one Codex log line: track the model (turn_context) and cumulative token counts per day."""
    head = line[:200]
    try:
        if TURN_CTX_TAG in head:
            payload = json.loads(line).get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("model"), str) and payload["model"]:
                fs["model"] = st.model_idx(payload["model"], True)
            return
        if EVENT_MSG_TAG not in head or TOKEN_COUNT_TAG not in line:
            return
        root = json.loads(line)
        p = root.get("payload")
        if not isinstance(p, dict) or p.get("type") != "token_count":
            return
        info = p.get("info")
        if not isinstance(info, dict):
            return
        tot = info.get("total_token_usage")
        ts = root.get("timestamp")
        if not isinstance(tot, dict) or not isinstance(ts, str):
            return
        parsed = parse_ts(ts)
        if parsed is None:
            return
        day, epoch = parsed
        cin, cout = _num(tot, "input_tokens"), _num(tot, "output_tokens")
        if fs["model"] < 0:
            fs["model"] = st.model_idx("unbekannt", True)
        days = fs["days"]
        if days and days[-1][0] == day:
            days[-1][1], days[-1][2] = cin, cout
        elif not days or days[-1][0] < day:
            days.append([day, cin, cout])
        st.note_time(fs["model"], epoch)
        st.dirty = True
    except (ValueError, AttributeError):
        pass


# ---------------------------------------------------------------- scan

def _scan_file(path: str, store: dict, st: ScanState, handler, codex: bool, res: dict) -> None:
    """Read the new part of one log file (if size or time changed) and update its state."""
    stat = os.stat(path)
    fs = store.get(path)
    if fs is None:
        fs = store[path] = _new_file()
        st.dirty = True
    mt, length = stat.st_mtime_ns, stat.st_size
    if fs["size"] == length and fs["mtime"] == mt:
        return
    if fs["size"] == length:
        fs["mtime"] = mt
        st.dirty = True
        return
    if length < fs["size"]:
        fs["offset"] = 0
        if codex:
            fs["days"] = []
            fs["model"] = -1
    try:
        pos, new_len, nbytes = read_lines(path, fs["offset"], lambda b: handler(b, fs))
    except OSError:
        return
    res["bytes_read"] += nbytes
    fs["offset"], fs["size"], fs["mtime"] = pos, new_len, mt
    st.dirty = True
    res["files_read"] += 1


def scan(claude_dir, codex_dir, cache_file, today_day: int) -> dict:
    """Scan new log data incrementally, update the cache and return per-model sums plus statistics.

    Result keys: models (list of dicts name, codex, today_in, today_out, total_in, total_out, since),
    files_read, files_seen, bytes_read, cache_written, claude_files, codex_jsonl_files, codex_other_files,
    claude_oldest, codex_oldest (epoch seconds or None), codex_oldest_path, seconds.
    """
    t0 = datetime.now()
    res = {"files_read": 0, "files_seen": 0, "bytes_read": 0, "cache_written": False, "claude_files": 0,
           "codex_jsonl_files": 0, "codex_other_files": 0, "claude_oldest": None, "codex_oldest": None,
           "codex_oldest_path": ""}
    cache_existed = Path(cache_file).is_file()
    st = load_cache(cache_file)

    if Path(claude_dir).is_dir():
        for dp, _dn, names in os.walk(claude_dir):
            for nm in names:
                if not nm.lower().endswith(".jsonl"):
                    continue
                p = os.path.join(dp, nm)
                res["claude_files"] += 1
                res["files_seen"] += 1
                try:
                    mtime = os.stat(p).st_mtime
                except OSError:
                    continue
                if res["claude_oldest"] is None or mtime < res["claude_oldest"]:
                    res["claude_oldest"] = mtime
                try:
                    _scan_file(p, st.claude, st, lambda b, fs: claude_line(b, st), False, res)
                except OSError:
                    pass

    if Path(codex_dir).is_dir():
        for dp, _dn, names in os.walk(codex_dir):
            for nm in names:
                p = os.path.join(dp, nm)
                try:
                    mtime = os.stat(p).st_mtime
                except OSError:
                    continue
                if res["codex_oldest"] is None or mtime < res["codex_oldest"]:
                    res["codex_oldest"], res["codex_oldest_path"] = mtime, p
                if not nm.lower().endswith(".jsonl"):
                    res["codex_other_files"] += 1
                    continue
                res["codex_jsonl_files"] += 1
                res["files_seen"] += 1
                try:
                    _scan_file(p, st.codex, st, lambda b, fs: codex_line(b, st, fs), True, res)
                except OSError:
                    pass

    if st.dirty or not cache_existed:
        save_cache(st, cache_file)
        res["cache_written"] = True

    agg = [{"name": m["name"], "codex": m["codex"], "today_in": 0, "today_out": 0, "total_in": 0,
            "total_out": 0, "since": m["min"], "has_total": False} for m in st.models]
    for model, day, cin, cout in st.ids.values():
        a = agg[model]
        a["has_total"] = True
        a["total_in"] += cin
        a["total_out"] += cout
        if day == today_day:
            a["today_in"] += cin
            a["today_out"] += cout
    for fs in st.codex.values():
        if not fs["days"] or fs["model"] < 0:
            continue
        a = agg[fs["model"]]
        last = fs["days"][-1]
        a["has_total"] = True
        a["total_in"] += last[1]
        a["total_out"] += last[2]
        if last[0] == today_day:
            prev = fs["days"][-2] if len(fs["days"]) >= 2 else None
            a["today_in"] += last[1] - (prev[1] if prev else 0)
            a["today_out"] += last[2] - (prev[2] if prev else 0)
    res["models"] = [a for a in agg if a["has_total"]]
    res["seconds"] = (datetime.now() - t0).total_seconds()
    return res


# ---------------------------------------------------------------- compressed Codex sessions (zst helper)

def has_zstd() -> bool:
    """True when the running Python has the standard module compression.zstd (Python 3.14)."""
    try:
        import importlib.util
        return importlib.util.find_spec("compression.zstd") is not None
    except (ImportError, ValueError):
        return False


def scan_zst_file(path: str) -> dict:
    """Read one compressed Codex session (read only) and return its cache entry (model, in, out, min)."""
    from compression import zstd  # Python 3.14 only
    model, tin, tout, first = None, 0, 0, None
    with zstd.open(path, "rb") as fh:
        for line in fh:
            head = line[:200]
            if TURN_CTX_TAG in head:
                try:
                    m = json.loads(line).get("payload", {}).get("model")
                    if isinstance(m, str) and m:
                        model = m
                except Exception:
                    pass
                continue
            if EVENT_MSG_TAG not in head or TOKEN_COUNT_TAG not in line:
                continue
            try:
                o = json.loads(line)
                tot = o["payload"]["info"]["total_token_usage"]
                cin, cout = int(tot.get("input_tokens", 0)), int(tot.get("output_tokens", 0))
                parsed = parse_ts(o["timestamp"])
            except Exception:
                continue
            tin, tout = cin, cout
            if parsed is not None and (first is None or parsed[1] < first):
                first = parsed[1]
    return {"model": model or "unbekannt", "in": tin, "out": tout, "min": first or 0}


def _save_zst(files: dict, cache_path: str) -> None:
    """Write the zst cache atomically."""
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"files": files}, fh)
    os.replace(tmp, cache_path)


def scan_zst(root, cache_path, lock_path) -> int:
    """Scan all *.zst files under root into the zst cache (unchanged files are skipped); returns files read.

    The lock file holds the PID of this process while it runs.
    """
    cache_path, lock_path = str(cache_path), str(lock_path)
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text(str(os.getpid()))
    n = 0
    try:
        files = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    files = json.load(fh).get("files", {})
            except Exception:
                files = {}
        for dp, _dn, names in os.walk(root):
            for nm in names:
                if not nm.endswith(".zst"):
                    continue
                p = os.path.join(dp, nm)
                stat = os.stat(p)
                old = files.get(p)
                if old and old.get("size") == stat.st_size and old.get("mtime") == int(stat.st_mtime):
                    continue
                try:
                    e = scan_zst_file(p)
                except Exception as ex:
                    e = {"model": "unbekannt", "in": 0, "out": 0, "min": 0, "error": str(ex)[:100]}
                e["size"], e["mtime"] = stat.st_size, int(stat.st_mtime)
                files[p] = e
                n += 1
                if n % 10 == 0:
                    _save_zst(files, cache_path)
        _save_zst(files, cache_path)
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass
    return n


def zst_main(argv) -> None:
    """Entry of the background helper: argv = [sessions root, cache json, lock file]."""
    scan_zst(argv[0], argv[1], argv[2])


def start_zst_helper(root, cache_path, lock_path, python_exe: str = ""):
    """Start the zst helper in the background unless one is already running; returns the Popen or None.

    The helper needs compression.zstd: the running Python is used when it has it, else python_exe.
    """
    lock = Path(lock_path)
    if lock.is_file():
        try:
            if osplat.process_alive(int(lock.read_text().strip())):
                return None
        except (ValueError, OSError):
            pass
    exe = python_exe if python_exe else (sys.executable if has_zstd() else "")
    if not exe or not Path(exe).is_file():
        return None
    pkg_root = str(Path(__file__).resolve().parent.parent)
    code = "import sys; sys.path.insert(0, sys.argv[1]); from haivequeen import usage; usage.zst_main(sys.argv[2:])"
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if osplat.is_windows():
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([exe, "-I", "-c", code, pkg_root, str(root), str(cache_path), str(lock_path)], **kw)


def merge_zst_cache(models: list, cache_path) -> None:
    """Add the sums found so far in the zst cache to the Codex model entries of the scan result."""
    try:
        with open(cache_path, encoding="utf-8") as fh:
            files = json.load(fh)["files"]
        for ze in files.values():
            agg = next((m for m in models if m["codex"] and m["name"] == ze["model"]), None)
            if agg is None:
                agg = {"name": str(ze["model"]), "codex": True, "today_in": 0, "today_out": 0, "total_in": 0,
                       "total_out": 0, "since": 0, "has_total": False}
                models.append(agg)
            if int(ze["in"]) > 0 or int(ze["out"]) > 0:
                agg["has_total"] = True
                agg["total_in"] += int(ze["in"])
                agg["total_out"] += int(ze["out"])
            if ze["min"] > 0 and (agg["since"] == 0 or ze["min"] < agg["since"]):
                agg["since"] = ze["min"]
    except Exception:
        pass


# ---------------------------------------------------------------- usage table

def _first_codex_model(codex_cfg: Path):
    """Return the model named in the Codex config file (first `model = "..."` line), or None."""
    if not codex_cfg.is_file():
        return None
    for line in codex_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'^\s*model\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def _since(epoch):
    """Return a local datetime for epoch seconds, or None for 0."""
    return datetime.fromtimestamp(epoch) if epoch else None


def get_usage_table(conn=None, cache_dir=None, project=None, claude_dir=None, codex_dir=None,
                    python_exe: str = "", spawn_zst: bool = True) -> dict:
    """Return the total usage of all Claude Code and Codex sessions as table data.

    conn is the central database (agent rows incl. archived and brain usage rows are read for the "plans"
    sums; None = no plan sums); project limits those rows to one project. Result:
    models = list of dicts {key, label, codex, today{in,out}, plans{in,out}, total{in,out}, since, no_data}
    (Claude first, then Codex, each by total input descending; plans values are None without data),
    retention, retention_claude, retention_codex (texts), stats (seconds, files_seen, files_read,
    bytes_read, cache_written, claude_files, codex_jsonl_files, codex_skipped_files).
    """
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    c_root = Path(claude_dir) if claude_dir else claude_root()
    x_root = Path(codex_dir) if codex_dir else codex_root()
    sc = scan(c_root, x_root, cache / "usage-cache.bin", day_of(datetime.now().date()))

    if sc["codex_other_files"] > 0:
        z_cache, z_lock = cache / "zst-cache.json", cache / "zst-usage.lock"
        if spawn_zst:
            start_zst_helper(x_root, z_cache, z_lock, python_exe)
        if z_cache.is_file():
            merge_zst_cache(sc["models"], z_cache)

    ordered = sorted(sc["models"], key=lambda a: (a["codex"], -a["total_in"]))
    by_key = {}
    for a in ordered:
        by_key[a["name"]] = {
            "key": a["name"], "label": model_label(a["name"], a["codex"]), "codex": a["codex"],
            "today": {"in": a["today_in"], "out": a["today_out"]},
            "plans": {"in": None, "out": None},
            "total": {"in": a["total_in"], "out": a["total_out"]},
            "since": _since(a["since"]), "no_data": False}

    claude_key = next((m["key"] for m in by_key.values() if not m["codex"] and m["key"].lower().startswith("claude-sonnet")),
                      None) or "claude-sonnet-5-5"
    codex_cfg = x_root.parent / "config.toml"
    codex_key = _first_codex_model(codex_cfg) or next((m["key"] for m in by_key.values() if m["codex"]), None)

    # Orchestrator sums (plans): agents and archive without pilot and test runs, plus brain calls of Claude Sonnet.
    sums = {}

    def add(key, tin, tout):
        """Add tokens to the plan sum of a model key."""
        if not key:
            return
        s = sums.setdefault(key, {"in": 0, "out": 0})
        s["in"] += int(tin or 0)
        s["out"] += int(tout or 0)

    if conn is not None:
        seen = set()
        for ag in runtime.list_agents(conn, project, "all"):
            if ag.get("name") is None:
                continue
            if "PILOT-PLAN" in str(ag.get("plan_path") or "").upper() or str(ag["name"]).upper().startswith("ZZTEST"):
                continue
            ident = f"{ag.get('project')}|{ag['name']}|{ag.get('started_at')}"
            if ident in seen:
                continue
            seen.add(ident)
            add(codex_key if ag.get("provider") == "codex" else claude_key, ag.get("tokens_in"), ag.get("tokens_out"))
        for br in runtime.usage_list(conn, kind="brain", project=project):
            if "sonnet" in str(br["model"] or "").lower():
                add(claude_key, br["tokens_in"], br["tokens_out"])
    for k, s in sums.items():
        if k not in by_key:
            is_codex = k == codex_key
            by_key[k] = {"key": k, "label": model_label(k, is_codex), "codex": is_codex,
                         "today": {"in": None, "out": None}, "plans": {"in": None, "out": None},
                         "total": {"in": None, "out": None}, "since": None, "no_data": True}
        by_key[k]["plans"] = {"in": s["in"], "out": s["out"]}

    # Retention texts with evidence (the settings file is only read).
    settings = claude_settings_path()
    days = None
    if settings.is_file():
        try:
            days = json.loads(settings.read_text(encoding="utf-8-sig")).get("cleanupPeriodDays")
        except (ValueError, OSError, AttributeError):
            days = None
    c_old = format_date(datetime.fromtimestamp(sc["claude_oldest"])) if sc["claude_oldest"] is not None else "no file"
    if days is not None:
        ret_claude = f"Claude Code: cleanupPeriodDays = {days} in {settings}, oldest session file from {c_old}."
    else:
        ret_claude = (f"Claude Code: no cleanupPeriodDays in {settings}, default per Claude Code 30 days (unchecked), "
                      f"oldest session file from {c_old}.")
    hit = None
    if codex_cfg.is_file():
        for line in codex_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.search(r"retention|cleanup|max_age|expire|history", line, re.I):
                hit = line.strip()
                break
    x_old = format_date(datetime.fromtimestamp(sc["codex_oldest"])) if sc["codex_oldest"] is not None else "no file"
    cfg_text = f"match in {codex_cfg}: {hit}" if hit else f"no retention setting in {codex_cfg}"
    ret_codex = (f"Codex: {cfg_text}, oldest session file from {x_old} ({sc['codex_oldest_path']}); "
                 f"{sc['codex_other_files']} compressed files (jsonl.zst) are only read and unpacked and counted "
                 f"via zst-cache.json.")
    return {
        "models": list(by_key.values()),
        "retention": f"{ret_claude} {ret_codex}",
        "retention_claude": ret_claude,
        "retention_codex": ret_codex,
        "stats": {"seconds": sc["seconds"], "files_seen": sc["files_seen"], "files_read": sc["files_read"],
                  "bytes_read": sc["bytes_read"], "cache_written": sc["cache_written"],
                  "claude_files": sc["claude_files"], "codex_jsonl_files": sc["codex_jsonl_files"],
                  "codex_skipped_files": sc["codex_other_files"]},
    }


# ---------------------------------------------------------------- 5h / 7d windows and weekly limits

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


def _claude_window(w, with_at: bool, now: int):
    """Return the display dict of one saved Claude window: pct and reset, or a 'new window' marker."""
    if not w:
        return None
    if int(w["resetsAt"]) > now:
        out = {"pct": round(100 * float(w["utilization"])), "reset": format_reset_time(w["resetsAt"])}
        if with_at:
            out["at"] = int(w["resetsAt"])
        return out
    return {"pct": None, "reset": "new window"}


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


def refresh_codex_usage(conn, codex_dir=None, project: str = GLOBAL_PROJECT, min_interval: float = 60.0, force: bool = False) -> bool:
    """Write Codex's 5h/7d windows from the session logs into kv (key usage-codex, same form as usage-claude); True when written.

    Cheap: the logs are read at most once per `min_interval` seconds per folder. Nothing is written when no window is known
    (the old value stays). Without an explicit folder it does nothing in a test runtime (BZZ_DATA_DIR set). Only reads ~/.codex.
    """
    if codex_dir is None and os.environ.get("BZZ_DATA_DIR"):
        return False
    root = str(Path(codex_dir) if codex_dir else codex_root())
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
    if not found:
        return False
    runtime.kv_set(conn, project, CODEX_USAGE_KEY, json.dumps(value))
    return True


def format_usage(u) -> str:
    """Return one display text for a usage value ('27% (until 12:30)' or 'n/a')."""
    if u is None or u.get("pct") is None:
        return "n/a"
    return f"{u['pct']}% (until {u['reset']})"


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


def provider_limits(conn, providers, project: str = GLOBAL_PROJECT, read_pc=None, codex_dir=None) -> dict:
    """Return {provider name: get_provider_limit(...)} for a list of provider dicts (the scheduler's source)."""
    return {p["name"]: get_provider_limit(conn, p, project, read_pc, codex_dir) for p in providers}
