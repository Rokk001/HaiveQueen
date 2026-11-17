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

from . import config, filetxn, settings, web_auth, web_jobs, web_server
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


class _ControlServer(http.server.HTTPServer):
    # HTTPServer.timeout only limits accept(), not reads from an accepted client.
    request_timeout = 2.0

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        return connection, address


class Service:
    def __init__(self):
        self.path = config.config_dir() / 'config.toml'
        self.public = None
        self.worker = None
        self.error = ''
        self.stopping = False
        self.restarting = False
        self.version_handover = False
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

        self.control = _ControlServer(('127.0.0.1', 0), Control)
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

    def _serve(self, server, candidate):
        self.public = server
        self.settings = candidate
        server.config_path = self.path
        server.lifecycle = self.actions.put

        def api(action, data):
            result = server.engine_api(action, data)
            if action == 'state':
                result = {**result, 'web_listener_error': self.error}
            return result

        server.api = api
        self.worker = threading.Thread(target=server.serve_forever, daemon=True)
        self.worker.start()
        address = server.server_address[0]
        host = '127.0.0.1' if address == '0.0.0.0' else address
        self.url = '%s://%s:%s' % ('https' if candidate['certificate'] else 'http', host, server.server_address[1])

    @filetxn.coordinated
    def reload(self):
        previous = dict(self.settings) if self.public else None
        try:
            candidate = web_auth.read(self.path)
            web_server.validate_tls(candidate)
            # Same-port address changes can overlap our current wildcard binding.
            # Other ports can be acquired before releasing the working listener.
            if self.public and candidate['port'] == self.public.server_address[1]:
                self.close_listener()
            replacement = web_server.make_server(candidate)
            self.close_listener()
            self._serve(replacement, candidate)
            self.error = ''
        except (OSError, ValueError, TypeError) as exc:
            self.error = 'Web server could not start: ' + str(exc)
            if previous is not None:
                try:
                    data = settings.load_raw(self.path)
                    data['web'] = previous
                    settings.save_file(self.path, data)
                    self.settings = previous
                    if self.public is None:
                        self._serve(web_server.make_server(previous), previous)
                    self.error += '. Previous web settings restored.'
                except (OSError, ValueError, TypeError) as recovery:
                    self.error += '. Restoring previous web settings failed: ' + str(recovery)
        return self.status()

    def run(self):
        from . import loop, proctree
        lock = state_path().with_suffix('.lock')
        holder = loop._take_pid_lock(lock, own_ok=False)
        if holder:
            self.control.server_close()
            raise OSError('Web service is already running or starting')
        try:
            web_jobs.recover()
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
            watched = time.monotonic()
            while not self.stopping:
                self.control.handle_request()
                if time.monotonic() - watched >= loop.LOOP_WATCH_SECONDS:
                    watched = time.monotonic()
                    _watch_loop()
                if loop.version_switch_requested():
                    self.version_handover = True
                    self.stopping = True
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
            web_jobs.begin_shutdown()
            self.close_listener()
            web_jobs.finish_shutdown()
            from . import web_login
            web_login.close()
            self.control.server_close()
            state_path().unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
            proctree.exempt_clear()
        if self.version_handover:
            # The stable launcher resolves the current pointer and reserves its
            # version. Start only after releasing the old service's lock.
            launcher = loop.TOOLS_DIR.parent.parent / 'launcher.py'
            options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS} if os.name == 'nt' else {'start_new_session': True}
            osplat.popen_outside_job(subprocess.Popen, [osplat.python_executable(), '-I', str(launcher), 'web', 'start'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        elif self.restarting:
            start()
        return 0


def _watch_loop():
    """The web service outlives an agent's job: a loop that ended unexpectedly is reported and restarted from here."""
    from contextlib import closing
    from . import db, loop
    try:
        with closing(db.connect(config.db_path())) as conn:
            loop.watch_loop(conn)
    except Exception:   # the watch must never end the web service
        import logging
        logging.exception('Loop watch failed')


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
        from . import version_leases
        process = version_leases.launch(Path(__file__).resolve().parent.parent, lambda *a, **k: osplat.popen_outside_job(subprocess.Popen, *a, **k),
                                         argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, env=environment, **options)
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
