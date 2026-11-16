"""Bitbucket Cloud adapter. See bitbucket.md for the public interface."""
import json
import os
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

CAPABILITIES = {'pull_request', 'tag', 'asset_upload', 'asset_list', 'pipeline', 'login'} | {'pr_status', 'pipeline_for_commit'}
LOGIN_OPTIONS = {'device': False, 'token_env': ('BITBUCKET_TOKEN',)}
_API = 'https://api.bitbucket.org/2.0/'
_PART = re.compile(r'^[A-Za-z0-9_.-]+$')


def detect(remote):
    if not isinstance(remote, str):
        return None
    match = re.fullmatch(r'(?:git@bitbucket\.org:|ssh://git@bitbucket\.org/|https://(?:[^/@]+@)?bitbucket\.org/)([^/]+)/([^/]+?)(?:\.git)?/?', remote, re.I)
    if not match or not all(_PART.fullmatch(p) and p not in ('.', '..') for p in match.groups()):
        return None
    return '/'.join(match.groups())


def _repo(repo):
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or any(p in ('.', '..') for p in repo.split('/')):
        raise ValueError('invalid Bitbucket repository')
    return 'repositories/' + repo


def _request(path, method='GET', payload=None, content_type='application/json'):
    from ..config import git_credential
    token = os.environ.get('BITBUCKET_TOKEN') or (git_credential('bitbucket') or {}).get('token')
    if not token:
        raise ValueError('BITBUCKET_TOKEN is required')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode() if content_type == 'application/json' else payload
        headers['Content-Type'] = content_type
    request = Request(_API + path, data=data, headers=headers, method=method)
    with urlopen(request) as response:
        body = response.read()
    return json.loads(body) if body else None


def _list(path):
    values = []
    while path:
        page = _request(path)
        values.extend(page['values'])
        next_url = page.get('next')
        if next_url and not next_url.startswith(_API):
            raise ValueError('invalid pagination URL')
        path = next_url[len(_API):] if next_url else None
    return values


def login(repo):
    return _request(_repo(repo))


def pull_request(repo, title, source, destination, description=''):
    return _request(_repo(repo) + '/pullrequests', 'POST', {'title': title, 'description': description, 'source': {'branch': {'name': source}}, 'destination': {'branch': {'name': destination}}})


def tag(repo, name, commit, message=None):
    payload = {'name': name, 'target': {'hash': commit}}
    if message is not None:
        payload['message'] = message
    return _request(_repo(repo) + '/refs/tags', 'POST', payload)


def asset_list(repo):
    return _list(_repo(repo) + '/downloads')


def asset_upload(repo, file_path):
    from pathlib import Path
    import uuid
    path = Path(file_path)
    name = path.name
    if not name or any(c in name for c in '\r\n"'):
        raise ValueError('invalid asset filename')
    boundary = uuid.uuid4().hex
    data = (('--' + boundary + '\r\nContent-Disposition: form-data; name="files"; filename="' + name + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + path.read_bytes() + ('\r\n--' + boundary + '--\r\n').encode())
    return _request(_repo(repo) + '/downloads', 'POST', data, 'multipart/form-data; boundary=' + boundary)


def pipeline(repo, pipeline_id):
    return _request(_repo(repo) + '/pipelines/' + quote(str(pipeline_id), safe=''))

def pr_status(repo, number):
    return _request(_repo(repo) + '/pullrequests/' + quote(str(number), safe=''))

def pipeline_for_commit(repo, commit):
    return _list(_repo(repo) + '/commit/' + quote(commit, safe='') + '/statuses/build')


# Normalized operations own provider-specific integration behavior.
from . import _integration as _normalized
CAPABILITIES |= {'open_pr', 'find_pr', 'pr_state', 'pr_commit', 'pipeline_state', 'credential'}

def pr_state(repo, identifier, token=None):
    return _normalized.state(pr_status(repo, identifier))

def pr_commit(repo, identifier, token=None):
    return _normalized.merge_commit(pr_status(repo, identifier))

def credential(repo):
    return os.environ.get("BITBUCKET_TOKEN")

def open_pr(repo, title, source, target, token=None):
    return pull_request(repo, title, source, target)["id"]

def find_pr(repo, source, target, token=None, commit=None):
    rows = _list(_repo(repo) + "/pullrequests?state=OPEN" + ("&state=MERGED" if commit else ""))
    return next((r["id"] for r in rows if r.get("source", {}).get("branch", {}).get("name") == source and r.get("destination", {}).get("branch", {}).get("name") == target and r.get("source", {}).get("repository", {}).get("uuid") == r.get("destination", {}).get("repository", {}).get("uuid") and _normalized.reusable(r, r.get("source", {}).get("commit", {}).get("hash"), commit)), None)

def pipeline_state(repo, commit, token=None):
    values = [str(item.get("state", "")).lower() for item in pipeline_for_commit(repo, commit)]
    return _normalized.pipeline(values)
