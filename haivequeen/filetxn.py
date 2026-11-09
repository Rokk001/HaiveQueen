"""Coordinate inbox file effects with SQLite using a durable redo journal.

Application writers take the same process/thread lock. Inbox actions read their
own staged writes; nothing reaches a user file before the database commits.
Recovery runs before the next coordinated operation, including database open.
An uncoordinated editor is detected by byte comparison: recovery stops and keeps
the journal rather than replacing an unexpected user edit.
"""
import base64
import functools
import errno
import json
import os
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path


_mutex = threading.RLock()
_local = threading.local()


class RecoveryError(RuntimeError):
    """A committed operation needs recovery; never classify it as rejected."""


def _disk(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _encode(value):
    return None if value is None else base64.b64encode(value).decode('ascii')


def _decode(value):
    return None if value is None else base64.b64decode(value)


def atomic_write(path, contents):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp, mode)
        os.replace(temp, path)
        # Windows flushes the file above; POSIX also needs the directory entry.
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _journals():
    from . import config
    return config.config_dir() / 'inbox-transactions'


def _acknowledge(ack):
    """Claim before deleting, so a producer replacing the input cannot lose it.

    The private claim is named in the durable journal. After interruption it is
    either consumed or returned under a unique inbox name before another batch.
    """
    source, claim = Path(ack['source']), Path(ack['claim'])
    if not claim.exists():
        if _disk(source) != _decode(ack['contents']):
            return
        try:
            source.rename(claim)
        except FileNotFoundError:
            return
    if _disk(claim) != _decode(ack['contents']):
        # The producer won the race before rename. Keep its entire file; link
        # creates the unique target exclusively, including on replay.
        saved = Path(ack['replacement'])
        try:
            os.link(claim, saved, follow_symlinks=False)
        except FileExistsError:
            if _disk(saved) != _disk(claim):
                raise RecoveryError('Inbox replacement conflict in ' + str(claim))
    claim.unlink()


def _publish(journal, record):
    """Idempotently finish a committed record, checking *all* targets first."""
    ack = record['ack']
    archived = _disk(ack['target'])
    if archived is not None and archived != _decode(ack['contents']):
        raise RecoveryError('Inbox archive changed; journal: %s' % journal)
    for entry in record['files']:
        if _disk(entry['path']) not in (_decode(entry['before']), _decode(entry['after'])):
            raise RecoveryError('Inbox recovery preserves a concurrent edit in %s; journal: %s' % (entry['path'], journal))
    for entry in record['files']:
        if _disk(entry['path']) != _decode(entry['after']):
            atomic_write(entry['path'], _decode(entry['after']))
    # Archive exactly the bytes consumed, even if a writer replaced the source.
    atomic_write(ack['target'], _decode(ack['contents']))
    _acknowledge(ack)
    journal.unlink()


def _recover():
    for journal in sorted(_journals().glob('*.json')):
        record = json.loads(journal.read_text(encoding='utf-8'))
        # Opening through db.connect would recurse into this recovery operation.
        with closing(sqlite3.connect(Path(record['database']).as_uri() + '?mode=rw', uri=True, timeout=10)) as conn:
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox_receipts'").fetchone()
            committed = table and conn.execute('SELECT 1 FROM inbox_receipts WHERE id=?', (record['id'],)).fetchone()
        if committed:
            finish((journal, record))
        else:
            journal.unlink()  # no user files were published before commit


@contextmanager
def locked():
    """Reentrant thread/process lock, with recovery before each outer call."""
    with _mutex:
        if getattr(_local, 'depth', 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        from . import config
        folder = config.config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / 'inbox-writes.lock', 'a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                # A short nonblocking retry avoids the one-second LK_LOCK poll.
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        time.sleep(.02)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            _local.depth = 1
            try:
                _recover()
                yield
            finally:
                _local.depth = 0
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def coordinated(func):
    @functools.wraps(func)
    def call(*args, **kwargs):
        with locked():
            return func(*args, **kwargs)
    return call


def read(path):
    path = str(Path(path).resolve())
    stage = getattr(_local, 'stage', None)
    if stage is None:
        return _disk(path)
    stage.before.setdefault(path, _disk(path))
    return stage.after.get(path, stage.before[path])


def write(path, contents):
    path = str(Path(path).resolve())
    stage = getattr(_local, 'stage', None)
    if stage is None:
        atomic_write(path, contents)
    else:
        read(path)
        stage.after[path] = contents


class Batch:
    def __init__(self):
        self.before = {}
        self.after = {}

    def prepare(self, conn, source, contents):
        if _disk(source) != contents:
            raise ValueError('inbox file changed while being processed')
        for path, before in self.before.items():
            if _disk(path) != before:
                raise ValueError('concurrent edit preserved in ' + path)
        database = conn.execute('PRAGMA database_list').fetchone()[2]
        if not database:
            raise ValueError('inbox processing requires a persistent database')
        ident = uuid.uuid4().hex
        journal = _journals() / (ident + '.json')
        target = source.parent / 'done' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + source.name)
        if target.exists():
            target = target.with_name(ident + '-' + target.name)
        record = {'id': ident, 'database': str(Path(database).resolve()),
                  'files': [{'path': path, 'before': _encode(self.before[path]), 'after': _encode(after)}
                            for path, after in self.after.items()],
                  'ack': {'source': str(source.absolute()), 'contents': _encode(contents),
                          'claim': str((source.parent / ('.' + ident + '.ack')).absolute()),
                          'replacement': str((source.parent / (ident + '-' + source.name)).absolute()),
                          'target': str(target.resolve())}}
        # Durable prepare precedes the receipt; the receipt commits with ALL SQL.
        atomic_write(journal, json.dumps(record).encode('utf-8'))
        conn.execute('CREATE TABLE IF NOT EXISTS inbox_receipts (id TEXT PRIMARY KEY)')
        conn.execute('INSERT INTO inbox_receipts VALUES (?)', (ident,))
        return journal, record


@contextmanager
def staging():
    if getattr(_local, 'stage', None) is not None:
        raise RuntimeError('nested inbox batch')
    stage = Batch()
    _local.stage = stage
    try:
        yield stage
    finally:
        _local.stage = None


def finish(prepared):
    try:
        _publish(*prepared)
    except (OSError, ValueError) as exc:
        raise RecoveryError('Committed inbox operation awaits recovery: %s' % prepared[0]) from exc
