"""Append-only action journals: a start, uncut output, and a terminal result."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import platform as osplat


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
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def read(path):
    """Ignore an incomplete last record while a writer is appending it."""
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            try:
                records.append(json.loads(line))
            except ValueError:
                break
    return records


def finish(path, code, result=None, message=""):
    if not path or any(r["event"] == "end" for r in read(path)):
        return
    _write(path, {"event": "end", "ended_at": _now(), "exit_code": code,
                  "result": result or ("succeeded" if code == 0 else "failed"), "message": message})


def output(path):
    return "".join(r["text"] for r in read(path) if r["event"] == "output")
