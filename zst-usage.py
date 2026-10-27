# zst-usage.py - reads the compressed Codex sessions (*.jsonl.zst, read only) and stores per file the model, the last cumulative
# token totals and the first token timestamp in a cache file. Same rules as the C# scanner for *.jsonl: model = last turn_context,
# total = last total_token_usage of the file. Usage: python -I zst-usage.py <sessions root> <cache json> <lock file>
import json, os, sys
from datetime import datetime
from compression import zstd

root, cache_path, lock_path = sys.argv[1], sys.argv[2], sys.argv[3]
EPOCH = datetime(1, 1, 1)

# Local .NET ticks (100 ns since 0001-01-01) of an ISO timestamp, or None.
def ticks_of(ts):
    try:
        d = datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except Exception:
        return None
    t = d - EPOCH
    return t.days * 864000000000 + t.seconds * 10000000 + t.microseconds * 10

# Reads one compressed session file; returns the cache entry.
def scan_file(path):
    model, tin, tout, first = None, 0, 0, None
    with zstd.open(path, 'rb') as f:
        for line in f:
            head = line[:200]
            if b'"type":"turn_context"' in head:
                try:
                    m = json.loads(line).get('payload', {}).get('model')
                    if isinstance(m, str) and m:
                        model = m
                except Exception:
                    pass
                continue
            if b'"type":"event_msg"' not in head or b'"token_count"' not in line:
                continue
            try:
                o = json.loads(line)
                p = o['payload']
                tot = p['info']['total_token_usage']
                cin, cout = int(tot.get('input_tokens', 0)), int(tot.get('output_tokens', 0))
                tk = ticks_of(o['timestamp'])
            except Exception:
                continue
            tin, tout = cin, cout
            if tk is not None and (first is None or tk < first):
                first = tk
    return {'model': model or 'unbekannt', 'in': tin, 'out': tout, 'min': first or 0}

# Writes the cache atomically.
def save(files):
    tmp = cache_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'files': files}, f)
    os.replace(tmp, cache_path)

with open(lock_path, 'w') as f:
    f.write(str(os.getpid()))
try:
    files = {}
    if os.path.exists(cache_path):
        try:
            files = json.load(open(cache_path, encoding='utf-8')).get('files', {})
        except Exception:
            files = {}
    n = 0
    for dp, _, names in os.walk(root):
        for nm in names:
            if not nm.endswith('.zst'):
                continue
            p = os.path.join(dp, nm)
            st = os.stat(p)
            old = files.get(p)
            if old and old.get('size') == st.st_size and old.get('mtime') == int(st.st_mtime):
                continue
            try:
                e = scan_file(p)
            except Exception as ex:
                e = {'model': 'unbekannt', 'in': 0, 'out': 0, 'min': 0, 'error': str(ex)[:100]}
            e['size'] = st.st_size; e['mtime'] = int(st.st_mtime)
            files[p] = e
            n += 1
            if n % 10 == 0:
                save(files)
    save(files)
finally:
    try: os.remove(lock_path)
    except OSError: pass
