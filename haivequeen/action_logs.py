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
_metadata_cache = collections.OrderedDict()

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
                with Path(path).open('ab+') as stream:
                    size = stream.seek(0, 2)
                    if size:
                        stream.seek(-1, 2)
                        if stream.read(1) != b'\n':
                            stream.write(b'\n')  # isolate an interrupted previous write
                    stream.write((json.dumps(record) + '\n').encode('utf-8'))
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


def _decode_record(line):
    record = json.loads(line)
    if not isinstance(record, dict) or record.get('event') not in ('start', 'output', 'end'):
        raise ValueError('Invalid journal record')
    if record['event'] == 'output' and not isinstance(record.get('text'), str):
        raise ValueError('Invalid output record')
    return record


def _damage(offset):
    return '[Warning: damaged log record at byte %d; continuing with recoverable output.]\n' % offset


def read(path):
    """Ignore an incomplete last record while a writer is appending it."""
    records = []
    with _fallback_lock:
        stored = _fallback.get(str(path))
        fallback = (stored[0], list(stored[1])) if stored else None
    try:
        with Path(path).open('rb') as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b'\n'):
                    break
                try:
                    records.append(_decode_record(line))
                except ValueError:
                    records.append({'event': 'output', 'text': _damage(offset)})
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


def _string_header(prefix, field, event):
    match = re.search(rb'"' + field.encode() + rb'"\s*:\s*"', prefix)
    if not match:
        return None
    try:
        header = json.loads(prefix[:match.start()] + ('"' + field + '":""}').encode())
    except ValueError:
        return None
    return (header, match.end()) if header.get('event') == event else None


def _output_slice(stream, start, prefix, skip, limit, field='text', event='output'):
    """Decode a bounded slice of the writer's final JSON text field."""
    parsed = _string_header(prefix, field, event)
    if parsed is None:
        return None
    position = skip or parsed[1]
    stream.seek(start + position)
    raw = stream.read(limit + 12)
    index, closed = 0, False
    while index < len(raw) and index < limit:
        byte = raw[index]
        if byte == 34:
            closed = True
            break
        if byte == 92:
            if index + 1 >= len(raw):
                break
            width = 6 if raw[index + 1] == 117 else 2
            if width == 6 and index + 6 <= len(raw):
                try:
                    high = int(raw[index + 2:index + 6], 16)
                except ValueError:
                    break
                if 0xD800 <= high <= 0xDBFF:
                    width = 12  # retain a UTF-16 surrogate pair as one code point
        else:
            width = 1 if byte < 128 else 2 if byte < 224 else 3 if byte < 240 else 4
        if index + width > len(raw):
            break
        index += width
    text = json.loads(b'"' + raw[:index] + b'"')
    if closed:
        stream.seek(start + position + index + 1)
        suffix = stream.readline(65536)
        if not suffix.endswith(b'\n'):
            return text, start, position + index, True
        if suffix.strip() != b'}':
            raise ValueError('Invalid output record ending')
        return text, stream.tell(), 0, False
    return text, start, position + index, index < limit


def page(path, cursor=None, limit=32768):
    """Return one output page; cursor offsets locate the record and encoded text.

    A partially written record is retried on the next poll. The journal remains
    unchanged and callers can retain page-start cursors to revisit history.
    """
    limit = max(1, min(int(limit), 32768))
    try:
        offset, skip = cursor if cursor is not None else (0, 0)
        offset, skip = int(offset), int(skip)
        if offset < 0 or skip < 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Invalid log cursor') from None
    parts, length, end, partial = [], 0, {}, False
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        if offset > size:
            raise ValueError('Log cursor is beyond the file')
        stream.seek(offset)
        read_from = offset
        while length < limit and stream.tell() - read_from < 256 * 1024:
            start = stream.tell()
            line = stream.readline(8192)
            if not line:
                break
            if not line.endswith(b'\n') and len(line) < 8192:
                partial = True
                break
            try:
                sliced = _output_slice(stream, start, line, skip, limit - length)
                if sliced is None:
                    sliced = _output_slice(stream, start, line, skip, limit - length, 'message', 'end')
                    if sliced is not None and not sliced[2] and not sliced[3]:
                        end = _string_header(line, 'message', 'end')[0]
            except ValueError:
                sliced = None
            if sliced is not None:
                text, offset, skip, partial = sliced
                parts.append(text)
                length += len(text)
                if skip:
                    break
                continue
            stream.seek(start + len(line))
            try:
                record = _decode_record(line)
            except (ValueError, UnicodeError):
                warning = _damage(start)[:limit - length]
                parts.append(warning)
                length += len(warning)
                offset, skip = stream.tell(), 0
                continue
            if record.get('event') == 'output':
                text = record.get('text', '')
                text = text if isinstance(text, str) else str(text)
                piece = text[skip:skip + limit - length]
                parts.append(piece)
                length += len(piece)
                if skip + len(piece) < len(text):
                    offset, skip = start, skip + len(piece)
                    break
            elif record.get('event') == 'end':
                end = record
            offset, skip = stream.tell(), 0
    if offset == size and not partial:
        with _fallback_lock:
            stored = _fallback.get(str(path))
            if stored:
                message, tail = stored
                text = ''.join(r.get('text', '') for r in tail if r.get('event') == 'output')
                end = next((r for r in reversed(tail) if r.get('event') == 'end'), end)
                warning = message[:limit]
                remaining = limit - len(warning)
                return {'output': warning + (text[-remaining:] if remaining else ''),
                        'cursor': [offset, 0], 'more': False, 'end': end, 'reset': True}
    return {'output': ''.join(parts), 'cursor': [offset, skip],
            'more': not partial and (offset < size or skip > 0), 'end': end}


def _last_complete_record(stream, size):
    """Locate the last complete line with fixed-size reads and constant memory."""
    position, end = size, None
    while position:
        begin = max(0, position - 65536)
        stream.seek(begin)
        block = stream.read(position - begin)
        if end is None:
            index = block.rfind(b'\n')
            if index < 0:
                position = begin
                continue
            end = begin + index + 1
            block = block[:index]
        index = block.rfind(b'\n')
        if index >= 0:
            return begin + index + 1, end
        position = begin
    return (0, end) if end is not None else (0, 0)


def metadata(path):
    """Read start/terminal fields, caching unchanged files without output history."""
    stat = Path(path).stat()
    stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    with _fallback_lock:
        cached = _metadata_cache.get(str(path))
        if cached and cached[0] == stamp and str(path) not in _fallback:
            return [dict(record) for record in cached[1]]
    with Path(path).open('rb') as stream:
        first = stream.readline(65536)
        if not first.endswith(b'\n'):
            return []
        try:
            start_record = json.loads(first)
        except ValueError:
            return []
        if not isinstance(start_record, dict):
            return []
        size = stream.seek(0, 2)
        records = [start_record]
        while size > len(first):
            begin, finish = _last_complete_record(stream, size)
            if not finish:
                break
            stream.seek(begin)
            line = stream.read(min(65536, finish - begin))
            try:
                record = _decode_record(line)
            except ValueError:
                parsed = _string_header(line, 'message', 'end')
                if parsed:
                    record = parsed[0]
                    skip = 0
                    try:
                        while True:
                            text, following, skip, partial = _output_slice(
                                stream, begin, line, skip, 32768, 'message', 'end')
                            if not record['message']:
                                record['message'] = text[:4096]
                            if partial:
                                raise ValueError('Incomplete terminal record')
                            if not skip:
                                if following != finish:
                                    raise ValueError('Invalid terminal boundary')
                                break
                    except ValueError:
                        size = begin
                        continue
                elif _string_header(line, 'text', 'output'):
                    break  # normal output is not a terminal record
                else:
                    size = begin
                    continue
            if record.get('event') == 'end':
                records.append(record)
            break
    with _fallback_lock:
        _metadata_cache[str(path)] = (stamp, records)
        _metadata_cache.move_to_end(str(path))
        while len(_metadata_cache) > 128:
            _metadata_cache.popitem(last=False)
        fallback = _fallback.get(str(path))
        if fallback:
            records.extend(r for r in fallback[1] if r.get('event') == 'end')
    return records


def latest(project, plan, step, *, metadata_only=False):
    """Read the latest journal for precisely this project, plan and step."""
    folder = osplat.data_dir() / "orchestrator" / "logs" / _component(project) / _component(plan)
    for path in sorted(folder.glob("*-" + step + "-*.jsonl"), reverse=True):
        records = metadata(path) if metadata_only else read(path)
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
