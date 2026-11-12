import json
import tempfile
import unittest
from pathlib import Path

from haivequeen import agent_runner as ar, gui_data, db, runtime, bees
from unittest import mock


class CodexChildTest(unittest.TestCase):
    def test_metadata_parent_identity_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def child(thread, parent, path, ctx=40):
                meta = {'type': 'session_meta', 'payload': {'id': thread, 'source': {'subagent': {'thread_spawn': {'parent_thread_id': parent, 'agent_path': path}}}}}
                count = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': ctx}, 'total_token_usage': {'input_tokens': 100, 'output_tokens': 9}}}}
                (root / ('rollout-' + thread + '.jsonl')).write_text('\n'.join(json.dumps(x, separators=(',', ':')) for x in (meta, count)), encoding='utf8')
            child('child1', 'parent', '/root/agent_13')
            child('other', 'different', '/root/agent_13')
            got = ar.get_codex_children('parent', root, ['Agent-13'])
            self.assertEqual((got['child1']['desc'], got['child1']['ctx'], got['child1']['tin'], got['child1']['tout']), ('Agent-13', 40, 100, 9))
            child('child2', 'parent', '/root/agent_13')
            self.assertEqual(ar.get_codex_children('parent', root, ['Agent-13']), {})
            self.assertEqual(ar.get_codex_children('parent', root, ['Agent-13', 'Agent_13']), {})

    def test_runner_persists_usage_and_dashboard_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = db.connect(root / 'db.sqlite')
            try:
                db.upsert_project(conn, 'P', None, 'cyan', 1)
                runtime.upsert_agent(conn, 'P', 'queen', {'plan': 'plan', 'phase': 'queen', 'status': 'running'})
                bees.record_embedded_start(conn, 'P', 'plan', 'A1', 'Agent-13', 'medium', 'codex', queen='queen')
                meta = {'type': 'session_meta', 'payload': {'id': 'child', 'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'parent', 'agent_path': '/root/agent_13'}}}}}
                def count(ctx, total, out):
                    return {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'input_tokens': ctx}, 'total_token_usage': {'input_tokens': total, 'output_tokens': out}}}}
                (root / 'rollout-child.jsonl').write_text('\n'.join(json.dumps(x, separators=(',', ':')) for x in (meta, count(20, 20, 2), count(40, 100, 9))), encoding='utf8')
                run = ar.AgentRun(conn, 'P', 'queen', 'codex', 'codex', root, root, 'plan', phase='queen', out=mock.Mock(), sessions=root)
                run.thread, run.tin, run.tout, run.ctx = 'parent', 200, 10, 80
                run.push_state(True)
                queen = runtime.get_agent(conn, 'P', 'queen')
                self.assertEqual((queen['tokens_in'], queen['tokens_out'], queen['ctxTokens']), (300, 19, 80))
                sub = gui_data.match_sub(queen, 'Agent-13')
                self.assertEqual((sub['ctx'], sub['tin'], sub['tout']), (40, 100, 9))
                starts = conn.execute("SELECT tokens FROM start_contexts WHERE kind='subbee'").fetchall()
                self.assertEqual([r[0] for r in starts], [20])
                run.push_state(True)
                self.assertEqual(conn.execute("SELECT count(*) FROM start_contexts WHERE kind='subbee'").fetchone()[0], 1)
            finally:
                conn.close()
