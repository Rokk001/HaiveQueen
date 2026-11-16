import unittest
from bzztest import RoundCase, REL
from unittest import mock
from haivequeen import db, web_api, web_drafts, question_service

class QuestionsLoadingTests(RoundCase):
    def test_compact_history_does_not_expand_evidence_or_packages(self):
        qid=db.add_question(self.conn,'testproj',REL,'item','Question','Recommendation')
        with mock.patch.object(web_drafts,'question_context') as context, mock.patch.object(question_service,'plan_packages') as packages:
            rows=web_api.execute(self.conn,'questions',{'history':'1','compact':'1'})
        context.assert_not_called()
        packages.assert_not_called()
        self.assertEqual(rows[0]['id'],qid)
        self.assertEqual(rows[0]['question'],'Question')
        self.assertTrue(rows[0]['details_pending'])

    def test_selected_question_keeps_full_evidence(self):
        qid=db.add_question(self.conn,'testproj',REL,'item','Question','Recommendation')
        expected=web_api.execute(self.conn,'questions',{'history':'1'})[0]
        self.assertEqual(web_api.execute(self.conn,'questions/detail',{'id':qid}),expected)
        with self.assertRaises(KeyError):web_api.execute(self.conn,'questions/detail',{'id':999999})

class QuestionsLoadingUITests(unittest.TestCase):
    def test_deferred_loading_and_initial_state(self):
        import subprocess, shutil
        from pathlib import Path
        if not shutil.which('node'): self.skipTest('Node required')
        result=subprocess.run(['node',str(Path(__file__).with_name('web_questions_loading_test.cjs'))],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
