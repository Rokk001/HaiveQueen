"""HTTP listener primitives for the independent web service."""
import http.server
import hmac
import json
import secrets
import socket
import ssl
import threading
import time
from datetime import date, datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from . import config, filetxn, web_auth


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError('Unsupported web response value: ' + type(value).__name__)


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, *args, **kwargs):
        self.tls_context = None
        self.connections = set()
        self.connection_lock = threading.Lock()
        self.config_path = config.config_dir() / 'config.toml'
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.setup_token = secrets.token_urlsafe(32)
        self.api = self.engine_api
        self.lifecycle = lambda action: None
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        try:
            connection.settimeout(15)
            if self.tls_context:
                # An incomplete handshake must occupy only this client's thread.
                connection = self.tls_context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
            with self.connection_lock:
                self.connections.add(connection)
            return connection, address
        except Exception:
            connection.close()
            raise

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.connection_lock:
                self.connections.discard(request)

    def server_close(self):
        with self.connection_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        super().server_close()

    @staticmethod
    def engine_api(action, data):
        from . import web_api
        return web_api.dispatch(action, data)


class _Handler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def reply(self, status, value, cookie=None):
        html = isinstance(value, str)
        body = value.encode('utf-8') if html else json.dumps(value, ensure_ascii=False, default=json_value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def settings(self):
        return web_auth.read(self.server.config_path)

    def session(self, settings):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['hq_session'].value
        except (KeyError, ValueError):
            return None
        with self.server.session_lock:
            entry = self.server.sessions.get(token)
            if entry and entry['expires'] > time.monotonic() and entry['password'] == settings['password']:
                return entry
        return None

    def same_origin(self):
        origin = self.headers.get('Origin')
        scheme = 'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'
        return not origin or origin == scheme + '://' + self.headers.get('Host', '')

    def local_host(self):
        try:
            return urlsplit('http://' + self.headers.get('Host', '')).hostname in ('127.0.0.1', 'localhost')
        except ValueError:
            return False

    def page(self, setup=False):
        warning = '' if isinstance(self.connection, ssl.SSLSocket) else '<p>Warning: HTTP sends the password unencrypted over the network.</p>'
        fields = '<input name="username" placeholder="Username" required autocomplete="username"><input name="password" type="password" placeholder="Password" required autocomplete="current-password">'
        if setup:
            fields += '<input name="address" value="0.0.0.0" required><input name="port" type="number" value="8484" required><input name="certificate" placeholder="Certificate file (optional)"><input name="key" placeholder="Key file (optional)">'
        title = 'Setup' if setup else 'Login'
        token = json.dumps(self.server.setup_token if setup else '')
        return ('<!doctype html><meta charset="utf-8"><title>HaiveQueen ' + title + '</title><h1>' + title + '</h1>' + warning +
                '<form>' + fields + '<button>' + title + '</button></form><p id="result"></p><script>' +
                'document.querySelector("form").onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));if(d.port)d.port=Number(d.port);' +
                'let r=await fetch(location.pathname==="/setup"||' + str(setup).lower() + '?"/setup":"/login",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":' + token + '},body:JSON.stringify(d)});let v=await r.json();' +
                'if(r.ok){if(v.csrf)sessionStorage.setItem("hq_csrf",v.csrf);if(v.url){let a=document.createElement("a");a.href=v.url;a.textContent="Setup saved. Open login";document.getElementById("result").replaceChildren(a);setTimeout(()=>location.href=v.url,750)}else location.href="/"}else document.getElementById("result").textContent=v.error;};</script>')

    def do_GET(self):
        settings = self.settings()
        path = urlsplit(self.path).path
        configured = bool(settings['username'] and settings['password'])
        if not configured and not self.local_host():
            return self.reply(403, {'error': 'Use localhost for first setup'})
        if not configured and path in ('/', '/setup', '/login'):
            return self.reply(200, self.page(setup=True))
        if configured and path == '/login':
            return self.reply(200, self.page())
        if not configured or not self.session(settings):
            return self.reply(401, self.page() if path == '/' else {'error': 'Login required'})
        if path == '/':
            # The dashboard package supplies the full interface at this boundary.
            return self.reply(200, '<!doctype html><meta charset="utf-8"><title>HaiveQueen</title><h1>HaiveQueen</h1><div id="app"></div>')
        if path not in ('/api/state', '/api/plans', '/api/plan/providers', '/api/questions', '/api/settings', '/api/providers', '/api/live', '/api/login/status', '/api/updates', '/api/benchmark', '/api/jobs/status'):
            return self.reply(404, {'error': 'Unknown route'})
        from urllib.parse import parse_qs
        data = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        self.call_api(path[5:], data)

    def call_api(self, action, data):
        try:
            self.reply(200, self.server.api(action, data))
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})
        except Exception as exc:
            self.reply(500, {'error': str(exc)})

    def do_POST(self):
        settings = self.settings()
        path = urlsplit(self.path).path
        configured = bool(settings['username'] and settings['password'])
        entry = self.session(settings)
        if not self.same_origin():
            return self.reply(403, {'error': 'Origin refused'})
        if path == '/setup':
            if configured or not self.local_host() or self.client_address[0] != '127.0.0.1' or not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.setup_token):
                return self.reply(403, {'error': 'Setup unavailable; use bzz web setup'})
        elif path != '/login':
            if not configured or not entry:
                return self.reply(401, {'error': 'Login required'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), entry['csrf']):
                return self.reply(403, {'error': 'CSRF token required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024 * 1024:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
        except (ValueError, UnicodeError) as exc:
            return self.reply(400, {'error': str(exc)})
        if path == '/setup':
            try:
                with filetxn.locked():
                    if self.settings()['password']:
                        raise ValueError('Setup already completed')
                    candidate = {**settings, **data, 'network': True}
                    validate_tls(candidate)
                    web_auth.setup(self.server.config_path, data['username'], data['password'], data.get('address', '0.0.0.0'), data.get('port', 8484), data.get('certificate', ''), data.get('key', ''))
            except (KeyError, ValueError, OSError) as exc:
                return self.reply(400, {'error': str(exc)})
            host = data.get('address', '0.0.0.0')
            host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
            if ':' in host:
                host = '[' + host + ']'
            url = '%s://%s:%s/login' % ('https' if data.get('certificate') else 'http', host, data.get('port', 8484))
            self.reply(200, {'ok': True, 'url': url})
            self.server.lifecycle('reload')
            return
        if path == '/login':
            if not configured or not web_auth.verify(settings, data.get('username', ''), data.get('password', '')):
                return self.reply(401, {'error': 'Invalid username or password'})
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.server.session_lock:
                now = time.monotonic()
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
                if len(self.server.sessions) >= 128:
                    self.server.sessions.pop(next(iter(self.server.sessions)))
                self.server.sessions[token] = {'csrf': csrf, 'expires': now + 12 * 3600, 'password': settings['password']}
            cookie = 'hq_session=' + token + '; HttpOnly; SameSite=Strict; Path=/'
            if isinstance(self.connection, ssl.SSLSocket):
                cookie += '; Secure'
            return self.reply(200, {'ok': True, 'csrf': csrf}, cookie)
        if path == '/logout':
            with self.server.session_lock:
                self.server.sessions = {k: v for k, v in self.server.sessions.items() if v is not entry}
            return self.reply(200, {'ok': True}, 'hq_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/')
        if path in ('/api/web/stop', '/api/web/restart'):
            self.reply(200, {'ok': True})
            self.server.lifecycle('stop' if path.endswith('/stop') else 'reload')
            return
        if path.startswith('/api/'):
            return self.call_api(path[5:], data)
        self.reply(404, {'error': 'Unknown route'})

    def log_message(self, format, *args):
        pass


def validate_tls(settings):
    """Validate user supplied certificate files without binding a socket."""
    certificate, key = settings.get('certificate', ''), settings.get('key', '')
    if bool(certificate) != bool(key):
        raise ValueError('Both certificate and key are required for HTTPS')
    context = None
    if certificate:
        for filename in (certificate, key):
            if not Path(filename).is_file():
                raise OSError('Certificate or key file not found: ' + filename)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certificate, key)
        except OSError as exc:
            raise OSError('Cannot load certificate %s or key %s: %s' % (certificate, key, exc)) from exc
    return context


def make_server(settings, handler=_Handler):
    """Bind a listener, validating TLS files before opening the socket."""
    context = validate_tls(settings)
    server = _Server((web_auth.bind_address(settings), settings['port']), handler)
    server.tls_context = context
    return server
