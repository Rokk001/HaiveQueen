"""Browser contract and executable client regressions, without browser packages."""
import shutil
import subprocess
import unittest
import http.server
import json
import os
import tempfile
import threading
from pathlib import Path

from bzztest import ROOT, RoundCase
from haivequeen import web_api, web_ui


class BrowserTests(unittest.TestCase):
    @staticmethod
    def browser_environment(folder):
        # Chromium also writes NSS, crash reports and MIME settings outside
        # --user-data-dir. Keep all of those in this test's own temporary home.
        environment = dict(os.environ)
        for key in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA',
                    'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
            environment[key] = folder
        return environment

    def test_page_bootstraps_csrf_and_shared_translations_safely(self):
        page = web_ui.page('session-token', language='German')
        self.assertIn('session-token', page)
        self.assertIn('Allgemein', page)
        self.assertIn('web-client.js', page)
        self.assertNotIn('</script><attack>', web_ui.page('</script><attack>'))

    def test_german_dashboard_labels(self):
        from haivequeen import i18n
        for label in ('Questions', 'Limits', 'Queue', 'Clear', 'Resume', 'Update', 'Live'):
            self.assertNotEqual(i18n.tr(label, language='German'), label, label)

    def test_client_behaviour(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required to execute browser regression tests')
        result = subprocess.run([node, str(Path(__file__).with_name('web_client_test.cjs'))],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tooltips_do_not_interrupt_scrolling(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        result = subprocess.run([node, str(Path(__file__).with_name('web_tooltip_scroll_test.cjs'))],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_confirmation_settles_without_rendering_close_event(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        result = subprocess.run([node, str(Path(__file__).with_name('web_confirmation_test.cjs'))],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_grouped_hives_and_missing_value_icons(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        for name in ('web_dashboard_fields_test.cjs', 'web_hives_groups_test.cjs', 'web_tree_queue_test.cjs', 'web_workflow_controls_test.cjs'):
            result = subprocess.run([node, str(Path(__file__).with_name(name))],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cicd_views_and_clipboard(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        result = subprocess.run([node, str(Path(__file__).with_name('web_cicd_experience_test.cjs'))],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mobile_layout_state_and_access_badges(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        result = subprocess.run([node, str(Path(__file__).with_name('web_mobile_state_test.cjs'))],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assets_are_packaged(self):
        text = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
        self.assertIn('web-client.js', text)
        self.assertIn('web-client.css', text)

    def test_dashboard_branding_is_packaged_and_dialog_is_named(self):
        import base64
        page = web_ui.page('token', language='English')
        emblem = ROOT / 'haivequeen' / 'web-login-logo.png'
        source = 'data:image/png;base64,' + base64.b64encode(emblem.read_bytes()).decode('ascii')
        self.assertTrue(f'class="header-queen" alt="" src="{source}"' in page)
        self.assertIn(emblem.name, (ROOT / 'pyproject.toml').read_text())
        self.assertIn('aria-labelledby="detail-title"', page)

    def test_header_groups_navigation_and_exposes_decorative_animation(self):
        from html.parser import HTMLParser
        class HeaderParser(HTMLParser):
            in_header = False
            nav_in_header = False
            decorative_canvas = False
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'header': self.in_header = True
                if tag == 'nav': self.nav_in_header = self.in_header
                if tag == 'canvas': self.decorative_canvas = attrs.get('aria-hidden') == 'true'
            def handle_endtag(self, tag):
                if tag == 'header': self.in_header = False
        parser = HeaderParser()
        page = web_ui.page('test', language='English')
        parser.feed(page)
        self.assertLess(page.index('id="version"'), page.index('class="brand-header"'))
        self.assertLess(page.index('id="update-status"'), page.index('class="brand-header"'))
        self.assertTrue(parser.nav_in_header)
        self.assertTrue(parser.decorative_canvas)

    def test_browser_labels_share_desktop_language_and_defaults(self):
        from haivequeen import settings, scheduler
        import re
        page=web_ui.page('token',language='German')
        data=json.loads(re.search(r'<script id="bootstrap" type="application/json">(.*?)</script>',page).group(1))
        for name in ('General','Providers','Projects','Stop gracefully','Force stop','Restart Queen on %s'):
            self.assertIn(name,data['translations'])
        self.assertEqual(data['general']['maxConcurrentCompileChecks'],scheduler.DEFAULT_SETTINGS['maxConcurrentCompileChecks'])
        for key in settings.SETTING_LABELS:
            self.assertIn(key,data['labels'])
        self.assertNotIn('password',data['general']['web'])
        self.assertEqual(data['fieldLabels']['General']['language'],'Interface language')
        self.assertEqual(data['fieldLabels']['Providers']['slots'],'Simultaneous agents')
        for group in ('labels', 'help'):
            for text in data[group].values():
                self.assertIn(text, data['translations'], (group, text))

    def test_real_browser_controls_settings_conflict_login_questions_updates_and_live(self):
        candidates = [shutil.which(name) for name in ('msedge', 'chromium', 'google-chrome')]
        for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
            if os.environ.get(variable):
                for executable in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
                    candidates.append(str(Path(os.environ[variable]) / executable))
        flags = ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                 '--disable-extensions', '--disable-background-networking']
        process_options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        browser = None
        for candidate in dict.fromkeys(candidates):
            if not candidate or not Path(candidate).is_file():
                continue
            # Some Windows browser launchers return before their child exits
            # and the child may still hold the throwaway probe profile open.
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
                try:
                    probe = subprocess.run([candidate, *flags, '--user-data-dir=' + profile,
                                            '--dump-dom', 'data:text/html,<p>headless probe</p>'],
                                           env=self.browser_environment(profile),
                                           capture_output=True, timeout=15, **process_options)
                except (OSError, subprocess.TimeoutExpired):
                    continue
            if probe.returncode == 0 and b'<p>headless probe</p>' in probe.stdout:
                browser = candidate
                break
        if not browser:
            self.skipTest('The DOM smoke test requires a Chromium browser with working headless DOM output')
        actions = []
        live_reads = []
        browser_language = ['English']
        git_method = ['']
        saved_projects = {}
        state = {'version':'test', 'orchestrator':{'label':'RUNNING'}, 'controls':{'force_warning':'Force stop ends all running Queens, Agents, Builds and Deploys immediately. Unfinished work will be done again after Start.'},
                 'projects':[{'name':'demo'}, {'name':'local-only'}], 'tiles':{'bees':1}, 'hives':[],
                 'bees':[{'name':'mock-agent','agent':'mock-agent','plan_path':'mock.md','project':'demo','apiary':'demo','task':'P1','status':'running','model':'running'}]}
        state['hives'] = [{'name':'Mock','apiary':'demo','plan_path':'mock.md','queen':'Test Queen','queen_agent':'queen-test','progress':50,'done':1,'total':2,'status':'Running'}]
        state['queue'] = {'open': 2, 'queued': 99, 'items': [
            {'project':'demo','plan_path':'mock.md','status':'QUEUED'},
            {'project':'demo','plan_path':'old.md','status':'in_run'},
            {'project':'demo','plan_path':'next.md','title':'Next plan','status':'QUEUED'},
            {'project':'demo','plan_path':'draft.md','title':'Draft plan','status':'DRAFT'},
            {'project':'demo','plan_path':'draft.md','title':'Draft plan','status':'DRAFT'}]}
        state['bees'][0].update(kind='bee', plan_path='mock.md')
        state['bees'].insert(0, {'kind':'queen','name':'Test Queen','apiary':'demo','project':'demo','plan_path':'mock.md','hive':'Mock','task':'Queen'})
        state['bees'].append({'kind':'orch','name':'HQ','apiary':'demo','project':'demo','plan_path':'mock.md','status':'running','orch':{'chain':[['Merge','done'],['Check','done'],['Build','running'],['Push','todo'],['Deploy','todo']]}})
        state['limits'] = [{'name': name, 'model': 'test-model', 'reserve': {'text': '+10 %', 'level': 'ok'},
                            'slots': {'total': 8, 'used': 2}, 'weekly': {'pct': 32, 'text': '7d 32 % · 18.11. 11:10'},
                            'five': '5h 12 % · 23:10'} for name in ('Claude', 'Codex', 'Local')]
        data = {'web':{'address':'127.0.0.1','port':8484,'certificate':'','key':''},
                'providers':[{'name':'mock','protocol':'claude','command':'mock','classes':{'medium':{'model':'test'}}}]}
        class Handler(http.server.BaseHTTPRequestHandler):
            def send(self, value, mime='application/json', status=200):
                body=(value if isinstance(value,str) else json.dumps(value)).encode()
                self.send_response(status)
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_GET(self):
                route=self.path.split('?')[0]
                if route=='/':
                    html=web_ui.page('test-csrf',language=browser_language[0])
                    return self.send(html.replace('</body>', '<script src="/test.js"></script></body>'),'text/html')
                if route=='/test.js':
                    return self.send(Path(__file__).with_name('web_browser_test.js').read_text(encoding='utf-8'),'text/javascript')
                if route in ('/web-client.js','/web-client.css'):
                    return self.send(web_ui.asset(route[1:]),'text/javascript' if route.endswith('.js') else 'text/css')
                if route=='/api/plan/detail':
                    return self.send({'text':'# Mock plan\n\n## Requirements\n\n- Keep existing actions\n\n<script>unsafe</script>', 'hive':{'packages':[]}})
                if route=='/api/live':
                    live_reads.append(True)
                    return self.send({'lines':[['green','live milestone '+str(len(live_reads))]]})
                if route=='/api/settings/view' and 'project=' in self.path:
                    project = 'local-only' if 'local-only' in self.path else 'demo'
                    return self.send({'data':saved_projects.get(project, {'name':project,'compileCheck':'check','wayOfWorking':'local', 'mergeMode':'local', 'checkMode':'local', 'buildMode':'local', 'deployMode':'local', 'releaseMode':'local', 'checkEnabled':True, 'buildEnabled':False, 'deployEnabled':False, 'releaseEnabled':False}),'revision':'project-revision',
                                      'git_workflow':{'provider':'example' if project == 'demo' else '', 'supported':{'merge':True,'check':True,'build':True,'deploy':True,'release':False}},
                                      'place':{'repository':'demo-repository','plansFolder':'demo-plans'},
                                      'workspace':'demo-plans','project_defaults':{'integrationBranch':'develop'},'rules':[{'label':'AGENTS.md','path':'AGENTS.md','tokens':12,'native_clis':['Codex CLI']}],
                                      'upstream':{'on':False,'usable':False,'note':''}})
                value={'/api/state':state, '/api/providers':[{'name':'mock','logged_in':True}],
                       '/api/git-login/providers':[{'name':'example','method':git_method[0],'device':False}],
                       '/api/releases':{'releases':[{'tag':'v2.0.1-dev.3','version':'v2.0.1-dev.3','date':'2026-01-02','commit':'a'*40,'prerelease':True,'full_tag':'v2.0.1'},
                                                    {'tag':'v2.0.0','version':'v2.0.0','date':'2026-01-01','commit':'b'*40,'prerelease':False,'full_tag':''}]},
                       '/api/settings/view':{'data':data,'revision':'revision-1','provider_models':{'mock':{'models':['test'],'efforts':['medium']}}},
                       '/api/updates':{'available':{'version':'v9.0.0'}},
                       '/api/plans':[{'name':'Mock','apiary':'demo','plan_path':'mock.md','status':'RUNNING','has_hive':True}],
                       '/api/plan/providers':[{'name':'mock','current':True,'selectable':False},
                                              {'name':'excluded','current':False,'selectable':False},
                                              {'name':'available','current':False,'selectable':True}],
                       '/api/questions':[{'id':1,'project':'demo','plan_path':'mock.md','text':'Proceed?','item':'question'},
                                         {'id':2,'project':'demo','plan_path':'mock.md','text':'Review?','item':'owner-check','packages':['P1']}],
                       '/api/live':{'lines':[['green','live milestone']]},
                       '/test-actions':actions}.get(route,{})
                self.send(value)
            def do_POST(self):
                action=self.path.removeprefix('/api/')
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                actions.append({'action':action,'data':payload})
                if self.headers.get('X-CSRF-Token')!='test-csrf':
                    return self.send({'error':'missing CSRF'},status=403)
                if action in ('questions/answer', 'owner/accept'):
                    state['queue']['open'] = max(0, state['queue']['open'] - 1)
                if action=='settings/save':
                    if sum(row['action']==action for row in actions)==1:
                        return self.send({'error':'Settings changed since loading; reload before saving'},status=400)
                    import time
                    time.sleep(0.15)
                    if not payload.get('project'):
                        browser_language[0]=payload['data'].get('language','English')
                    else:
                        saved_projects[payload['project']] = payload['data']
                    return self.send({'data':payload['data'],'revision':'revision-2'})
                if action=='git-login/token':
                    git_method[0] = 'token'
                    return self.send({'ok':True})
                if action=='releases/promote':
                    return self.send({'id':'release-job', 'running':False, 'result':{'tag':'v2.0.1'}, 'error':''})
                if action=='login/start':
                    return self.send({'id':'login-1','provider':'mock','running':True,'output':'ABCD-EFGH','links':['https://example.invalid/device']})
                if action=='updates/check':
                    return self.send({'available':{'version':'v9.0.0'}})
                self.send({'ok':True})
            def log_message(self, *_args):
                pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile:
                command=[browser,*flags,'--window-size=1500,1000','--user-data-dir='+profile,
                         '--dump-dom','--virtual-time-budget=60000','http://127.0.0.1:'+str(server.server_port)]
                result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,
                                      env=self.browser_environment(profile),
                                      **process_options)
                failure=result.stdout.split('BROWSER TEST:')[-1][-1500:]
            self.assertTrue('data-test-result="passed"' in result.stdout,
                            f'Browser exit code {result.returncode}\n{failure}\n{result.stderr[-1000:]}')
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


class SettingsViewTests(RoundCase):
    def test_project_rules_explain_native_cli_loading(self):
        from unittest.mock import patch
        rules = [{'label':'AGENTS.md','name':'AGENTS.md','upstream':False},
                 {'label':'upstream AGENTS.md','name':'AGENTS.md','upstream':True}]
        with patch('haivequeen.profile.rule_entries', return_value=rules):
            result = web_api.execute(self.conn, 'settings/view', {'project':'testproj'})
        self.assertIn('Codex', result['rules'][0]['native_clis'])
        self.assertEqual(result['rules'][1]['native_clis'], [])

    def test_project_form_keeps_the_repository_default_branch(self):
        from unittest.mock import patch
        with patch('haivequeen.projsettings.derive_remote',return_value=('origin',False)), \
             patch('haivequeen.projsettings.default_branch',return_value='develop'):
            result=web_api.execute(self.conn,'settings/view',{'project':'testproj'})
        self.assertEqual(result['project_defaults']['integrationBranch'],'develop')

    def test_automatic_update_check_obeys_the_existing_interval(self):
        from unittest.mock import patch
        with patch('haivequeen.updates.check',return_value=None) as check:
            web_api.execute(self.conn,'updates/check',{'automatic':True})
        self.assertFalse(check.call_args.kwargs['manual'])

    def test_project_place_refuses_moving_registered_settings(self):
        loaded=web_api.execute(self.conn, 'settings', {'project':'testproj'})
        with self.assertRaisesRegex(ValueError, 'settings of this Project are kept'):
            web_api.execute(self.conn, 'settings/save', {**loaded,'project':'testproj','place':{'repository':'other-repository','plansFolder':''}})

    def test_project_view_includes_knowledge_base_and_workspace(self):
        from unittest.mock import patch
        with patch('haivequeen.profile.rule_entries', return_value=[{'label':'AGENTS.md','path':'AGENTS.md','tokens':12,'on':True}]), \
             patch('haivequeen.profile.upstream_state', return_value={'usable':False,'on':False,'note':''}):
            result = web_api.execute(self.conn, 'settings/view', {'project':'testproj'})
        self.assertIn('revision', result)
        self.assertEqual(result['rules'][0]['tokens'], 12)
        self.assertTrue(result['workspace'])
        self.assertNotIn('password', result['data'].get('web', {}))

    def test_machine_view_has_saved_provider_models_and_benchmarks(self):
        result = web_api.execute(self.conn, 'settings/view', {})
        self.assertIn('provider_models', result)
        self.assertIn('benchmark', result)
        self.assertIn('data', result)
        self.assertEqual(result['build']['minFreeRamGB'], 2)
        self.assertGreater(result['cores'], 0)

    def test_profile_install_uses_existing_installer_as_a_job(self):
        from unittest.mock import patch
        with patch('haivequeen.web_jobs.start', return_value={'id':'install'}) as start:
            result = web_api.execute(self.conn, 'tools/install', {})
        self.assertEqual(result['id'], 'install')
        with patch('haivequeen.bzz_install.install', return_value={'mock':'installed'}) as install:
            self.assertEqual(start.call_args.args[1](lambda _text: None), {'mock':'installed'})
        install.assert_called_once_with()


class ProviderBrowserLoginTests(unittest.TestCase):
    def test_builtin_logins_stream_in_browser_and_accept_returned_code(self):
        import sys
        import time
        from unittest.mock import patch
        from haivequeen import web_login
        for protocol in ('codex','claude'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as folder:
                script=Path(folder)/'standin.py'
                script.write_text('import sys\nprint("https://example.invalid/device CODE-567 " + " ".join(sys.argv[1:]), flush=True)\nprint("received:"+input(),flush=True)\n',encoding='utf-8')
                provider={'name':'mock-'+protocol,'protocol':protocol,'command':'"%s" -u "%s"' % (sys.executable,script)}
                with patch('haivequeen.profile.prepare',return_value=True), patch('haivequeen.profile.environment',return_value=dict(os.environ)):
                    run=web_login.start(provider)
                token=run['id']
                try:
                    deadline=time.monotonic()+5
                    while 'CODE-567' not in web_login.status(token)['output'] and time.monotonic()<deadline:
                        time.sleep(.01)
                    self.assertIn('https://example.invalid/device',web_login.status(token)['links'])
                    if protocol=='codex':
                        self.assertIn('login --device-auth',web_login.status(token)['output'])
                    web_login.send(token,'returned-code')
                    while web_login.status(token)['running'] and time.monotonic()<deadline:
                        time.sleep(.01)
                    self.assertFalse(web_login.status(token)['running'])
                    self.assertIn('received:returned-code',web_login.status(token)['output'])
                finally:
                    web_login.close()
