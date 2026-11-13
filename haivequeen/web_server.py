"""HTTP listener primitives for the independent web service."""
import http.server
import ssl
from pathlib import Path

from . import web_auth


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Routes are deliberately unavailable until the authenticated API is installed.
        self.send_error(503, 'Web interface is not available')

    def log_message(self, format, *args):
        pass


def make_server(settings, handler=_Handler):
    """Bind a listener, validating TLS files before opening the socket."""
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
    server = _Server((web_auth.bind_address(settings), settings['port']), handler)
    try:
        if context:
            server.socket = context.wrap_socket(server.socket, server_side=True)
    except Exception:
        server.server_close()
        raise
    return server
