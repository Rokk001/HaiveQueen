import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
import isolation  # noqa: F401 (keep runtime data isolated)
from haivequeen import servers


@contextmanager
def endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.path.startswith('/redirect'):
                self.send_response(getattr(self.server, 'redirect_code', 302))
                self.send_header('Location', self.server.destination)
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'ok')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, 'http://127.0.0.1:%s' % server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ApiRedirectTests(unittest.TestCase):
    def test_cross_origin_redirect_never_contacts_destination(self):
        with endpoint() as (target, target_url, received), endpoint() as (source, source_url, sent):
            source.destination = target_url + '/models'
            status, _ = servers._request(source_url + '/redirect', 'test-secret')
            self.assertEqual(received, [], 'Redirect destination received credentials')
            self.assertEqual(status, 302)
            self.assertEqual(sent[0][1]['Authorization'], 'Bearer test-secret')

    def test_plain_http_and_same_origin_redirect_remain_supported(self):
        with endpoint() as (source, url, received):
            source.destination = '/models'
            self.assertEqual(servers._request(url + '/models', 'test-secret'), (200, 'ok'))
            self.assertEqual(servers._request(url + '/redirect', 'test-secret'), (200, 'ok'))
            self.assertEqual(received[-1][1]['Authorization'], 'Bearer test-secret')
            self.assertEqual(received[-1][1]['X-Api-Key'], 'test-secret')

    def test_redirect_policy_blocks_host_port_scheme_and_userinfo_changes(self):
        handler = servers._SameOriginRedirect()
        req = urllib.request.Request('https://example.test/models', headers={'Authorization': 'Bearer test-secret'})
        for destination in ('https://other.test/models', 'https://example.test:444/models',
                            'http://example.test/models', 'https://user@example.test/models',
                            'https://example.test:invalid/models'):
            with self.subTest(destination=destination):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    handler.redirect_request(req, None, 302, 'Found', {}, destination)
                caught.exception.close()
        redirected = handler.redirect_request(req, None, 302, 'Found', {}, 'https://EXAMPLE.test:443/new')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer test-secret')

    def test_all_supported_redirect_codes_block_external_origin(self):
        with endpoint() as (target, target_url, received), endpoint() as (source, source_url, sent):
            source.destination = target_url + '/models'
            for code in (301, 302, 303, 307, 308):
                with self.subTest(code=code):
                    source.redirect_code = code
                    self.assertEqual(servers._request(source_url + '/redirect', 'test-secret')[0], code)
                    self.assertEqual(received, [])
