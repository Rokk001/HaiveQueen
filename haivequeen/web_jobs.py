"""Progress of long owner actions, without tying their lifetime to an HTTP request."""
import json
import re
import threading
import time
import uuid

from . import config, filetxn

_lock = threading.RLock()
_jobs = {}
_threads = {}
_closing = False


def _path(token):
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise ValueError('Unknown owner action')
    return config.data_dir() / 'owner-jobs' / (token + '.json')


def _save(job):
    filetxn.atomic_write(_path(job['id']), json.dumps(job, default=str).encode('utf-8'), mode=0o600)


def recover():
    """Called after acquiring the exclusive web-service lock, before accepting work."""
    global _closing
    with _lock:
        if any(thread.is_alive() for thread in _threads.values()):
            raise RuntimeError('Owner actions are still running')
        _jobs.clear()
        _threads.clear()
        _closing = False
        for path in (config.data_dir() / 'owner-jobs').glob('*.json'):
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(job, dict) or job.get('id') != path.stem or type(job.get('running')) is not bool:
                    raise ValueError('Invalid owner action record')
            except (ValueError, OSError):
                # Preserve the damaged evidence and expose uncertainty without blocking the web service.
                _jobs[path.stem] = {'id': path.stem, 'key': 'Unknown owner action', 'running': False,
                                    'interrupted': True, 'result': None, 'progress': '',
                                    'error': 'Saved owner action is unreadable; outcome unknown. Inspect before retrying.'}
                continue
            if job['running']:
                job.update(running=False, interrupted=True,
                           error='Web process ended before completion was recorded; outcome unknown. '
                                 'Inspect any partial effects before retrying.')
                _save(job)
            _jobs[job['id']] = job


def begin_shutdown():
    global _closing
    with _lock:
        _closing = True


def finish_shutdown():
    """Finish our owner workers without cancelling their subprocesses."""
    begin_shutdown()
    with _lock:
        threads = list(_threads.values())
    for thread in threads:
        thread.join()


def recent():
    with _lock:
        return [dict(job) for job in sorted(_jobs.values(), key=lambda job: job.get('started', 0), reverse=True)[:64]]


def start(key, work):
    with _lock:
        if _closing:
            raise ValueError('Web server is stopping; owner actions must finish before restart')
        for token, job in _jobs.items():
            if job['key'] == key and job['running']:
                return status(token)
        if sum(j['running'] for j in _jobs.values()) >= 8:
            raise ValueError('Too many owner actions are running')
        token = uuid.uuid4().hex
        job = {'id': token, 'key': key, 'running': True, 'progress': '', 'result': None, 'error': '',
               'started': time.time()}
        # Refuse side effects if their existence cannot first be recorded.
        _save(job)
        _jobs[token] = job

        def progress(text):
            with _lock:
                job['progress'] = str(text)
                _save(job)

        def run():
            try:
                result = work(progress)
                with _lock:
                    job['result'] = result
            except Exception as exc:
                with _lock:
                    job['error'] = str(exc)
            finally:
                with _lock:
                    job['running'] = False
                    job['ended'] = time.time()
                    try:
                        _save(job)
                    except OSError as exc:
                        job['error'] += ' Completion could not be saved: ' + str(exc)
        thread = threading.Thread(target=run, daemon=False)
        _threads[token] = thread
        try:
            thread.start()
        except Exception:
            _threads.pop(token, None)
            job.update(running=False, error='Owner worker could not start', ended=time.time())
            _save(job)
            raise
        for old in list(_jobs):
            if len(_jobs) <= 64:
                break
            if not _jobs[old]['running']:
                _jobs.pop(old)
                _threads.pop(old, None)
        return status(token)


def status(token):
    with _lock:
        if token not in _jobs:
            try:
                return json.loads(_path(token).read_text(encoding='utf-8'))
            except FileNotFoundError:
                raise ValueError('Unknown owner action') from None
        return dict(_jobs[token])
