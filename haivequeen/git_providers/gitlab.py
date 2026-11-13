"""GitLab.com REST provider. Operations return GitLab's decoded JSON objects."""
import json
import mimetypes
from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'asset', 'release_list', 'pipeline', 'login_methods'})


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


def _request(repo, method, path, token=None, data=None, query=None, multipart=None):
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
    elif data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=body, headers=headers, method=method), timeout=30) as response:
        return json.load(response), response.headers.get('X-Next-Page', '')


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
    return ('personal_access_token',)
