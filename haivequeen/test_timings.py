"""Incremental duration summaries from durable journals, without rerunning tests."""
import collections
import heapq
import re
import threading
from pathlib import Path

from . import action_logs

_cache = collections.OrderedDict()
_lock = threading.RLock()


def clear_cache():
    with _lock:
        _cache.clear()


def parse(line):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)s\s+(call|setup|teardown)\s+(.+)', line)
    if match:
        return float(match[1]), match[3] + ' (' + match[2] + ')'
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)s\s+(test\S+\s+\(.+\))\s*', line)
    if match:
        return float(match[1]), match[2]
    match = re.fullmatch(r'\s*(\S+\.py)\s+exit=-?\d+\s+seconds=(\d+(?:\.\d+)?)\s*', line)
    if match:
        return float(match[2]), match[1]
    return None


def summary(path, threshold):
    """Bound scan work and presentation; journal is the durable source of truth."""
    with _lock:
        stat = Path(path).stat()
        key = (str(path), threshold)
        state = _cache.get(key)
        if state is None or stat.st_size < state['size']:
            state = dict(cursor=None, carry='', rows=[], count=0, measured=0, complete=False, size=0, discarded=False)
            _cache[key] = state
        _cache.move_to_end(key)
        while len(_cache) > 32:
            _cache.popitem(last=False)
        if state['size'] != stat.st_size or not state['complete']:
            for _ in range(8):
                page = action_logs.page(path, state['cursor'])
                text = state['carry'] + page['output']
                lines = text.split('\n')
                state['carry'] = lines.pop()
                if not page['more'] and action_logs.metadata(path)[-1].get('event') == 'end':
                    lines.append(state['carry'])
                    state['carry'] = ''
                if state['discarded'] and lines:
                    lines.pop(0)
                    state['discarded'] = False
                for line in lines:
                    row = parse(line.rstrip('\r'))
                    if row:
                        state['measured'] += 1
                        if row[0] >= threshold:
                            state['count'] += 1
                            heapq.heappush(state['rows'], row)
                            if len(state['rows']) > 100:
                                heapq.heappop(state['rows'])
                # Avoid retaining an arbitrarily large unbroken log line.
                if len(state['carry']) > 8192:
                    state['carry'] = ''
                    state['discarded'] = True
                state['cursor'] = page['cursor']
                state['complete'] = not page['more']
                if not page['more']:
                    break
            state['size'] = stat.st_size
        return {'rows': [{'seconds': seconds, 'name': name} for seconds, name in sorted(state['rows'], reverse=True)],
                'count': state['count'], 'measured': state['measured'], 'complete': state['complete'],
                'omitted': max(0, state['count'] - len(state['rows'])), 'threshold': threshold}
