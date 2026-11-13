"""Gitea and Forgejo REST adapter. See GITEA.md for the interface."""
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'asset_upload', 'release_list', 'pipeline', 'login'})


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


def _request(repo, path, token=None, method='GET', data=None, content_type=None):
    origin, owner, name = repo
    url = origin.rstrip('/') + '/api/v1/repos/' + quote(owner, safe='') + '/' + quote(name, safe='') + path
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'token ' + token
    if content_type:
        headers['Content-Type'] = content_type
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


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
