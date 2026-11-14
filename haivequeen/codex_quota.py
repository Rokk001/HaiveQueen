"""Read account quota metadata over Codex's stdio API, without creating a turn."""
import json
import queue
import shutil
import subprocess
import threading
import time

from . import platform as osplat, profile


def read(provider=None, timeout=15):
    provider = {**(provider or {}), 'protocol': 'codex'}
    command = shutil.which(str(provider.get('command') or 'codex'))
    if not command:
        raise OSError('Codex CLI unavailable')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if osplat.is_windows() else {}
    process = subprocess.Popen([command, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                               env=profile.environment(provider), **options)
    replies = queue.Queue(maxsize=32)
    stopped = threading.Event()
    deadline = time.monotonic() + timeout

    def enqueue(message):
        while not stopped.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                replies.put(message, timeout=min(.05, remaining))
                return True
            except queue.Full:
                continue
        return False

    def receive():
        try:
            for line in process.stdout:
                if stopped.is_set() or time.monotonic() >= deadline:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not enqueue(message):
                    break
        except OSError:
            pass
        finally:
            enqueue(None)
            process.stdout.close()

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    def send(message):
        process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()
    def response(identifier):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Codex quota request timed out')
            try:
                message = replies.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError('Codex quota request timed out') from exc
            if message is None:
                raise OSError('Codex quota connection closed')
            if isinstance(message, dict) and message.get('id') == identifier:
                if 'error' in message:
                    raise OSError('Codex quota request failed')
                return message.get('result') or {}
    try:
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'haivequeen', 'version': '2.0.0'}}})
        response(1)
        send({'method': 'initialized', 'params': {}})
        send({'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
        return response(2)
    finally:
        stopped.set()
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()  # only the metadata helper created above
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        # The reader owns stdout: closing it here could block on its read lock.
        reader.join(timeout=2)
