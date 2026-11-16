"""Provider login subprocesses whose output and input belong to the web session."""
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

_lock = threading.RLock()
_runs = {}
INPUT_TIMEOUT = 2.0


def command(provider):
    from . import profile
    if provider.get('protocol') == 'codex':
        return str(provider.get('command') or 'codex') + ' login --device-auth'
    return profile.login_command(provider)


def start(provider):
    from . import profile
    with _lock:
        for token, run in _runs.items():
            if run['provider'] == provider['name'] and run['process'].poll() is None:
                return status(token)
        line = command(provider)
        if not line or not profile.prepare(provider):
            raise ValueError('This provider has no configured login command')
        env = profile.environment(provider)
        # Browser launchers print the URL back into the captured stream.
        env['BROWSER'] = '"%s" "%s" --browser-url' % (sys.executable, Path(__file__).resolve())
        argv = (subprocess.list2cmdline([os.environ.get('COMSPEC', 'cmd.exe')]) + ' /d /s /c "' + line + '"') if os.name == 'nt' else ['sh', '-c', line]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, bufsize=0, **options)
        token = uuid.uuid4().hex
        run = {'provider': provider['name'], 'process': process, 'output': '', 'complete': False}
        _runs[token] = run

        def read():
            import codecs
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            try:
                while chunk := process.stdout.read(1024):
                    with _lock:
                        run['output'] = (run['output'] + decoder.decode(chunk))[-128 * 1024:]
            finally:
                process.stdout.close()
                process.wait()
                process.stdin.close()
                with _lock:
                    run['complete'] = True
        threading.Thread(target=read, daemon=True).start()
        # Keep completed output bounded across repeated logins.
        for old in list(_runs):
            if len(_runs) <= 32:
                break
            if _runs[old]['complete']:
                _runs.pop(old)
        return status(token)


def status(token):
    with _lock:
        run = _runs.get(token)
        if run is None:
            raise ValueError('Unknown provider login')
        output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', run['output'])
        return {'ok': True, 'id': token, 'provider': run['provider'], 'output': output,
                'links': re.findall(r'https?://[^\s<>"\x00-\x1f]+', output),
                'running': not run['complete'], 'exit': run['process'].poll()}


def send(token, text):
    if not isinstance(text, str) or len(text) > 8192 or '\n' in text or '\r' in text:
        raise ValueError('Enter a single line of at most 8192 characters')
    with _lock:
        run = _runs.get(token)
        if not run or run['process'].poll() is not None:
            raise ValueError('Provider login is no longer running')
        if run.get('input_done') and not run['input_done'].is_set():
            raise ValueError('Previous login input is still pending; wait or close the login')
        done = threading.Event()
        errors = []
        run['input_done'] = done
        stream = run['process'].stdin
        payload = (text + '\n').encode('utf-8')

        def write():
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = stream.write(remaining)
                    if not written:
                        raise OSError('Login input pipe accepted no data')
                    remaining = remaining[written:]
                stream.flush()
            except (OSError, ValueError) as exc:
                errors.append(exc)
            finally:
                done.set()
        threading.Thread(target=write, daemon=True).start()
    # Never wait for a pipe while holding the shared session lock. Only one
    # bounded input may be pending per session, so retries cannot spawn writers.
    if not done.wait(INPUT_TIMEOUT):
        raise ValueError('Login input is still pending; do not resend it')
    if errors:
        raise ValueError('Provider login no longer accepts input') from errors[0]
    return status(token)


def close():
    from . import platform as osplat
    with _lock:
        processes = [r['process'] for r in _runs.values() if r['process'].poll() is None]
    for process in processes:
        osplat.kill_process_tree(process.pid)
        process.wait(timeout=10)


if __name__ == '__main__' and sys.argv[1:2] == ['--browser-url']:
    print(sys.argv[-1], flush=True)
