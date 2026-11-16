import sqlite3
import unittest
from unittest.mock import patch
from bzztest import RoundCase, db, runtime
from haivequeen import inbox, loop


class ObsoleteQuestionsTest(RoundCase):
    def test_startup_closes_only_obsolete_open_and_triage_questions_once(self):
        self.conn.execute("INSERT INTO plans(project,path,title,status,updated_at) VALUES (?,?,?,?,?)", ('testproj', 'finished', 'Done', 'DONE', db.now()))
        self.conn.execute("INSERT INTO plans(project,path,title,status,updated_at) VALUES (?,?,?,?,?)", ('testproj', 'rejected', 'Rejected', 'CANCELLED', db.now()))
        ids = [db.add_question(self.conn, 'testproj', 'finished', 'a', 'a', ''),
               db.add_question(self.conn, 'testproj', 'rejected', 'b', 'b', '', status='triage'),
               db.add_question(self.conn, 'unknown', None, 'c', 'c', '')]
        kept = [db.add_question(self.conn, 'testproj', None, 'd', 'd', ''),
                db.add_question(self.conn, 'testproj', 'missing', 'e', 'e', ''),
                db.add_question(self.conn, 'testproj', 'finished', 'f', 'f', '', status='fault_check')]
        with patch.object(loop.sch, 'load_projects', return_value=[]), patch.object(loop, 'configuration_stamp', side_effect=RuntimeError('stop after startup')):
            with self.assertRaisesRegex(RuntimeError, 'stop after startup'):
                loop._run_loop(self.conn, {}, 1, 0, lambda _: None, 0, {})
        for qid in ids:
            q = self.conn.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
            self.assertEqual((q['status'], q['answer'], q['answered_by']), ('answered', 'closed: plan finished or project unknown', 'coordinator'))
        for qid in kept:
            self.assertNotEqual(self.conn.execute('SELECT status FROM questions WHERE id=?', (qid,)).fetchone()[0], 'answered')
        loop.close_obsolete_questions(self.conn)
        events = db.rows(self.conn, "SELECT * FROM events WHERE kind='answer'")
        self.assertEqual(len(events), 3)

    def test_legacy_rejected_plan_is_migrated_and_its_question_closed(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        schema = db.SCHEMA_FILE.read_text(encoding='utf-8')
        conn.executescript(schema.replace("'CANCELLED'", "'REJECTED'"))
        conn.execute("INSERT INTO projects(name) VALUES ('legacy')")
        conn.execute("INSERT INTO plans(project,path,status) VALUES ('legacy','old','REJECTED')")
        qid = db.add_question(conn, 'legacy', 'old', 'a', 'Question?', '')
        conn.execute('PRAGMA user_version = 14')
        db.migrate(conn)
        self.assertEqual(conn.execute("SELECT status FROM plans WHERE path='old'").fetchone()[0], 'CANCELLED')
        loop.close_obsolete_questions(conn)
        self.assertEqual(conn.execute('SELECT status FROM questions WHERE id=?', (qid,)).fetchone()[0], 'answered')

    def test_inbox_rejects_answer_to_other_project_by_id(self):
        qid = db.add_question(self.conn, 'other', 'other-plan', 'P1', 'Question?', '')
        folder = inbox.inbox_dir(self.repo)
        folder.mkdir(parents=True)
        (folder / 'answer.toml').write_text('[[answer]]\nquestion_id = %d\ntext = "yes"\nby = "owner"\n' % qid, encoding='utf-8')
        self.round()
        question = runtime.list_questions(self.conn, 'other', status='open')[0]
        self.assertEqual(question['id'], qid)
        self.assertFalse(question['answer'])
        self.assertEqual(runtime.list_questions(self.conn, 'other', status='answered'), [])
        self.assertEqual(runtime.list_decisions(self.conn, 'other'), [])
