import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from bzztest import RoundCase
from haivequeen import dashboard_data, live_activity, runtime, web_api


class LiveActivityTests(RoundCase):
    def test_browser_activity_preserves_details_and_selected_identity(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required')
        result = subprocess.run([node, str(Path(__file__).with_name('web_live_activity_test.cjs'))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def read_events(self, events, status='running'):
        path = self.base / 'events.jsonl'
        path.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
        return live_activity.read(path, status)

    def test_codex_updates_keep_command_and_actual_exit(self):
        actions = self.read_events([
            {'type': 'item.started', 'item': {'id': 'one', 'type': 'command_execution', 'command': 'rg needle source'}},
            {'type': 'item.completed', 'item': {'id': 'one', 'type': 'command_execution', 'exit_code': 2, 'aggregated_output': 'bad input'}},
            {'type': 'item.completed', 'item': {'id': 'two', 'type': 'mcp_tool_call', 'tool': 'lookup', 'arguments': {'q': 'term'}, 'result': {'ok': True}}},
        ])
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]['command'], 'rg needle source')
        self.assertEqual(actions[0]['label'], 'Searching files')
        self.assertEqual((actions[0]['state'], actions[0]['exit']), ('failed', 2))
        self.assertEqual(actions[1]['state'], 'completed')
        self.assertIsNone(actions[1]['exit'])

    def test_claude_tools_and_orphan_result_remain_recorded(self):
        actions = self.read_events([
            {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'read', 'name': 'Read', 'input': {'file_path': 'source.py'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'read', 'content': [{'type': 'text', 'text': 'file text'}]}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'outside-tail', 'is_error': True, 'content': 'failed'}]}},
        ])
        self.assertEqual(actions[0]['label'], 'Reading files')
        self.assertIn('source.py', actions[0]['command'])
        self.assertIn('file text', actions[0]['output'])
        self.assertEqual(actions[1]['state'], 'failed')
        self.assertEqual(actions[1]['command'], '')

    def test_opencode_and_stopped_incomplete_commands(self):
        actions = self.read_events([
            {'type': 'tool_use', 'part': {'id': 'one', 'tool': 'bash', 'state': {'input': {'command': 'cat source.py'}, 'status': 'completed', 'output': 'text', 'metadata': {'exit': 0}}}},
            {'type': 'item.started', 'item': {'id': 'two', 'type': 'command_execution', 'command': 'unknown'}},
        ], 'done')
        self.assertEqual(actions[0]['state'], 'completed')
        self.assertEqual(actions[0]['label'], 'Reading files')
        self.assertEqual(actions[1]['state'], 'unconfirmed')

    def test_malformed_shapes_and_missing_events_produce_no_invented_work(self):
        self.assertEqual(live_activity.read(self.base / 'missing'), [])
        self.assertEqual(self.read_events([None, [], {'type': 'assistant', 'message': 'bad'},
            {'type': 'assistant', 'message': {'content': 'bad'}}, {'type': 'tool_use', 'part': {'state': 'bad'}},
            {'type': 'item.started', 'item': []}]), [])

    def test_tail_and_details_are_bounded_even_with_incomplete_last_line(self):
        path = self.base / 'events.jsonl'
        events = [{'type': 'item.completed', 'item': {'id': str(i), 'type': 'command_execution',
                  'command': 'x' * 5000, 'aggregated_output': 'y' * 7000}} for i in range(12)]
        path.write_text('x' * 300000 + '\n' + '\n'.join(map(json.dumps, events)) + '\n{"type":', encoding='utf-8')
        actions = live_activity.read(path)
        self.assertEqual(len(actions), 8)
        self.assertEqual(actions[0]['id'], '4')
        self.assertEqual(len(actions[-1]['command']), 4000)
        self.assertEqual(len(actions[-1]['output']), 6000)

    def test_api_keeps_selected_agent_activity_with_its_milestones(self):
        folder = self.base / 'selected-run'; folder.mkdir()
        (folder / 'milestones.txt').write_text('Green\tSelected milestone\n', encoding='utf-8')
        (folder / 'events.jsonl').write_text(json.dumps({'type': 'item.started', 'item': {
            'id': 'selected-tool', 'type': 'command_execution', 'command': 'rg selected'}}), encoding='utf-8')
        with mock.patch.object(runtime, 'get_agent', return_value={'project': 'selected-project', 'name': 'selected-agent', 'status': 'done'}) as lookup, \
             mock.patch.object(dashboard_data, 'live_path', return_value=folder / 'milestones.txt') as path:
            result = web_api.execute(self.conn, 'live', {'project': 'selected-project', 'agent': 'selected-agent'})
        lookup.assert_called_once_with(self.conn, 'selected-project', 'selected-agent')
        path.assert_called_once_with('selected-project', 'selected-agent')
        self.assertIn('Selected milestone', result['lines'][0][1])
        self.assertEqual(result['activity'][0]['id'], 'selected-tool')
        self.assertEqual(result['activity'][0]['state'], 'unconfirmed')
