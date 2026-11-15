"""Capture the real dashboard with fictional, in-memory example data.

From the repository root, run with an existing Python 3.11+ environment:
    python scripts/capture_dashboard.py
Requires an already installed Chromium browser (Edge, Chrome or Chromium).
Optionally pass --browser PATH and --output PATH. No packages are installed.
The server binds only to loopback, serves the checked-in UI unchanged, rejects
writes, and never starts HaiveQueen or opens its database. Configuration/data
and the browser profile are isolated in a temporary directory before imports.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def example_state():
    plans = [
        {'name': 'Accessible checkout', 'apiary': 'Storefront', 'plan_path': 'checkout.md',
         'progress': 60, 'done': 3, 'total': 5, 'status': 'Running', 'runtime': '18m'},
        {'name': 'Search performance', 'apiary': 'Knowledge Base', 'plan_path': 'search.md',
         'progress': 75, 'done': 3, 'total': 4, 'status': 'Running', 'runtime': '26m'},
    ]
    agents = []
    for project, plan, name, kind, model, task, context, runtime in [
        ('Storefront', 'checkout.md', 'Checkout', 'queen', 'Claude Sonnet', 'Coordinate packages', 24, '18m'),
        ('Storefront', 'checkout.md', 'Forms', 'bee', 'GPT-5.1 Codex', 'P4 · Keyboard navigation', 16, '6m'),
        ('Storefront', 'checkout.md', 'Tests', 'bee', 'Claude Sonnet', 'P5 · Checkout tests', 12, '4m'),
        ('Knowledge Base', 'search.md', 'Search', 'queen', 'GPT-5.1 Codex', 'Review package results', 21, '26m'),
        ('Knowledge Base', 'search.md', 'Index', 'bee', 'Local coder', 'P4 · Index regression tests', 9, '3m'),
    ]:
        agents.append({'name': name, 'agent': name.lower(), 'kind': kind, 'apiary': project,
                       'project': project, 'plan_path': plan, 'model': model, 'task': task,
                       'ctx': context * 1000, 'ctx_limit': 128000, 'tin': context * 5200,
                       'tout': context * 800, 'status': 'running', 'runtime': runtime})
    return {
        'version': 'Example workspace', 'orchestrator': {'label': 'RUNNING'},
        'projects': [{'name': 'Storefront'}, {'name': 'Knowledge Base'}],
        'tiles': {'apiaries': 2, 'hives': 2, 'queens': 2, 'orchestrators': 1,
                  'bees': 3, 'bee_capacity': 8, 'tokens_in': 842000, 'tokens_out': 126000,
                  'start_ctx': {'saving': 38}},
        'hives': plans, 'bees': agents,
        'limits': [
            {'name': 'Claude', 'model': 'Claude Sonnet', 'reserve': {'text': '+42 %', 'level': 'ok'},
             'slots': {'used': 2, 'total': 4}, 'weekly': {'pct': 28, 'text': '7d 28 %'}, 'five': '5h 18 %'},
            {'name': 'Codex', 'model': 'GPT-5.1 Codex', 'next': True,
             'reserve': {'text': '+56 %', 'level': 'ok'}, 'slots': {'used': 2, 'total': 3},
             'weekly': {'pct': 19, 'text': '7d 19 %'}, 'five': '5h 12 %'},
            {'name': 'Local', 'model': 'Local coder', 'reserve': {'text': 'No quota', 'level': 'ok'},
             'slots': {'used': 1, 'total': 1}},
        ],
        'system': {'cpu': 34, 'ram_pct': 48, 'disk_pct': 22},
        'queue': {'open': 0, 'items': [
            {'project': 'Storefront', 'plan_path': 'receipts.md', 'title': 'Email receipts', 'status': 'QUEUED'},
            {'project': 'Knowledge Base', 'plan_path': 'feedback.md', 'title': 'Article feedback', 'status': 'DRAFT'},
        ]},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'assets' / 'web-dashboard.png')
    args = parser.parse_args()
    candidates = [args.browser] if args.browser else [
        Path(found) for name in ('msedge', 'chromium', 'google-chrome')
        if (found := shutil.which(name))
    ]
    if not args.browser:
        for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
            if os.environ.get(variable):
                candidates.extend(Path(os.environ[variable]) / executable for executable in (
                    'Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'))
    browser = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not browser:
        parser.error('No installed Chromium browser found; use --browser.')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hq-dashboard-', ignore_cleanup_errors=True) as folder:
        for key in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'XDG_DATA_HOME',
                    'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'BZZ_CONFIG_DIR', 'BZZ_DATA_DIR'):
            os.environ[key] = folder
        # Fail closed if a future UI import attempts to open any database.
        def guard_database(event, _args):
            if event == 'sqlite3.connect':
                raise RuntimeError('Screenshot generation must never open a database')
        sys.addaudithook(guard_database)
        sys.path.insert(0, str(ROOT))
        from haivequeen import web_ui
        state = example_state()
        routes = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                route = self.path.split('?')[0]
                routes.append(route)
                if route == '/':
                    body, mime = web_ui.page('example-only', language='English'), 'text/html'
                elif route in ('/web-client.js', '/web-client.css'):
                    body = web_ui.asset(route[1:])
                    mime = 'text/javascript' if route.endswith('.js') else 'text/css'
                elif route == '/api/state':
                    body, mime = json.dumps(state), 'application/json'
                elif route in ('/api/updates', '/api/updates/check'):
                    body, mime = '{"last_check": "example"}', 'application/json'
                elif route == '/api/jobs/status':
                    body, mime = '[]', 'application/json'
                else:
                    self.send_error(404)
                    return
                encoded = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                # The dashboard polls this read-only check using POST.
                if self.path == '/api/updates/check':
                    self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    return self.do_GET()
                self.send_error(405)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            command = [str(browser), '--headless', '--disable-gpu', '--no-first-run',
                       '--no-default-browser-check', '--disable-extensions', '--disable-background-networking',
                       '--force-device-scale-factor=1', '--window-size=1680,1120',
                       '--user-data-dir=' + folder, '--virtual-time-budget=10000',
                       '--screenshot=' + str(output), '--dump-dom',
                       'http://127.0.0.1:' + str(server.server_port)]
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(command, capture_output=True, timeout=45, **options)
            dom = result.stdout.decode('utf-8', errors='replace')
            assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')[-1000:]
            for text in ('Accessible checkout', 'Keyboard navigation', 'Search performance', 'Email receipts'):
                assert text in dom, 'Missing rendered fixture: ' + text
            assert 'is not valid JSON' not in dom, 'An API fixture route is missing'
            assert all(route in routes for route in ('/web-client.js', '/web-client.css', '/api/state'))
            assert output.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), 'Missing PNG screenshot'
            print('Captured real dashboard with fictional data:', output)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    main()
