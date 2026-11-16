"""GitLab.com REST provider. Operations return GitLab's decoded JSON objects."""
import os
import json
import mimetypes
from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'asset', 'release_list', 'pipeline', 'login_methods'}) | {'pr_status'}
LOGIN_OPTIONS = {'device': True, 'client_id_env': 'HAIVEQUEEN_GITLAB_CLIENT_ID', 'token_env': ('GITLAB_TOKEN',),
                 'registration_url': 'https://gitlab.com/-/user_settings/applications',
                 'registration_help': 'Register a public OAuth application with the api scope and device authorization enabled. Enter its application ID. An access token is also sufficient.'}


def detect(remote):
    """Recognize GitLab.com HTTPS, SSH and SCP-style remotes without network access."""
    if not isinstance(remote, str):
        return None
    match = re.fullmatch(r'git@gitlab\.com:(.+)', remote, re.I)
    if match:
        path = match.group(1)
    else:
        try:
            url = urlsplit(remote)
            if (url.scheme.lower() not in ('https', 'ssh') or url.hostname != 'gitlab.com'
                    or url.query or url.fragment or url.password or url.port is not None):
                return None
            if url.username and (url.scheme.lower() != 'ssh' or url.username != 'git'):
                return None
            path = url.path.lstrip('/')
        except ValueError:
            return None
    if path.endswith('.git'):
        path = path[:-4]
    parts = path.split('/')
    if len(parts) < 2 or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', p) or p in ('.', '..') for p in parts):
        return None
    return '/'.join(parts)


def _request(repo, method, path, token=None, data=None, query=None, multipart=None, raw=False):
    if detect('https://gitlab.com/' + str(repo)) != repo:
        raise ValueError('invalid GitLab repository')
    url = 'https://gitlab.com/api/v4/projects/' + quote(repo, safe='') + path
    if query:
        url += '?' + urlencode(query)
    headers = {'Accept': 'application/json'}
    auth_method = None
    if token is None:
        from ..config import git_credential
        credential = git_credential('gitlab') or {}
        token = credential.get('token')
        auth_method = credential.get('method')
    if token:
        if auth_method == 'device':
            headers['Authorization'] = 'Bearer ' + token
        else:
            headers['PRIVATE-TOKEN'] = token
    body = None
    if multipart is not None:
        filename, content = multipart
        if any(ord(char) < 32 or ord(char) == 127 for char in filename):
            raise ValueError('invalid upload filename')
        boundary = uuid4().hex
        filename = filename.replace('\\', '\\\\').replace('"', '\\"')
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: {content_type}\r\n\r\n').encode('utf-8') + content + f'\r\n--{boundary}--\r\n'.encode('ascii')
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    elif isinstance(data, bytes):
        body = data
        headers['Content-Type'] = 'application/octet-stream'
    elif data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=body, headers=headers, method=method), timeout=30) as response:
        return (response.read() if raw else json.load(response)), response.headers.get('X-Next-Page', '')


def pull_request(repo, source_branch, target_branch, title, token=None, description=''):
    return _request(repo, 'POST', '/merge_requests', token, {'source_branch': source_branch, 'target_branch': target_branch, 'title': title, 'description': description})[0]


def tag(repo, name, ref, token=None):
    return _request(repo, 'POST', '/repository/tags', token, {'tag_name': name, 'ref': ref})[0]


def release(repo, tag_name, name, description, token=None, ref=None):
    data = {'tag_name': tag_name, 'name': name, 'description': description}
    if ref is not None:
        data['ref'] = ref
    return _request(repo, 'POST', '/releases', token, data)[0]


def asset(repo, tag_name, name, url, token=None):
    if not isinstance(url, str) or urlsplit(url).scheme.lower() not in ('http', 'https', 'ftp'):
        path = Path(url)
        upload, _ = _request(repo, 'POST', '/uploads', token, multipart=(path.name, path.read_bytes()))
        relative_url = upload['url']
        if not isinstance(relative_url, str) or not relative_url.startswith('/uploads/'):
            raise ValueError('invalid GitLab upload URL')
        url = 'https://gitlab.com/' + quote(repo, safe='/') + quote(relative_url, safe='/%')
    return _request(repo, 'POST', '/releases/' + quote(tag_name, safe='') + '/assets/links', token, {'name': name, 'url': url})[0]


def release_list(repo, token=None):
    releases = []
    page = '1'
    seen = set()
    while page:
        if not page.isdigit() or page in seen:
            raise ValueError('invalid GitLab pagination')
        seen.add(page)
        items, page = _request(repo, 'GET', '/releases', token, query={'per_page': 100, 'page': page})
        if not isinstance(items, list):
            raise ValueError('invalid GitLab release list')
        releases.extend(items)
    return releases


def pipeline(repo, ref, token=None):
    items, _ = _request(repo, 'GET', '/pipelines', token, query={'ref': ref, 'per_page': 1, 'order_by': 'id', 'sort': 'desc'})
    return items[0] if items else None


def login_methods(repo):
    return ('personal_access_token', 'device')

def pr_status(repo, number, token=None):
    return _request(repo, 'GET', '/merge_requests/' + quote(str(number), safe=''), token)[0]


CAPABILITIES |= {'release_tag', 'release_state'}
CAPABILITIES |= {'release_prepare', 'release_upload', 'release_asset_digest'}


def release_prepare(repo, name, commit, token=None):
    from urllib.error import HTTPError
    release_tag(repo, name, commit, token)
    try:
        result = _request(repo, 'GET', '/releases/' + quote(name, safe=''), token)[0]
    except HTTPError as exc:
        if exc.code != 404:
            raise
        # GitLab represents prereleases by their semantic-version tag and name;
        # unlike GitHub/Gitea its release API has no prerelease boolean.
        label = ('Pre-release ' if '-' in name else 'Release ') + name
        result = release(repo, name, label, label, token, ref=commit)
    if result.get('tag_name') != name or result.get('commit', {}).get('id') != commit:
        raise ValueError('existing release has the wrong tag or commit')
    links = []
    page = '1'
    while page:
        batch, page = _request(repo, 'GET', '/releases/' + quote(name, safe='') + '/assets/links',
                               token, query={'per_page': 100, 'page': page})
        links.extend(batch)
    return dict(result, id=name, assets=links)


def _package_path(release, name):
    return '/packages/generic/haivequeen-release/' + quote(release['id'], safe='') + '/' + quote(name, safe='')


def release_upload(repo, release, name, content, token=None):
    from urllib.error import HTTPError
    path = _package_path(release, name)
    try:
        existing = _request(repo, 'GET', path, token, raw=True)[0]
    except HTTPError as exc:
        if exc.code != 404:
            raise
        _request(repo, 'PUT', path, token, data=content)
    else:
        if existing != content:
            raise ValueError('existing release package has different content')
    url = 'https://gitlab.com/api/v4/projects/' + quote(repo, safe='') + path
    return asset(repo, release['id'], name, url, token)


def release_asset_digest(repo, release, asset, token=None):
    import hashlib
    path = _package_path(release, asset['name'])
    expected = 'https://gitlab.com/api/v4/projects/' + quote(repo, safe='') + path
    if asset.get('url') != expected:
        raise ValueError('existing release asset has an unexpected download URL')
    return hashlib.sha256(_request(repo, 'GET', path, token, raw=True)[0]).hexdigest()


CAPABILITIES |= {'release_history', 'release_download', 'release_snapshot'}


def release_snapshot(repo, name, commit, token=None):
    result = _request(repo, 'GET', '/releases/' + quote(name, safe=''), token)[0]
    if result.get('tag_name') != name or result.get('commit', {}).get('id') != commit or '-' not in name:
        raise ValueError('selected pre-release has a different tag or commit')
    assets, page = [], '1'
    while page:
        batch, page = _request(repo, 'GET', '/releases/' + quote(name, safe='') + '/assets/links', token,
                               query={'per_page': 100, 'page': page})
        assets.extend(batch)
    return dict(result, id=name, assets=assets)


def release_history(repo, token=None):
    from ._release import metadata
    return [metadata(row['tag_name'], row.get('released_at') or row.get('created_at', ''), row['commit']['id'])
            for row in release_list(repo, token)]


def release_download(repo, release, asset, token=None):
    # Only this repository's package endpoint receives its credential.
    path = _package_path(release, asset['name'])
    expected = 'https://gitlab.com/api/v4/projects/' + quote(repo, safe='') + path
    if asset.get('url') != expected:
        raise ValueError('release asset has an unexpected download URL')
    return _request(repo, 'GET', path, token, raw=True)[0]


def release_tag(repo, name, commit, token=None):
    from urllib.error import HTTPError
    try:
        existing = _request(repo, 'GET', '/repository/tags/' + quote(name, safe=''), token)[0]
    except HTTPError as exc:
        if exc.code != 404:
            raise
        tag(repo, name, commit, token)
        return
    if existing.get('commit', {}).get('id') != commit:
        raise ValueError('release tag points at a different commit')


def release_state(repo, name, commit, token=None):
    rows = _request(repo, 'GET', '/pipelines', token, query={
        'ref': name, 'sha': commit, 'source': 'push', 'per_page': 1, 'order_by': 'id', 'sort': 'desc'})[0]
    rows = [r for r in rows if r.get('sha') == commit and r.get('ref') == name and r.get('source') == 'push']
    value = rows[0].get('status') if rows else None
    return 'success' if value == 'success' else 'failure' if value in ('failed', 'canceled') else 'pending'


# Normalized operations own provider-specific integration behavior.
from . import _integration as _normalized
CAPABILITIES |= {'open_pr', 'find_pr', 'pr_state', 'pr_commit', 'pipeline_state', 'credential'}

def pr_state(repo, identifier, token=None):
    return _normalized.state(pr_status(repo, identifier, token))

def pr_commit(repo, identifier, token=None):
    return _normalized.merge_commit(pr_status(repo, identifier, token))

def credential(repo):
    return os.environ.get("GITLAB_TOKEN")

def open_pr(repo, title, source, target, token=None):
    return pull_request(repo, source, target, title, token)["iid"]

def find_pr(repo, source, target, token=None, commit=None):
    rows = _request(repo, "GET", "/merge_requests", token, query={"state": "all" if commit else "opened", "source_branch": source, "target_branch": target})[0]
    return next((r["iid"] for r in rows if r.get("source_branch") == source and r.get("target_branch") == target and r.get("source_project_id") == r.get("target_project_id") and _normalized.reusable(r, r.get("sha"), commit)), None)

def pipeline_state(repo, commit, token=None):
    rows = _request(repo, "GET", "/pipelines", token, query={"sha": commit, "per_page": 1, "order_by": "id", "sort": "desc"})[0]
    result = rows[0] if rows else None
    values = [str(result.get("state", result.get("status", ""))).lower()] if result and result.get("sha") == commit else []
    return _normalized.pipeline(values)
