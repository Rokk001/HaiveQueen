"""Bounded activity from recorded provider events; never infer unreported work."""
import json
import re
from .dashboard_data import tail_file
from .agent_runner import _plain_commands


def command_label(command):
    commands = _plain_commands(command)
    labels = []
    for words in commands:
        name = re.split(r"[/\\]", words[0])[-1].lower().removesuffix('.exe')
        label = {'get-content': 'Reading files', 'cat': 'Reading files',
                 'get-childitem': 'Listing files', 'ls': 'Listing files',
                 'rg': 'Searching files', 'grep': 'Searching files',
                 'git': 'Git command'}.get(name)
        if label and label not in labels:
            labels.append(label)
    return ' / '.join(labels[:2]) or 'Executing command'


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def read(path, agent_status='running'):
    """At most eight recorded actions from a 256 KiB tail, without reading older history."""
    actions = {}

    def update(key, command=None, output=None, state='running', code=None, tool=''):
        if not isinstance(key, (str, int)) or key == '':
            return
        key = str(key)
        value = actions.setdefault(key, dict(command='', output='', exit=None, state='running', label='Tool activity'))
        if command is not None:
            value['command'] = _text(command)[:4000]
            value['label'] = {'Read': 'Reading files', 'read': 'Reading files',
                'Grep': 'Searching files', 'grep': 'Searching files', 'Glob': 'Listing files',
                'glob': 'Listing files', 'Write': 'Writing files', 'write': 'Writing files',
                'Edit': 'Editing files', 'edit': 'Editing files', 'file_change': 'Editing files'
                }.get(tool, command_label(value['command']) if tool in ('', 'Bash', 'bash', 'shell') else 'Tool: ' + tool[:80])
        if output is not None:
            value['output'] = _text(output)[-6000:]
        value.update(state=state, exit=code if type(code) is int else None)
        if type(code) is int and code != 0:
            value['state'] = 'failed'

    for line in tail_file(path, lines=160, max_bytes=256 * 1024):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        item = _dict(event.get('item'))
        if kind in ('item.started', 'item.updated', 'item.completed') and item.get('type') in ('command_execution', 'mcp_tool_call', 'file_change'):
            tool = '' if item['type'] == 'command_execution' else str(item.get('tool') or item['type'])
            command = item.get('command') if not tool else _text(item.get('arguments') or item.get('changes') or tool)
            update(item.get('id'), command, item.get('aggregated_output', item.get('result', item.get('error'))),
                   'failed' if item.get('status') == 'failed' or item.get('error') else
                   'completed' if kind == 'item.completed' else 'running', item.get('exit_code'), tool)
        elif kind == 'tool_use':
            part = _dict(event.get('part')); state = _dict(part.get('state')); inputs = _dict(state.get('input'))
            tool = str(part.get('tool') or 'Tool')
            update(part.get('id'), inputs.get('command') or _text(inputs) if inputs else tool,
                   state.get('output', state.get('error')), {'completed': 'completed', 'error': 'failed'}.get(state.get('status'), 'running'),
                   _dict(state.get('metadata')).get('exit'), tool)
        elif kind in ('assistant', 'user'):
            content = _dict(event.get('message')).get('content')
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get('type') == 'tool_use':
                    inputs = _dict(part.get('input')); tool = str(part.get('name') or 'Tool')
                    update(part.get('id'), inputs.get('command') or _text(inputs), tool=tool)
                elif part.get('type') == 'tool_result':
                    update(part.get('tool_use_id'), output=part.get('content'),
                           state='failed' if part.get('is_error') else 'completed')
    result = []
    for key, value in list(actions.items())[-8:]:
        if value['state'] == 'running' and agent_status not in ('running', 'starting'):
            value['state'] = 'unconfirmed'
        result.append(dict(id=key, **value))
    return result
