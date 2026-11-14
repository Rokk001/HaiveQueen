"""Append-only action journals: a start, uncut output, and a terminal result."""
import collections
import logging
import threading
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import platform as osplat


_fallback = collections.OrderedDict()
_fallback_lock = threading.RLock()

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _component(value):
    return re.sub(r"[^\w.-]", "_", value)[-60:] + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def start(project, plan, step):
    started = _now()
    folder = osplat.data_dir() / "orchestrator" / "logs" / _component(project) / _component(plan)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (started.replace(":", "-") + "-" + step + "-" + uuid.uuid4().hex + ".jsonl")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "start", "project": project, "plan": plan, "step": step,
                                 "started_at": started, "result": "running"}) + "\n")
    return str(path)


def append(path, text):
    if path and text:
        _write(path, {"event": "output", "time": _now(), "text": str(text)})


def _write(path, record):
    key = str(path)
    with _fallback_lock:
        if key not in _fallback:
            try:
                with Path(path).open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record) + '\n')
                return
            except OSError as exc:
                message = 'Log storage error: %s. Only a bounded in-memory output tail is available; it is not durable.\n' % exc
                _fallback[key] = (message, collections.deque(maxlen=128))
                while len(_fallback) > 16:
                    _fallback.popitem(last=False)
                logging.getLogger(__name__).error('%s', message.rstrip())
        # Do not let a failed journal stop draining the command pipe. Keep a
        # bounded tail and terminal status, explicitly marked as non-durable.
        item = dict(record)
        if 'text' in item:
            item['text'] = item['text'][-4096:]
        _fallback[key][1].append(item)


def read(path):
    """Ignore an incomplete last record while a writer is appending it."""
    records = []
    with _fallback_lock:
        stored = _fallback.get(str(path))
        fallback = (stored[0], list(stored[1])) if stored else None
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    break
    except OSError:
        if fallback is None:
            raise
    if fallback:
        message, tail = fallback
        records.append({'event': 'output', 'text': message})
        records.extend(tail)
    return records


def finish(path, code, result=None, message=""):
    if not path or any(r["event"] == "end" for r in read(path)):
        return
    _write(path, {"event": "end", "ended_at": _now(), "exit_code": code,
                  "result": result or ("succeeded" if code == 0 else "failed"), "message": message})


def output(path):
    return "".join(r["text"] for r in read(path) if r["event"] == "output")


def latest(project, plan, step):
    """Read the latest journal for precisely this project, plan and step."""
    folder = osplat.data_dir() / "orchestrator" / "logs" / _component(project) / _component(plan)
    for path in sorted(folder.glob("*-" + step + "-*.jsonl"), reverse=True):
        records = read(path)
        if not records:
            continue  # The writer may not have flushed its start record yet.
        start_record = records[0]
        if any(start_record.get(key) != value for key, value in
               (("event", "start"), ("project", project), ("plan", plan), ("step", step))):
            continue
        end = next((r for r in reversed(records) if r.get("event") == "end"), {})
        return {"path": str(path), "started_at": start_record.get("started_at"),
                "ended_at": end.get("ended_at"), "result": end.get("result"),
                "exit_code": end.get("exit_code"), "message": end.get("message", ""),
                "output": "".join(r.get("text", "") for r in records if r.get("event") == "output")}
    return None
