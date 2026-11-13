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
