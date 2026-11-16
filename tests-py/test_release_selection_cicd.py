from types import SimpleNamespace
from bzztest import RoundCase
from haivequeen import db, publish, runtime


class TagSelection(RoundCase):
    def test_remote_tags_and_start_question_are_used_once(self):
        env = SimpleNamespace(name='testproj', workspace=self.repo, settings={'releasePostfix': 'preview'})
        entry = {'plan': 'plans/example.md'}
        def run(args, cwd):
            self.assertIn('--refs', args)
            return 0, ''
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'waiting for the start version'):
                publish._release_tag(self.conn, env, entry, 'example/repo', run)
        questions = runtime.list_questions(self.conn, env.name, item='release-start:example/repo')
        self.assertEqual(len(questions), 1)
        db.answer_question(self.conn, questions[0]['id'], '1.2.3', 'owner')
        self.assertEqual(publish._release_tag(self.conn, env, entry, 'example/repo', run), 'v1.2.3-preview.1')

    def test_highest_full_tag_and_existing_prereleases(self):
        env = SimpleNamespace(name='testproj', workspace=self.repo, settings={'releasePostfix': 'dev'})
        lines = '\n'.join('abc\trefs/tags/' + tag for tag in ('v1.0.0', 'v2.0.0', 'v2.0.1-dev.1', 'v2.0.1-dev.2'))
        self.assertEqual(publish._release_tag(self.conn, env, {}, 'example/repo', lambda *args: (0, lines)), 'v2.0.1-dev.3')

    def test_queue_publishes_chosen_prerelease_and_reuses_it_on_retry(self):
        from unittest.mock import Mock
        env = SimpleNamespace(name='testproj', workspace=self.repo, settings={'releasePostfix': 'dev'})
        entry = {'queue_id': 87, 'plan': 'plan', 'tip': '', 'commit': 'a' * 40, 'version': '', 'repository': 'example/repo'}
        with db.transaction(self.conn):
            publish._store(self.conn, env.name, [entry])
        run = Mock(return_value=(0, 'abc\trefs/tags/v1.2.3'))
        publisher = Mock(side_effect=[ValueError('temporary'), {'id': 1}])
        args = (self.conn, env, 'example/repo', run, lambda repo: None, publisher, lambda **kw: 'token')
        self.assertEqual(publish._drain(*args), [])
        self.assertEqual(publish.pending_releases(self.conn, env.name)[0]['version'], 'v1.2.4-dev.1')
        self.assertEqual(len(publish._drain(*args)), 1)
        self.assertEqual(publisher.call_args_list[0], publisher.call_args_list[1])
        self.assertEqual(run.call_count, 1)

    def test_github_publishes_prerelease_tag(self):
        import io, json
        from urllib.error import HTTPError
        from haivequeen.git_providers import github
        calls = []
        def request(req, timeout):
            calls.append(req)
            if len(calls) in (1, 3):
                raise HTTPError(req.full_url, 404, 'missing', {}, None)
            payload = json.loads(req.data) if req.data else {}
            response = {'object': {'type': 'commit', 'sha': 'a' * 40}} if len(calls) == 2 else payload
            return io.BytesIO(json.dumps(response).encode())
        result = github.publish_release('example/repo', 'v1.2.4-dev.1', 'a' * 40, 'token', _urlopen=request)
        self.assertEqual(result['tag_name'], 'v1.2.4-dev.1')
        self.assertTrue(result['prerelease'])
