"""Progress of long owner actions, without tying their lifetime to an HTTP request."""
import threading
import uuid

_lock = threading.RLock()
_jobs = {}


def start(key, work):
    with _lock:
        for token, job in _jobs.items():
            if job['key'] == key and job['running']:
                return status(token)
        if sum(j['running'] for j in _jobs.values()) >= 8:
            raise ValueError('Too many owner actions are running')
        token = uuid.uuid4().hex
        job = {'id': token, 'key': key, 'running': True, 'progress': '', 'result': None, 'error': ''}
        _jobs[token] = job

        def progress(text):
            with _lock:
                job['progress'] = str(text)

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
        threading.Thread(target=run, daemon=True).start()
        for old in list(_jobs):
            if len(_jobs) <= 64:
                break
            if not _jobs[old]['running']:
                _jobs.pop(old)
        return status(token)


def status(token):
    with _lock:
        if token not in _jobs:
            raise ValueError('Unknown owner action')
        return dict(_jobs[token])
