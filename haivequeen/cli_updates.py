"""Read-only provider CLI update notices; never run an installer."""
import json
import re
import subprocess
import threading
import time
from urllib.request import urlopen
from urllib.parse import quote
from . import profile

PACKAGES = {'claude': '@anthropic-ai/claude-code', 'codex': '@openai/codex',
            'opencode': '@opencode/cli', 'kilo': '@kilocode/cli'}
_cache = {}
_lock = threading.RLock()


def version(text):
    match = re.search(r'(?<![\w.])(\d+)\.(\d+)\.(\d+)(?![\w.+-])', str(text))
    return tuple(map(int, match.groups())) if match else None


def latest(package):
    with urlopen('https://registry.npmjs.org/' + quote(package, safe='') + '/latest', timeout=8) as response:
        return json.loads(response.read(262144))['version']


def check(provider):
    protocol = provider.get('protocol')
    if protocol not in PACKAGES:
        return {'status': 'unsupported', 'message': 'Update detection is unavailable for this CLI.'}
    try:
        result = subprocess.run([str(provider.get('command') or protocol).strip('"'), '--version'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=8, env=profile.environment(provider))
        installed = version(result.stdout)
        available_text = latest(PACKAGES[protocol])
        available = version(available_text)
        if result.returncode or installed is None or available is None:
            raise ValueError('Version could not be determined')
        return {'status': 'update' if available > installed else 'current',
                'installed': '.'.join(map(str, installed)), 'latest': available_text,
                'message': 'Updates are reported only; installation is manual.'}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return {'status': 'unknown', 'message': 'Version check unavailable. Try again later.'}


def notice(provider, refresh=False):
    """Keep network/process work off the snapshot thread; check at most every six hours."""
    key = (provider.get('protocol'), provider.get('command'))
    with _lock:
        entry = _cache.get(key)
        if entry and entry['checking']:
            return dict(entry['result'])
        if entry and not refresh and time.monotonic() - entry['time'] < 21600:
            return dict(entry['result'])
        entry = {'time': time.monotonic(), 'checking': True, 'result': {'status':'checking', 'message':'Checking CLI version'}}
        _cache[key] = entry
        def run():
            value = check(provider)
            with _lock:
                entry.update(result=value, checking=False, time=time.monotonic())
        threading.Thread(target=run, daemon=True).start()
        return dict(entry['result'])
