"""Bounded pages for one selected integration log source."""
import base64
import codecs
import hashlib
import hmac
import json
import os
from pathlib import Path

from . import action_logs

PAGE_SIZE = 32768
_KEY = os.urandom(32)


def _token(scope, name):
    payload = json.dumps([scope, name], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(payload + hmac.digest(_KEY, payload, 'sha256')).decode()


def _name(scope, token):
    try:
        raw = base64.urlsafe_b64decode(token)
        payload, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.digest(_KEY, payload, 'sha256')):
            raise ValueError
        saved_scope, name = json.loads(payload)
        if saved_scope != scope:
            raise ValueError
        return name
    except (ValueError, TypeError):
        raise ValueError('Log source expired or belongs to another run; reopen the console.') from None


def _cursor(value):
    if not value:
        return None
    try:
        values = json.loads(value) if isinstance(value, str) else value
        if values is None:
            return None
        if len(values) != 2 or any(type(v) is not int or v < 0 for v in values):
            raise ValueError
        return values
    except (ValueError, TypeError):
        raise ValueError('Invalid log cursor') from None


def file_page(path, cursor):
    """Use byte positions, retaining incomplete multibyte characters for the next read."""
    offset = (cursor or [0, 0])[0]
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        if offset > size:
            offset = 0
        stream.seek(0)
        bom = stream.read(3)
        encoding = 'utf-16-le' if bom.startswith(b'\xff\xfe') else 'utf-16-be' if bom.startswith(b'\xfe\xff') else 'utf-8'
        if encoding.startswith('utf-16') and offset % 2:
            raise ValueError('Invalid UTF-16 log cursor')
        stream.seek(offset)
        raw = stream.read(PAGE_SIZE)
    decoder = codecs.getincrementaldecoder(encoding)(errors='replace')
    text = decoder.decode(raw, final=False)
    pending = decoder.getstate()[0]
    following = offset + len(raw) - len(pending)
    return {'output': text.lstrip('\ufeff') if offset == 0 else text,
            'cursor': [following, 0], 'more': following < size and following > offset,
            'reset': cursor is not None and offset != cursor[0]}


def response(conn, project, plan, step, record, journal, summary, request):
    from . import web_step_logs
    run = Path(journal['path']).name if journal else hashlib.sha256(
        json.dumps(record, sort_keys=True).encode()).hexdigest()
    scope = [project, plan, step, run]
    reset = bool(request.get('run') and request['run'] != run)
    cursor = None if reset else _cursor(request.get('cursor'))
    source = '' if reset else request.get('source', '')
    references = []
    continuation = ''
    if source:
        name = _name(scope, source)
        root = web_step_logs.reference_root(conn, project, plan)
        if root is None:
            raise ValueError('Plan worktree unavailable')
        path = (root / name).resolve()
        if not path.is_relative_to(root / 'out') or '..' in Path(name).parts:
            raise ValueError('Path outside plan output folder')
        try:
            page = file_page(path, cursor)
        except OSError:
            page = {'output': 'Referenced log unavailable: ' + name, 'cursor': cursor or [0, 0], 'more': False}
    else:
        if journal:
            try:
                page = action_logs.page(journal['path'], cursor)
            except ValueError:
                page = action_logs.page(journal['path'])
                reset = True
        else:
            text = record.get('output', '')
            start = (cursor or [0, 0])[0]
            output = text[start:start + PAGE_SIZE]
            page = {'output': output, 'cursor': [start + len(output), 0], 'more': start + len(output) < len(text)}
        carry = ''
        if not reset and request.get('continuation'):
            saved_cursor, tail = _name(scope + ['continuation'], request['continuation'])
            if saved_cursor == cursor:
                carry = tail
        context = carry + page['output']
        continuation = _token(scope + ['continuation'], [page['cursor'], context[-1024:]])
        for item in web_step_logs.referenced_logs(conn, project, plan,
                context + '\n' + summary['reason'][:8192], content=False):
            item['source'] = _token(scope, item['path'])
            references.append(item)
    # One payload only, no repeated console/file content in metadata.
    return {**summary, 'reason': summary['reason'][:8192], 'error': summary['error'][:8192],
            'output': page['output'], 'cursor': page['cursor'], 'more': page['more'],
            'run': run, 'source': source, 'reset': reset or page.get('reset', False),
            'references': references, 'continuation': continuation}
