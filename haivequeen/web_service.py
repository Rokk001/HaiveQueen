"""Independent web process and authenticated loopback lifecycle control."""
import http.client
import http.server
import json
import os
import queue
import secrets
import subprocess
import threading
import time
from pathlib import Path

from . import config, filetxn, web_auth, web_server
from . import platform as osplat

_children = {}


def state_path():
    return config.data_dir() / 'web-service.json'


def control(action):
    """Contact only the service that holds the secret in our local state file."""
    try:
        state = json.loads(state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    connection = http.client.HTTPConnection('127.0.0.1', state['control_port'], timeout=20)
    try:
        connection.request('POST', '/' + action, headers={'Authorization': 'Bearer ' + state['token']})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError('Web service control refused the request')
        return json.loads(response.read())
    except ConnectionRefusedError:
        return None
    finally:
        connection.close()


class Service:
    def __init__(self):
        self.path = config.config_dir() / 'config.toml'
        self.public = None
        self.worker = None
        self.error = ''
        self.stopping = False
        self.restarting = False
        self.actions = queue.Queue()
        self.token = secrets.token_urlsafe(32)
        service = self

        class Control(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + service.token):
                    self.send_error(403)
                    return
                action = self.path[1:]
                if action == 'stop':
                    service.stopping = True
                elif action == 'reload':
                    service.reload()
                elif action != 'status':
                    self.send_error(404)
                    return
                body = json.dumps(service.status()).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.control = http.server.HTTPServer(('127.0.0.1', 0), Control)
        self.control.timeout = 0.2

    def status(self):
        return {'ok': not bool(self.error), 'running': self.public is not None and not self.stopping,
                'pid': os.getpid(), 'url': self.url if self.public else '', 'error': self.error}

    def close_listener(self):
        if self.public:
            self.public.shutdown()
            self.worker.join()
            self.public.server_close()
            self.public = None

    def reload(self):
        self.close_listener()
        try:
            self.settings = web_auth.read(self.path)
            self.public = web_server.make_server(self.settings)
            self.public.config_path = self.path
            self.public.lifecycle = self.actions.put
            self.worker = threading.Thread(target=self.public.serve_forever, daemon=True)
            self.worker.start()
            address = self.public.server_address[0]
            host = '127.0.0.1' if address == '0.0.0.0' else address
            self.url = '%s://%s:%s' % ('https' if self.settings['certificate'] else 'http', host, self.public.server_address[1])
            self.error = ''
        except (OSError, ValueError, TypeError) as exc:
            self.error = 'Web server could not start: ' + str(exc)
        return self.status()

    def run(self):
        from . import loop, proctree
        lock = state_path().with_suffix('.lock')
        holder = loop._take_pid_lock(lock, own_ok=False)
        if holder:
            self.control.server_close()
            raise OSError('Web service is already running or starting')
        try:
            self.reload()
            state = {'control_port': self.control.server_address[1], 'token': self.token}
            filetxn.atomic_write(state_path(), json.dumps(state).encode())
            if os.name != 'nt':
                state_path().chmod(0o600)
            print(json.dumps(self.status()), flush=True)
            if self.error:
                return 1
            # Direct foreground serving also owns the Windows tray lifecycle.
            # Publish the running service first so the tray can discover it.
            from . import tray
            tray.ensure_started()
            while not self.stopping:
                self.control.handle_request()
                while not self.actions.empty():
                    action = self.actions.get_nowait()
                    if action == 'stop':
                        self.stopping = True
                    elif action == 'restart':
                        self.restarting = True
                        self.stopping = True
                    elif action == 'reload':
                        self.reload()
                # Settings remain the only source of truth, including edits from W2.
                if not self.stopping and web_auth.read(self.path) != self.settings:
                    self.reload()
        finally:
            self.close_listener()
            from . import web_login
            web_login.close()
            self.control.server_close()
            state_path().unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            proctree.exempt_clear()
        if self.restarting:
            start()
        return 0


def start():
    running = control('status')
    if running and running['running']:
        from . import tray
        tray.ensure_started()
        return running
    if running is not None:
        result = control('reload')
        if result and result['ok']:
            return result
        raise OSError((result or running).get('error') or 'Web server is stopping')
    argv = [osplat.python_executable(), str(Path(__file__).with_name('cli.py')), 'web', 'serve']
    options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
    # stdout carries exactly one readiness record; the child does not inherit a terminal.
    log = config.data_dir() / 'web-service.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop('BZZ_AGENT', None)
    environment.pop('BZZ_UPDATE_RESTART', None)
    with log.open('ab') as errors:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, env=environment, **options)
    from . import proctree
    proctree.exempt_register(process.pid)
    result = queue.Queue()

    def ready():
        result.put(process.stdout.readline())
        process.stdout.close()

    threading.Thread(target=ready, daemon=True).start()
    try:
        line = result.get(timeout=25)
    except queue.Empty as exc:
        # Only this new child is ours to end; never terminate another service.
        process.terminate()
        process.wait(timeout=10)
        raise OSError('Web server startup timed out; see web-service.log') from exc
    if not line:
        process.wait(timeout=10)
        # A concurrent caller may have won the service lock.
        running = control('status')
        if running and running['running']:
            return running
        raise OSError('Web server startup failed; see web-service.log')
    status = json.loads(line)
    if not status['ok']:
        process.wait(timeout=10)
        raise OSError(status['error'])
    from . import tray
    tray.ensure_started()
    _children[process.pid] = process
    def reap():
        process.wait()
        _children.pop(process.pid, None)
    threading.Thread(target=reap, daemon=True).start()
    return status


def stop():
    result = control('stop')
    if result:
        child = _children.get(result['pid'])
        if child:
            child.wait(timeout=20)
        else:
            deadline = time.monotonic() + 20
            if os.name == 'nt':
                import ctypes
                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.restype = ctypes.c_void_p
                handle = kernel.OpenProcess(0x00100000, False, result['pid'])  # SYNCHRONIZE; never terminate a process
                if handle:
                    try:
                        if kernel.WaitForSingleObject(ctypes.c_void_p(handle), 20_000) != 0:
                            raise OSError('Web server has not finished stopping')
                    finally:
                        kernel.CloseHandle(ctypes.c_void_p(handle))
            else:
                while osplat.process_alive(result['pid']) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if osplat.process_alive(result['pid']):
                    raise OSError('Web server has not finished stopping')
    return result or {'ok': True, 'running': False}


def restart():
    stop()
    return start()


def open_browser():
    import webbrowser
    status = start()
    if not webbrowser.open(status['url']):
        raise OSError('Could not open the web interface in a browser')
    return {'web': status['url']}
