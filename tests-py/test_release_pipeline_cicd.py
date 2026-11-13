"""Release requirements through real persisted Orchestrator rounds and adapters."""
import hashlib
import io
import json
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from bzztest import REL
from test_o1_orchestrator import OrchCase
from haivequeen import db, git_provider, orchestrator, publish, runtime
from haivequeen.git_providers import github


class ReleasePipeline(OrchCase):
    def setUp(self):
        super().setUp()
        self.assets = {}
        self.refs = {}
        self.releases = {}
        self.uploads = []
        self.fail_upload = None
        self.workflow = 'pending'
        self.service = git_provider.Provider('github', github, 'example/repo')
        for patch in (mock.patch.object(git_provider, 'for_project', return_value=(self.service, [])),
                      mock.patch.object(github, 'urlopen', side_effect=self.http),
                      mock.patch.object(github, 'login', return_value='credential')):
            patch.start()
            self.addCleanup(patch.stop)
        # Selection reads the actual bare code remote, never the project's version.
        self.git('tag', 'v1.4.0')
        self.git('push', '-q', 'origin', 'refs/tags/v1.4.0')

    def http(self, req, timeout=None):
        url = urlsplit(req.full_url)
        path = url.path.split('/repos/example/repo', 1)[-1]
        data = json.loads(req.data) if req.data and url.hostname != 'uploads.github.com' else None
        method = req.get_method()
        if path.startswith('/git/ref/tags/'):
            tag = path.split('/tags/', 1)[1]
            if tag not in self.refs:
                raise HTTPError(req.full_url, 404, 'missing', {}, None)
            result = {'object': {'type': 'commit', 'sha': self.refs[tag]}}
        elif path == '/git/refs':
            self.refs[data['ref'].removeprefix('refs/tags/')] = data['sha']
            self.git('update-ref', data['ref'], data['sha'], cwd=self.remote)
            result = {'object': {'type': 'commit', 'sha': data['sha']}}
        elif path.startswith('/releases/tags/'):
            tag = path.split('/tags/', 1)[1]
            if tag not in self.releases:
                raise HTTPError(req.full_url, 404, 'missing', {}, None)
            result = self.releases[tag]
        elif path == '/releases' and method == 'POST':
            self.assertEqual(self.run_order(), ['Build', 'Deploy'])
            result = dict(data, id=len(self.releases) + 1)
            self.releases[data['tag_name']] = result
        elif path.endswith('/assets') and method == 'GET':
            result = list(self.assets.values())
        elif path.endswith('/assets') and method == 'POST':
            name = parse_qs(url.query)['name'][0]
            self.uploads.append((name, req.data))
            result = {'id': len(self.assets) + 1, 'name': name, 'state': 'uploaded',
                      'digest': 'sha256:' + hashlib.sha256(req.data).hexdigest()}
            self.assets[name] = result
            if name == self.fail_upload:
                self.fail_upload = None
                raise TimeoutError('response lost after successful upload')
        elif path == '/actions/runs':
            tag, commit = next(reversed(self.refs.items()))
            result = {'workflow_runs': [{'id': 5, 'workflow_id': 1, 'head_sha': commit,
                       'head_branch': tag, 'event': 'push',
                       'status': 'in_progress' if self.workflow == 'pending' else 'completed',
                       'conclusion': self.workflow}]}
        else:
            raise AssertionError((method, req.full_url))
        return io.BytesIO(json.dumps(result).encode())

    def configure(self, mode='local', enabled=True, postfix='preview', next_version=''):
        self.build, self.deploy = self.echo('Build'), self.echo('Deploy')
        self.project_toml('wayOfWorking = "custom"\nbuildEnabled = true\ndeployEnabled = true\n' +
                          'releaseEnabled = %s\nreleaseMode = "%s"\nreleaseFolder = "out"\n' %
                          (str(enabled).lower(), mode) + 'releasePostfix = "%s"\nnextVersion = "%s"\n' %
                          (postfix, next_version))
        out = self.pwt / 'out'
        out.mkdir(exist_ok=True)
        return out

    def test_local_partial_upload_restart_keeps_tag_and_bytes_then_closes(self):
        out = self.configure()
        self.ready_plan()
        (out / 'first package.zip').write_bytes(b'first bytes')
        (out / 'second.bin').write_bytes(b'unchanged\x00bytes')
        self.fail_upload = 'second.bin'
        self.round()
        state = self.state()
        self.assertEqual(state['step'], 'Release')
        self.assertEqual(state['release']['tag'], 'v1.4.1-preview.1')
        self.assertEqual(state['steps']['Build'], 'done')
        self.assertEqual(state['steps']['Deploy'], 'done')
        self.assertEqual(len(list(out.iterdir())), 2)
        self.assertEqual(self.fixers(), [])
        # Polling after reload must recover the uploaded-but-unacknowledged file.
        self.round()
        self.assertIsNone(self.state())
        self.assertEqual(self.plan_status(), 'REVIEW')
        self.assertEqual(self.uploads, [('first package.zip', b'first bytes'), ('second.bin', b'unchanged\x00bytes')])
        self.assertEqual(list(out.iterdir()), [])
        release = self.releases['v1.4.1-preview.1']
        self.assertTrue(release['prerelease'])
        self.assertEqual(release['target_commitish'], state['release']['commit'])

    def test_empty_and_unset_folder_skip_without_tag(self):
        self.configure()
        self.ready_plan()
        self.round()
        self.assertIsNone(self.state())
        self.assertEqual(self.refs, {})
        self.assertEqual(self.releases, {})

    def test_off_ignores_legacy_switch_and_never_publishes(self):
        out = self.configure(enabled=False)
        with (self.repo / '.haivequeen/project.toml').open('a') as stream:
            stream.write('publishReleases = true\n')
        self.ready_plan()
        (out / 'keep.zip').write_bytes(b'keep')
        self.round()
        self.assertIsNone(self.state())
        with mock.patch.object(publish, 'discover', side_effect=AssertionError('legacy discovery')):
            self.assertEqual(publish.retry_pending(self.conn, [self.env()]), [])
            self.assertIsNone(publish.after_integration(self.conn, self.env(), REL))
        self.assertEqual(self.releases, {})
        self.assertTrue((out / 'keep.zip').exists())

    def test_git_release_tag_only_waits_after_restart_until_workflow_green(self):
        self.configure(mode='git', postfix='nightly', next_version='2.0.0')
        self.ready_plan()
        self.round()
        first = self.state()
        self.assertEqual(first['step'], 'Release')
        self.assertTrue(first['release']['tagged'])
        self.assertEqual(first['release']['tag'], 'v2.0.0-nightly.1')
        self.round()
        self.assertEqual(self.state()['release'], first['release'])
        self.assertEqual(self.fixers(), [])
        self.workflow = 'success'
        self.round()
        self.assertIsNone(self.state())
        self.assertEqual(self.releases, {})
        self.assertEqual(self.uploads, [])

    def test_failed_release_workflow_enters_fix_flow(self):
        self.configure(mode='git')
        self.ready_plan()
        self.workflow = 'failure'
        self.round()
        self.assertEqual(self.state()['fix']['step'], 'Release')
        self.assertEqual(self.state()['steps']['Release'], 'red')
        self.assertNotEqual(self.plan_status(), 'REVIEW')

    def test_close_rechecks_enabled_release_after_settings_change(self):
        self.configure(enabled=False)
        self.ready_plan()
        with mock.patch.object(orchestrator, '_close', return_value=False):
            self.round()
        self.assertEqual(self.state()['step'], 'Close')
        self.configure(mode='git')
        self.round()
        self.assertEqual(self.state()['step'], 'Release')
        self.round()
        self.assertTrue(self.state()['release']['tagged'])

    def test_changed_local_file_is_kept_and_blocks_cleanup(self):
        out = self.configure()
        self.ready_plan()
        path = out / 'artifact.zip'
        path.write_bytes(b'original')
        self.fail_upload = path.name
        self.round()
        path.write_bytes(b'new build')
        self.round()
        self.assertEqual(self.state()['step'], 'Release')
        self.assertEqual(path.read_bytes(), b'new build')
        self.assertIn('file changed', runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + REL))

    def test_start_version_is_asked_once_and_project_version_is_never_used(self):
        self.configure(mode='git')
        self.git('push', '-q', 'origin', ':refs/tags/v1.4.0')
        self.ready_plan()
        with mock.patch.object(publish, 'base_version', side_effect=AssertionError('project version read')):
            self.round()
            self.round()
            questions = runtime.list_questions(self.conn, 'testproj', item='release-start:example/repo')
            self.assertEqual(len(questions), 1)
            self.assertEqual(self.refs, {})
            db.answer_question(self.conn, questions[0]['id'], '5.0.0', 'owner')
            self.round()
        self.assertEqual(self.state()['release']['tag'], 'v5.0.0-preview.1')

    def test_integration_tags_count_across_postfix_changes_before_next_full_release(self):
        self.configure(mode='git', postfix='dev')
        commit = self.ready_plan()
        self.git('push', '-q', 'origin', 'HEAD:main', cwd=self.pwt)
        self.workflow = 'success'
        tags = []
        env = self.env()
        for index, postfix in enumerate(('dev', 'release-candidate', 'dev')):
            self.configure(mode='git', postfix=postfix)
            env = self.env()
            state = {'merged': commit}
            publish.pipeline_release(self.conn, env, 'integration-%s' % index, state, self.pwt, env.settings)
            tags.append(state['release']['tag'])
        self.assertEqual(tags, ['v1.4.1-dev.1', 'v1.4.1-release-candidate.2', 'v1.4.1-dev.3'])
        self.git('update-ref', 'refs/tags/v1.4.1', commit, cwd=self.remote)
        state = {'merged': commit}
        publish.pipeline_release(self.conn, env, 'after-full-release', state, self.pwt, env.settings)
        self.assertEqual(state['release']['tag'], 'v1.4.2-dev.1')

    def local_adapter_rounds(self, module, repository):
        """Run real local adapter operations, losing the first upload response."""
        out = self.configure()
        self.ready_plan()
        content = b'package bytes\x00unchanged'
        (out / 'artifact.zip').write_bytes(content)
        provider = git_provider.Provider(module.__name__.split('.')[-1], module, repository)
        remote = {'tag': None, 'release': None, 'assets': [], 'package': None, 'uploads': 0}
        tag = 'v1.4.1-preview.1'
        commit = {'value': None}

        def request(*args, **kwargs):
            if provider.name == 'gitlab':
                repo, method, path, *_ = args
                payload = kwargs.get('data') if 'data' in kwargs else args[4] if len(args) > 4 else None
            else:
                repo, path, *_ = args
                method = args[3] if len(args) > 3 else 'GET'
                payload = args[4] if len(args) > 4 else None
                if payload and kwargs.get('raw') is not True and 'Content-Disposition' not in str(payload):
                    payload = json.loads(payload)
            if path.startswith('/repository/tags/') or path.startswith('/tags/'):
                if not remote['tag']:
                    raise HTTPError('', 404, 'missing', {}, None)
                value = {'commit': {'id': commit['value'], 'sha': commit['value']}}
            elif path in ('/repository/tags', '/tags'):
                remote['tag'] = tag
                commit['value'] = payload.get('ref') or payload.get('target')
                value = {}
            elif method == 'GET' and path in ('/releases/tags/' + tag, '/releases/' + tag):
                if not remote['release']:
                    raise HTTPError('', 404, 'missing', {}, None)
                value = remote['release']
            elif path == '/releases' and method == 'POST':
                self.assertEqual(self.run_order(), ['Build', 'Deploy'])
                remote['release'] = dict(payload, id=1, commit={'id': commit['value']})
                value = remote['release']
            elif method == 'GET' and ('/assets?' in path or path.endswith('/assets/links')):
                value = list(remote['assets'])
            elif '/packages/generic/' in path:
                if method == 'PUT':
                    remote['package'] = payload
                    remote['uploads'] += 1
                    raise TimeoutError('package stored; response lost')
                if remote['package'] is None:
                    raise HTTPError('', 404, 'missing', {}, None)
                value = remote['package']
            elif method == 'POST' and path.endswith('/assets/links'):
                value = dict(payload, id=1)
                remote['assets'].append(value)
            elif method == 'POST' and '/assets?' in path:
                self.assertIn(content, payload)
                value = {'id': 1, 'name': 'artifact.zip', 'browser_download_url': repository[0] + '/download/1'}
                remote['assets'].append(value)
                remote['uploads'] += 1
                raise TimeoutError('attachment stored; response lost')
            else:
                raise AssertionError((method, path, payload))
            return (value, '') if provider.name == 'gitlab' else value

        with mock.patch.object(git_provider, 'for_project', return_value=(provider, [])), \
             mock.patch.object(git_provider.Provider, 'credential', return_value='credential'), \
             mock.patch.object(module, '_request', side_effect=request), \
             mock.patch.object(module, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(content)):
            self.round()
            self.assertEqual(self.state()['step'], 'Release')
            self.assertEqual((out / 'artifact.zip').read_bytes(), content)
            self.round()
        self.assertIsNone(self.state())
        self.assertEqual(remote['uploads'], 1)
        self.assertEqual(list(out.iterdir()), [])
        self.assertEqual(remote['release']['tag_name'], tag)
        if provider.name == 'gitea':
            self.assertTrue(remote['release']['prerelease'])
        else:
            self.assertIn('Pre-release', remote['release']['name'])

    def test_gitea_local_upload_recovers_uncertain_attachment(self):
        from haivequeen.git_providers import gitea
        self.local_adapter_rounds(gitea, ('https://gitea.example', 'example', 'repo'))

    def test_gitlab_local_upload_recovers_uncertain_package(self):
        from haivequeen.git_providers import gitlab
        self.local_adapter_rounds(gitlab, 'example/repo')
