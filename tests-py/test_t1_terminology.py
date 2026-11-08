"""Regression tests for the terminology and owner-check transition."""
import unittest
import ast
import re
from pathlib import Path
from bzztest import REL, RoundCase
from haivequeen import claude_hook, cli, db, gui_questions, hives, profile, runtime

ROOT = Path(__file__).resolve().parent.parent


class TerminologyTests(unittest.TestCase):
    def test_agent_command_and_legacy_alias_share_handler(self):
        parser = cli.build_parser()
        args = ['start', '--project', 'p', '--plan', 'x', '--package', 'P', '--class', 'light']
        self.assertIs(parser.parse_args(['agent'] + args).fn, parser.parse_args(['bee'] + args).fn)

    def test_started_column_and_translation_rule(self):
        self.assertIn('tr("Started")', (ROOT / 'haivequeen/gui_hives.py').read_text(encoding='utf-8'))
        rule = (ROOT / 'haivequeen/i18n.py').read_text(encoding='utf-8').split('"""', 2)[1]
        self.assertNotIn('product names Apiary, Hive, Queen and Bee', rule)

    def test_agent_alias_is_permitted_by_queen_hook(self):
        policy = profile.claude_hook_policy('p', 'plan.md', 42, 'origin', 'main', 'python', 'cli.py', 'branch', name='Queen')
        args = ['agent', 'wait', '--project', 'p', '--plan', 'plan.md', '--package', 'P', '--class', 'medium']
        self.assertTrue(claude_hook.bzz_allowed(args, policy))

    def test_display_catalog_uses_standard_terms_in_both_languages(self):
        from haivequeen import i18n
        old = re.compile(r'\b(?:Apiar(?:y|ies)|Hives?|Beekeepers?|Bees?)\b', re.I)
        for key, value in i18n.GERMAN.items():
            with self.subTest(key=key):
                self.assertIsNone(old.search(key))
                self.assertIsNone(old.search(value))

    def test_display_calls_do_not_use_legacy_terms(self):
        old = re.compile(r'\b(?:Apiar(?:y|ies)|Hives?|Beekeepers?|Bees?)\b', re.I)
        for path in (ROOT / 'haivequeen').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Call) and getattr(node.func, 'id', '') in ('tr', 'N_') and node.args:
                    value = node.args[0]
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        with self.subTest(path=path.name, line=node.lineno):
                            self.assertIsNone(old.search(value.value))


class OwnerCheckTransitionTests(RoundCase):
    def test_existing_config_plan_and_inbox_keep_their_keys(self):
        from unittest import mock
        from haivequeen import config, inbox, plans
        settings = self.base / 'legacy-config'
        settings.mkdir()
        content = 'hives = 4\n[[providers]]\nname="legacy"\nprotocol="claude"\nbeeMode="process"\n[[projects]]\nname="testproj"\n'
        path = settings / 'config.toml'
        path.write_text(content, encoding='utf-8')
        with mock.patch.object(config, 'config_dir', return_value=settings):
            loaded = config.load_config()
        self.assertEqual(loaded['hives'], 4)
        self.assertEqual(loaded['providers'][0]['beeMode'], 'process')
        self.assertEqual(path.read_text(encoding='utf-8'), content)
        self.plan('READY', [{'id': 'P1'}])
        plan_path = self.repo / REL
        before = plan_path.read_bytes()
        self.assertEqual(plans.load_plan(str(plan_path)).packages[0].id, 'P1')
        self.assertEqual(plan_path.read_bytes(), before)
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        box = inbox.inbox_dir(self.repo)
        box.mkdir(parents=True, exist_ok=True)
        (box / 'legacy.toml').write_text('[[answer]]\nquestion_id=%d\ntext="ok"\nby="owner"\n' % qid, encoding='utf-8')
        self.round()
        row = self.conn.execute('SELECT status,answer FROM questions WHERE id=?', (qid,)).fetchone()
        self.assertEqual(tuple(row), ('open', 'ok'))
        self.assertTrue(any((box / 'done').glob('*legacy.toml')))

    def test_runtime_question_fields_remain_compatible(self):
        db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        question = runtime.list_questions(self.conn, project='testproj')[0]
        self.assertNotIn('owner_check_result', question)

    def test_gui_owner_check_note_does_not_reject_packages(self):
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        gui_questions.answer(self.conn, qid, 'A note', rejected=['P1'])
        self.assertEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (qid,)).fetchone()[0], 'open')

    def test_unprocessed_answer_remains_open(self):
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        db.answer_question(self.conn, qid, 'ok', 'owner')
        self.assertEqual(hives.owner_check_step(self.conn, 'testproj', REL), ('wait', [qid]))
        row = self.conn.execute('SELECT status, answer FROM questions WHERE id=?', (qid,)).fetchone()
        self.assertEqual(tuple(row), ('open', 'ok'))

    def test_processed_results_are_migrated_once(self):
        accepted = db.add_question(self.conn, 'testproj', REL, 'owner-check-answered', 'Check?', '')
        rejected = db.add_question(self.conn, 'testproj', REL, 'owner-check-answered', 'Check?', '')
        db.answer_question(self.conn, accepted, 'ok', 'owner')
        db.answer_question(self.conn, rejected, 'still broken', 'owner')
        self.conn.execute('PRAGMA user_version = 15')
        db.migrate(self.conn)
        rows = self.conn.execute('SELECT id, owner_check_result FROM questions WHERE id IN (?, ?) ORDER BY id', (accepted, rejected)).fetchall()
        self.assertEqual([row[1] for row in rows], ['accepted', 'rejected'])
        self.conn.execute("UPDATE questions SET answer='no' WHERE id=?", (accepted,))
        db.migrate(self.conn)
        self.assertEqual(self.conn.execute('SELECT owner_check_result FROM questions WHERE id=?', (accepted,)).fetchone()[0], 'accepted')

    def test_upgrade_distinguishes_done_plans_from_pending_answers(self):
        from haivequeen import runtime, i18n
        self.plan('DONE', [{'id': 'P1'}])
        done = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check?', '')
        pending = db.add_question(self.conn, 'testproj', 'other.md', 'owner-check-build17', 'Check?', '')
        for qid in (done, pending):
            self.conn.execute("UPDATE questions SET status='answered',answer='Yes!' WHERE id=?", (qid,))
        self.conn.execute('PRAGMA user_version = 15')
        db.migrate(self.conn)
        self.assertEqual(db.owner_check_result(self.conn, done), 'accepted')
        self.assertIsNone(db.owner_check_result(self.conn, pending))
        self.assertEqual(runtime.list_questions(self.conn, plan='other.md')[0]['status'], 'open')
        self.assertIsNone(hives.owner_check_step(self.conn, 'testproj', REL))
        old = 'Bee slots not reserved: old text'
        self.assertEqual(i18n.log_text(old, language='German'), old)

    def test_old_schema_upgrade_preserves_question_fields_and_events(self):
        import sqlite3
        from haivequeen import runtime
        conn = sqlite3.connect(':memory:', isolation_level=None)
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        legacy_schema = (ROOT / 'schema.sql').read_text(encoding='utf-8').replace(',\n    owner_check_result TEXT', '')
        conn.executescript(legacy_schema)
        conn.execute('PRAGMA user_version=15')
        qid = db.add_question(conn, 'legacy', 'plan.md', 'owner-check', 'Check?', '')
        conn.execute("UPDATE questions SET status='answered',answer='ok' WHERE id=?", (qid,))
        db.add_event(conn, 'legacy', 'note', 'Apiary and Hive with Bee')
        db.migrate(conn)
        question = runtime.list_questions(conn)[0]
        self.assertEqual(set(question), {'id','project','plan_path','item','question','recommendation','status','answer','answered_by','created_at','answered_at'})
        self.assertEqual(question['status'], 'open')
        self.assertEqual(conn.execute("SELECT text FROM events WHERE kind='note'").fetchone()[0], 'Apiary and Hive with Bee')
