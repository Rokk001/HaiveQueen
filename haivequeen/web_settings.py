"""Settings operations for web clients, with a revision for each file."""
import hashlib
from pathlib import Path

from . import web_secrets, filetxn, scheduler, settings as settings_store


def _revision(source):
    path, contents = source
    digest = hashlib.sha256()
    digest.update(path.encode('utf-8'))
    digest.update(b'\0missing' if contents is None else b'\0present\0' + contents)
    return digest.hexdigest()


@filetxn.coordinated
def load(path):
    """Return editable settings and a JSON-safe revision for a later save."""
    data = settings_store.load_raw(path)
    return {'settings': web_secrets.redact(data), 'revision': _revision(data.source)}


@filetxn.coordinated
def save(path, settings, revision, *, project=False, conn=None):
    """Save only if the file still matches the revision the client read."""
    data = settings_store.load_raw(path)
    if not isinstance(revision, str) or revision != _revision(data.source):
        raise ValueError('settings changed since loading; reload before saving')
    settings = web_secrets.restore(settings, settings_store.load_migrated(path), data)
    if not project:
        for provider in settings.get('providers', []):
            scheduler.capability_thresholds(provider)
    data.clear()
    data.update(settings)
    if project and conn is not None:
        settings_store.save_project_file(conn, Path(path), data)
    elif project and data.get('publishReleases'):
        raise ValueError('project database connection required for release settings')
    else:
        settings_store.save_file(path, data)
    return {'settings': web_secrets.redact(data), 'revision': _revision(data.source)}
