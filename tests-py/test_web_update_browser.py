"""Exercise update controls in a real browser against an isolated local API."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from haivequeen import web_ui


class UpdateBrowserTest(unittest.TestCase):
    def test_wait_tooltip_force_confirmation_and_version_display(self):
        candidates = [shutil.which(name) for name in ('msedge', 'chromium', 'google-chrome')]
        for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
            if os.environ.get(variable):
                candidates.append(str(Path(os.environ[variable]) / 'Microsoft/Edge/Application/msedge.exe'))
        browser = next((p for p in candidates if p and Path(p).is_file()), None)
        if not browser:
            self.skipTest('Chromium is required')
        actions = []
        waiting = {'reason': 'schema change', 'jobs': [
            {'kind': 'Queen', 'name': 'Q', 'project': 'P'}, {'kind': 'Build', 'name': 'B', 'project': 'P'}]}
        update = {'available': {'version': 'v2'}}
        state = {'version': 'v2', 'orchestrator': {'label': 'RUNNING'}, 'projects': [{'name': 'P'}],
                 'tiles': {}, 'limits': [], 'queue': {}, 'hives': [{'name': 'Plan', 'apiary': 'P',
                 'plan_path': 'plan.md', 'queen': 'Q', 'queen_agent': 'q', 'queen_version': 'v1',
                 'status': 'Running', 'progress': 0, 'done': 0, 'total': 1}],
                 'bees': [{'kind': 'orchestrator', 'name': 'Build', 'apiary': 'P', 'project': 'P',
                 'plan_path': 'plan.md', 'hive': 'Plan', 'task': 'Build', 'status': 'running', 'version': 'v1'}]}
        script = Path(__file__).with_name('web_update_browser_test.js').read_text(encoding='utf-8')
        class Handler(http.server.BaseHTTPRequestHandler):
            def send(self, value, mime='application/json'):
                raw = (value if isinstance(value, str) else json.dumps(value)).encode()
                self.send_response(200); self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                route = self.path.split('?')[0]
                if route == '/':
                    return self.send(web_ui.page('csrf', language='English').replace('</body>', '<script src="/test.js"></script></body>'), 'text/html')
                if route == '/test.js': return self.send(script, 'text/javascript')
                if route in ('/web-client.js', '/web-client.css'):
                    return self.send(web_ui.asset(route[1:]), 'text/javascript' if route.endswith('.js') else 'text/css')
                return self.send({'/api/state': state, '/api/updates': update, '/test-actions': actions}.get(route, []))
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                actions.append({'action': self.path, 'data': payload})
                if self.path == '/api/updates/install' and payload.get('confirmed'):
                    update.update(installing=True, waiting=waiting)
                self.send({'ok': True})
            def log_message(self, *_): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile:
                cmd = [browser, '--headless', '--disable-gpu', '--no-first-run', '--disable-extensions',
                       '--disable-background-networking', '--user-data-dir='+profile, '--dump-dom',
                       '--virtual-time-budget=10000', 'http://127.0.0.1:'+str(server.server_port)]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
                                        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
                self.assertIn('data-test-result="passed"', result.stdout, result.stdout.split('BROWSER TEST:')[-1][-1500:])
        finally:
            server.shutdown(); thread.join(); server.server_close()
