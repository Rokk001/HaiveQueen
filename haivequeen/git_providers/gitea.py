"""Gitea and Forgejo REST adapter. See GITEA.md for the interface."""
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'asset_upload', 'release_list', 'pipeline', 'login'}) | {'pr_status'}
LOGIN_OPTIONS = {'device': False, 'origin_required': True, 'token_env': ('GITEA_TOKEN',)}


def detect(remote):
    if not isinstance(remote, str):
        return None
    remote = remote.strip()
    scp = re.fullmatch(r'(?:[^/@:]+@)?([^/:]+):([^?#]+)', remote)
    if scp and '://' not in remote:
        host, path = scp.groups()
        origin = 'https://' + host
    else:
        try:
            parsed = urlsplit(remote)
            if parsed.scheme not in ('http', 'https', 'ssh') or not parsed.hostname or parsed.query or parsed.fragment:
                return None
            host, path = parsed.hostname, parsed.path.lstrip('/')
            origin = ('https://' + parsed.hostname if parsed.scheme == 'ssh' else parsed.scheme + '://' + parsed.netloc.rsplit('@', 1)[-1])
        except ValueError:
            return None
    hosts = {'gitea.com', 'codeberg.org'} | {h.strip().lower() for h in os.environ.get('HAIVEQUEEN_GITEA_HOSTS', '').split(',') if h.strip()}
    if host.lower() not in hosts:
        return None
    parts = path.removesuffix('.git').split('/')
    if len(parts) != 2 or any(not p or p in ('.', '..') for p in parts):
        return None
    return (origin, parts[0], parts[1])


def _request(repo, path, token=None, method='GET', data=None, content_type=None, raw=False):
    origin, owner, name = repo
    url = origin.rstrip('/') + '/api/v1/repos/' + quote(owner, safe='') + '/' + quote(name, safe='') + path
    headers = {'Accept': 'application/json'}
    if token is None:
        from ..config import git_credential
        credential = git_credential('gitea') or {}
        if origin.startswith('https://') and credential.get('origin') == origin.rstrip('/'):
            token = credential.get('token')
    if token:
        headers['Authorization'] = 'token ' + token
    if content_type:
        headers['Content-Type'] = content_type
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=30) as response:
        body = response.read()
    return body if raw else json.loads(body) if body else None


def _post(repo, path, payload, token):
    return _request(repo, path, token, 'POST', json.dumps(payload).encode('utf-8'), 'application/json')


def pull_request(repo, title, head, base, body='', token=None):
    return _post(repo, '/pulls', {'title': title, 'head': head, 'base': base, 'body': body}, token)


def tag(repo, name, target, message='', token=None):
    return _post(repo, '/tags', {'tag_name': name, 'target': target, 'message': message}, token)


def release(repo, tag_name, name='', body='', target_commitish='', draft=False, prerelease=False, token=None):
    payload = {'tag_name': tag_name, 'name': name, 'body': body, 'draft': draft, 'prerelease': prerelease}
    if target_commitish:
        payload['target_commitish'] = target_commitish
    return _post(repo, '/releases', payload, token)


def asset_upload(repo, release_id, file_path, token=None):
    path = Path(file_path)
    boundary = 'haivequeen-' + uuid.uuid4().hex
    filename = path.name.replace('\\', '_').replace('"', '_').replace('\r', '_').replace('\n', '_')
    data = (('--' + boundary + '\r\nContent-Disposition: form-data; name="attachment"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('utf-8') + path.read_bytes() + ('\r\n--' + boundary + '--\r\n').encode('ascii'))
    return _request(repo, '/releases/' + quote(str(release_id), safe='') + '/assets?' + urlencode({'name': path.name}), token, 'POST', data, 'multipart/form-data; boundary=' + boundary)


def release_list(repo, token=None):
    releases = []
    page = 1
    while True:
        batch = _request(repo, '/releases?' + urlencode({'page': page, 'limit': 50}), token)
        if not isinstance(batch, list):
            raise ValueError('Invalid releases response')
        releases.extend(batch)
        if len(batch) < 50:
            return releases
        page += 1


def pipeline(repo, ref, token=None):
    return _request(repo, '/commits/' + quote(ref, safe='') + '/status', token)


def login(repo, token):
    if not token:
        raise ValueError('A personal access token is required')
    origin = repo[0].rstrip('/')
    request = Request(origin + '/api/v1/user', headers={'Accept': 'application/json', 'Authorization': 'token ' + token})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())

def pr_status(repo, number, token=None):
    return _request(repo, '/pulls/' + quote(str(number), safe=''), token)


CAPABILITIES |= {'release_tag', 'release_state'}
CAPABILITIES |= {'release_prepare', 'release_upload', 'release_asset_digest'}


def release_prepare(repo, name, commit, token=None):
    from urllib.error import HTTPError
    release_tag(repo, name, commit, token)
    try:
        result = _request(repo, '/releases/tags/' + quote(name, safe=''), token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        result = release(repo, name, name=name, target_commitish=commit, prerelease='-' in name, token=token)
    if result.get('tag_name') != name or result.get('draft') or bool(result.get('prerelease')) != ('-' in name):
        raise ValueError('existing release has the wrong publication status')
    assets = []
    page = 1
    while True:
        batch = _request(repo, '/releases/%s/assets?limit=100&page=%s' % (result['id'], page), token)
        assets.extend(batch)
        if len(batch) < 100:
            return dict(result, assets=assets)
        page += 1


def release_upload(repo, release, name, content, token=None):
    if any(ord(c) < 32 or c in '"\\' for c in name):
        raise ValueError('invalid upload filename')
    boundary = 'haivequeen-' + uuid.uuid4().hex
    data = (('--' + boundary + '\r\nContent-Disposition: form-data; name="attachment"; filename="' + name +
             '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + content +
            ('\r\n--' + boundary + '--\r\n').encode())
    return _request(repo, '/releases/%s/assets?' % release['id'] + urlencode({'name': name}), token,
                    'POST', data, 'multipart/form-data; boundary=' + boundary)


def release_asset_digest(repo, release, asset, token=None):
    import hashlib
    return hashlib.sha256(release_download(repo, release, asset, token)).hexdigest()


CAPABILITIES |= {'release_history', 'release_download', 'release_snapshot'}


def release_snapshot(repo, name, commit, token=None):
    tag = _request(repo, '/tags/' + quote(name, safe=''), token)
    if tag['commit']['sha'] != commit:
        raise ValueError('pre-release tag points at a different commit')
    result = _request(repo, '/releases/tags/' + quote(name, safe=''), token)
    if result.get('tag_name') != name or result.get('draft') or not result.get('prerelease'):
        raise ValueError('selected release is no longer a pre-release')
    assets, page = [], 1
    while True:
        batch = _request(repo, '/releases/%s/assets?limit=100&page=%s' % (result['id'], page), token)
        assets.extend(batch)
        if len(batch) < 100:
            return dict(result, assets=assets)
        page += 1


def release_history(repo, token=None):
    from ._release import metadata
    rows = []
    for row in release_list(repo, token):
        if row.get('draft'):
            continue
        tag = _request(repo, '/tags/' + quote(row['tag_name'], safe=''), token)
        rows.append(metadata(row['tag_name'], row.get('published_at') or row.get('created_at', ''),
                             tag['commit']['sha'], row.get('prerelease', False)))
    return rows


def release_download(repo, release, asset, token=None):
    # Download through this configured origin only; never send credentials to a
    # URL supplied by release metadata on another host.
    url = asset.get('browser_download_url', '')
    origin = repo[0].rstrip('/')
    if not url.startswith(origin + '/'):
        raise ValueError('release asset download is outside the provider origin')
    if token is None:
        from ..config import git_credential
        credential = git_credential('gitea') or {}
        if credential.get('origin') == origin:
            token = credential.get('token')
    headers = {'Authorization': 'token ' + token} if token else {}
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return response.read()


def release_tag(repo, name, commit, token=None):
    from urllib.error import HTTPError
    try:
        existing = _request(repo, '/tags/' + quote(name, safe=''), token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        tag(repo, name, commit, token=token)
        return
    if existing.get('commit', {}).get('sha') != commit:
        raise ValueError('release tag points at a different commit')


def release_state(repo, name, commit, token=None):
    from ._release import workflows
    rows = []
    page = 1
    while True:
        batch = _request(repo, '/actions/runs?' + urlencode({'limit': 100, 'page': page}), token)['workflow_runs']
        rows.extend(r for r in batch if r.get('head_sha') == commit and
                    r.get('head_branch') in (name, 'refs/tags/' + name) and r.get('event') == 'push')
        if len(batch) < 100:
            return workflows(rows)
        page += 1
