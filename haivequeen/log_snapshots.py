"""Run-owned copies of explicitly referenced output logs, retained with the journal."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from .filetxn import atomic_write


def folder(journal):
    path = Path(journal)
    return path.parent / ('refs-' + hashlib.sha256(path.name.encode()).hexdigest()[:24])


def names(text):
    return list(dict.fromkeys(m.group(1).replace('\\', '/') for m in re.finditer(
        r'''(?i)\b(?:see|log)\s+["']?(out[\\/][^\r\n"']*?\.(?:txt|log))(?=[\s"']|$)''', text)))


def capture(journal, records, message=''):
    target = folder(journal)
    if (target / 'manifest.json').is_file():
        return
    refs = names(''.join(r.get('text', '') for r in records if r['event'] == 'output') + '\n' + message)
    execution = next((r for r in reversed(records) if r['event'] == 'execution'), {})
    root = Path(execution['cwd']).resolve() if execution.get('cwd') else None
    manifest = {}
    target.mkdir(exist_ok=True)
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise OSError('Snapshot directory is linked')
    for name in refs:
        item = {}
        destination = target / (hashlib.sha256(name.encode()).hexdigest()[:24] + '.log')
        try:
            if root is None:
                raise ValueError('Execution directory unavailable at completion')
            source = (root / name).resolve()
            if '..' in Path(name).parts or not source.is_relative_to(root / 'out'):
                raise ValueError('Path outside execution output folder')
            before = source.stat()
            with source.open('rb') as incoming, destination.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError('Log changed while being archived')
            item = {'file': destination.name, 'size': before.st_size, 'mtime': before.st_mtime}
        except (OSError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            item = {'error': str(exc)}
        manifest[name] = item
    atomic_write(target / 'manifest.json', json.dumps(manifest).encode('utf-8'))


def resolve(journal, name):
    """None means a legacy/live run, never a missing archived reference."""
    if not journal:
        return None
    target = folder(journal)
    manifest = target / 'manifest.json'
    if not manifest.exists():
        from . import action_logs
        if any(r.get('references_snapshot') for r in action_logs.metadata(journal)):
            raise ValueError('Snapshot unavailable for this completed run')
        return None
    items = json.loads(manifest.read_text(encoding='utf-8'))
    item = items.get(name, {'error': 'Not archived for this run'})
    if item.get('error'):
        raise ValueError(item['error'])
    path = target / (hashlib.sha256(name.encode()).hexdigest()[:24] + '.log')
    if path.resolve().parent != target.resolve() or target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise ValueError('Invalid snapshot path')
    return path
