"""Owner web release history and promotion; no plan or CLI publication entry."""
import hashlib
import re
from types import SimpleNamespace

from . import git_provider, projsettings, settings


def context(conn, project):
    from .web_api import settings_path
    if not project:
        raise ValueError('Choose a project')
    path = settings_path(conn, project)
    data = projsettings.working_settings(settings.load_raw(path))
    env = SimpleNamespace(name=project, workspace=path.parent.parent, settings=data)
    provider, errors = git_provider.for_project(env)
    if not provider:
        raise ValueError('; '.join(errors) or 'Git provider is not supported for this remote')
    return provider, data


def listing(conn, project):
    provider, data = context(conn, project)
    if 'release_history' not in provider.capabilities:
        return {'releases': [], 'reason': 'This provider does not support release history.'}
    return {'releases': provider.call('release_history', provider.credential()), 'reason': ''}


def promote(provider, mode, selected):
    """Use provider-owned source metadata, never a browser-supplied commit or URL."""
    if 'release_history' not in provider.capabilities:
        raise ValueError('This provider does not support release history or promotion.')
    token = provider.credential()
    rows = provider.call('release_history', token)
    source = next((row for row in rows if row['tag'] == selected), None)
    if not source or not source['prerelease'] or not source['full_tag']:
        raise ValueError('Choose a versioned pre-release')
    tag, commit = source['full_tag'], source['commit']
    if not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag):
        raise ValueError('Invalid full release version')
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', commit):
        raise ValueError('Pre-release has no exact commit')
    if mode == 'git':
        provider.call('release_tag', tag, commit, token)
        return {'tag': tag, 'commit': commit, 'message': 'Tag set; the provider workflow publishes the release.'}
    required = {'release_prepare', 'release_snapshot', 'release_download', 'release_upload', 'release_asset_digest'}
    if mode != 'local' or not required <= provider.capabilities:
        raise ValueError('This provider does not support copying local release assets')
    original = provider.call('release_snapshot', source['tag'], commit, token)
    contents = {}
    for asset in original['assets']:
        name = asset['name']
        if name in contents:
            raise ValueError('Pre-release has duplicate asset names')
        content = provider.call('release_download', original, asset, token)
        if not isinstance(content, bytes):
            raise ValueError('Invalid release asset content')
        contents[name] = content
    target = provider.call('release_prepare', tag, commit, token)
    existing = {asset['name']: asset for asset in target['assets']}
    if set(existing) - set(contents):
        raise ValueError('Full release contains files absent from the pre-release')
    for name, content in contents.items():
        asset = existing.get(name)
        if asset is None:
            asset = provider.call('release_upload', target, name, content, token)
        digest = provider.call('release_asset_digest', target, asset, token)
        if digest != hashlib.sha256(content).hexdigest():
            raise ValueError('Full release file differs from the pre-release: ' + name)
    return {'tag': tag, 'commit': commit, 'files': list(contents)}
