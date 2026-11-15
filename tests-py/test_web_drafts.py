from bzztest import REL, RoundCase
from haivequeen import db, runtime, web_api


class BrowserDraftTests(RoundCase):
    def create(self, **values):
        return web_api.execute(self.conn, 'plans/create', dict(project='testproj', filename='docs/plans/new.md', content='# Request\n\n- R1: Keep this exact requirement.\n', **values))

    def test_create_import_edit_and_explicit_submit(self):
        result = self.create()
        path = result['plan']
        detail = web_api.execute(self.conn, 'plan/detail', {'project':'testproj', 'plan':path})
        self.assertEqual(detail['plan']['status'], 'DRAFT')
        content = detail['text'] + '\n- R2: Another requirement.\n'
        saved = web_api.execute(self.conn, 'plans/save', {'project':'testproj', 'plan':path, 'content':content, 'revision':detail['revision']})
        self.assertNotEqual(saved['revision'], detail['revision'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            web_api.execute(self.conn, 'plans/save', {'project':'testproj', 'plan':path, 'content':content, 'revision':detail['revision']})
        web_api.execute(self.conn, 'plan/start', {'project':'testproj', 'plan':path})
        self.assertEqual(runtime.list_plans(self.conn, 'testproj')[0]['status'], 'SENT')
        with self.assertRaisesRegex(ValueError, 'DRAFT'):
            web_api.execute(self.conn, 'plans/save', {'project':'testproj', 'plan':path, 'content':content, 'revision':saved['revision']})

    def test_import_active_status_never_starts_and_body_is_preserved(self):
        content = '+++\nstatus = "RUNNING"\nowner_check = "none"\nbranch = "imported"\nworktree = "imported"\n+++\n# Imported\n\n- R1: Keep  spaces.\n'
        result = web_api.execute(self.conn, 'plans/create', {'project':'testproj', 'filename':'docs/plans/import.md', 'content':content})
        text = (self.repo / result['plan']).read_text()
        self.assertIn('status = "DRAFT"', text)
        self.assertIn('owner_check = "none"', text)
        self.assertIn('branch = "imported"', text)
        self.assertIn('worktree = "imported"', text)
        self.assertTrue(text.endswith('# Imported\n\n- R1: Keep  spaces.\n'))
        self.assertEqual(runtime.list_plans(self.conn, 'testproj')[0]['status'], 'DRAFT')

    def test_create_rejects_overwrite_paths_and_malformed_header(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'exists'):
            self.create()
        for filename in ('../escape.md', 'C:/escape.md', 'docs/plans/a:bad.md', 'other/new.md', 'docs/plans/new.txt', 'docs/plans/../new.md'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                web_api.execute(self.conn, 'plans/create', {'project':'testproj', 'filename':filename, 'content':'# Request'})
        for content in ('+++\nstatus = [\n+++\n# Bad', '+++\nstatus="BOGUS"\n+++\n# Bad'):
            with self.assertRaises(ValueError):
                web_api.execute(self.conn, 'plans/create', {'project':'testproj', 'filename':'docs/plans/bad.md', 'content':content})
        self.assertFalse((self.repo / 'docs/plans/bad.md').exists())

    def test_question_history_and_plan_context_preserve_owner_semantics(self):
        self.plan(status='DRAFT')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Review result', 'Read evidence')
        web_api.execute(self.conn, 'questions/answer', {'id':qid, 'text':'This is a note'})
        questions = web_api.execute(self.conn, 'questions', {'project':'testproj', 'history':'1'})
        question = next(q for q in questions if q['id'] == qid)
        self.assertEqual(question['kind'], 'acceptance')
        self.assertEqual(question['status'], 'open')
        self.assertEqual(question['context']['plan'], REL)
        self.assertIn('completeness', question['evidence'])
        ordinary = db.add_question(self.conn, 'testproj', REL, 'scope', 'Choose scope', '')
        web_api.execute(self.conn, 'questions/answer', {'id':ordinary, 'text':'Keep scope'})
        history = web_api.execute(self.conn, 'questions', {'history':'1'})
        self.assertEqual(next(q for q in history if q['id']==ordinary)['answer'], 'Keep scope')
        detail = web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':REL})
        self.assertEqual([q['id'] for q in detail['open_questions']], [qid])

    def test_custom_discovery_exclusions_and_unknown_project(self):
        settings = self.repo / '.haivequeen/project.toml'
        settings.write_text('name="testproj"\nplans=["requests/**/*.md", "!requests/private/**"]\n')
        result = web_api.execute(self.conn, 'plans/create', {'project':'testproj','filename':'requests/new.md','content':'# New'})
        self.assertEqual(result['plan'], 'requests/new.md')
        for project, filename in [('testproj','docs/plans/new.md'), ('testproj','requests/private/new.md'), ('unknown','requests/new.md')]:
            with self.subTest(project=project,filename=filename), self.assertRaises(ValueError):
                web_api.execute(self.conn, 'plans/create', {'project':project,'filename':filename,'content':'# New'})

    def test_resolved_symlink_escape_is_rejected(self):
        from unittest.mock import patch
        from pathlib import Path
        outside = self.repo.parent / 'outside.md'
        with patch.object(Path, 'resolve', side_effect=[self.repo, outside]):
            with self.assertRaisesRegex(ValueError, 'outside'):
                from haivequeen import web_drafts
                web_drafts.target(self.conn, 'testproj', 'docs/plans/new.md')
        self.assertFalse(outside.exists())

    def test_history_records_actual_acceptance_separately_from_notes(self):
        from haivequeen import hives
        self.plan(status='REVIEW')
        hives.create(self.conn, 'testproj', REL, '', '')
        self.conn.execute("UPDATE hives SET state='owner_check'")
        self.conn.commit()
        hives.set_completion(self.conn, 'testproj', REL, 'passed')
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Review result', '')
        web_api.execute(self.conn, 'questions/answer', {'id':qid, 'text':'Reviewed'})
        self.assertIsNone(web_api.execute(self.conn, 'questions', {})[0]['owner_check_result'])
        web_api.execute(self.conn, 'owner/accept', {'project':'testproj','plan':REL})
        self.assertEqual(web_api.execute(self.conn, 'questions', {}), [])
        history = web_api.execute(self.conn, 'questions', {'history':'1'})
        self.assertEqual(history[0]['owner_check_result'], 'accepted')
        self.assertEqual(history[0]['status'], 'answered')

    def test_missing_registered_plan_cannot_be_recreated(self):
        self.plan(status='DONE', rel='docs/plans/new.md')
        (self.repo / 'docs/plans/new.md').unlink()
        with self.assertRaisesRegex(ValueError, 'exists'):
            self.create()

    def test_hidden_wildcard_target_is_not_discoverable(self):
        for filename in ('docs/plans/.hidden.md', 'docs/plans/.private/new.md'):
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'patterns'):
                web_api.execute(self.conn, 'plans/create', {'project':'testproj','filename':filename,'content':'# New'})

    def test_draft_with_active_agent_cannot_be_edited(self):
        self.plan(status='DRAFT')
        self.agent('Worker', status='running')
        detail = web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':REL})
        with self.assertRaisesRegex(ValueError, 'idle DRAFT'):
            web_api.execute(self.conn, 'plans/save', {'project':'testproj','plan':REL,'content':'# Edit','revision':detail['revision']})

    def test_settings_view_exposes_one_effective_plan_field(self):
        result = web_api.settings_view(self.conn, 'testproj')['data']
        self.assertEqual(result['plans'], ['docs/plans/**/*.md'])
        self.assertNotIn('planGlobs', result)
        self.assertNotIn('planExclude', result)

    def test_edit_preserves_existing_owner_policy_and_execution_metadata(self):
        from haivequeen import web_drafts
        content = '+++\nstatus="DRAFT"\nowner_check="none"\nbranch="existing"\nworktree="existing"\n+++\n# Plan\n'
        text = web_drafts.draft_text(content)
        parsed, _ = __import__('haivequeen.plans', fromlist=['split_front_matter']).split_front_matter(text)
        self.assertEqual(parsed['owner_check'], 'none')
        self.assertEqual(parsed['branch'], 'existing')
        self.assertEqual(parsed['worktree'], 'existing')

    def test_discovery_accepts_benign_relative_pattern_segments(self):
        settings = self.repo / '.haivequeen/project.toml'
        settings.write_text('name="testproj"\nplans=["./docs//plans/*.md"]\n')
        self.assertEqual(self.create()['status'], 'DRAFT')

    def test_plan_reader_uses_one_content_snapshot_for_revision(self):
        from pathlib import Path
        from unittest.mock import patch
        from haivequeen import web_drafts
        self.plan(status='DRAFT')
        original = Path.read_bytes
        snapshots = []
        def read(path):
            raw = original(path)
            if path == self.repo / REL:
                snapshots.append(raw)
            return raw
        with patch.object(Path, 'read_bytes', read):
            detail = web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':REL})
        self.assertEqual(detail['revision'], web_drafts.revision(snapshots[0]))
        self.assertEqual(len(snapshots), 1)
