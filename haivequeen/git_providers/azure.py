"""Azure DevOps Services REST adapter; see azure.md for the public interface."""
import os
import base64
import json
import re
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'release_assets', 'release_list', 'pipeline', 'login_methods'}) | {'pr_status', 'pipeline_for_commit'}
LOGIN_OPTIONS = {'device': True, 'client_id_env': 'HAIVEQUEEN_AZURE_CLIENT_ID', 'token_method': 'pat', 'token_env': ('AZURE_DEVOPS_TOKEN',),
                 'registration_url': 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
                 'registration_help': 'Register a public client application in Microsoft Entra with Azure DevOps delegated access and public client flows enabled. Enter its application client ID. A personal access token is also sufficient.'}


def detect(remote):
    if not isinstance(remote, str):
        return None
    # Azure's SSH form is scp-like, while HTTPS can contain a username.
    match = re.fullmatch(r'git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+?)/?', remote, re.I)
    if match:
        parts = match.groups()
    else:
        url = urlsplit(remote)
        try:
            port = url.port
        except ValueError:
            return None
        if url.scheme.lower() not in ('https', 'ssh') or port not in (None, 443 if url.scheme.lower() == 'https' else 22):
            return None
        host = (url.hostname or '').lower()
        path = url.path.strip('/').split('/')
        if host == 'dev.azure.com' and len(path) == 4 and path[2].lower() == '_git':
            parts = (path[0], path[1], path[3])
        elif host.endswith('.visualstudio.com') and len(path) == 3 and path[1].lower() == '_git':
            parts = (host[:-len('.visualstudio.com')], path[0], path[2])
        elif host == 'ssh.dev.azure.com' and url.scheme.lower() == 'ssh' and len(path) == 4 and path[0].lower() == 'v3':
            parts = tuple(path[1:])
        else:
            return None
    parts = tuple(unquote(x) for x in parts)
    if any(not x or '/' in x or '\\' in x or x in ('.', '..') for x in parts):
        return None
    return parts


def login_methods(repo):
    """Credentials are supplied by the caller, never read during discovery."""
    return ('pat', 'bearer', 'device')


def _request(repo, path, token, auth='pat', method='GET', body=None, query=None, release_service=False, include_continuation=False):
    if token is None:
        from ..config import git_credential
        credential = git_credential('azure') or {}
        token = credential.get('token')
        auth = credential.get('method', auth)
    if auth == 'token':  # Credentials saved by the first web token form are PATs.
        auth = 'pat'
    if auth not in ('pat', 'bearer') or not isinstance(token, str) or not token:
        raise ValueError('a token and supported auth method are required')
    org, project, repository = repo
    host = 'vsrm.dev.azure.com' if release_service else 'dev.azure.com'
    base = 'https://%s/%s/%s/_apis/' % (host, quote(org, safe=''), quote(project, safe=''))
    params = dict(query or {})
    params['api-version'] = '7.1'
    url = base + path.format(repo=quote(repository, safe='')) + '?' + urlencode(params)
    credential = ('Basic ' + base64.b64encode((':' + token).encode()).decode()) if auth == 'pat' else 'Bearer ' + token
    headers = {'Authorization': credential, 'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    with urlopen(Request(url, data=data, headers=headers, method=method)) as response:
        result = json.load(response)
        if include_continuation:
            return result, response.headers.get('x-ms-continuationtoken')
        return result


def pull_request(repo, source, target, title, token=None, auth='pat', description=''):
    if not all(isinstance(v, str) and v for v in (source, target, title)):
        raise ValueError('source, target and title are required')
    def branch(value):
        return value if value.startswith('refs/heads/') else 'refs/heads/' + value
    return _request(repo, 'git/repositories/{repo}/pullrequests', token, auth, 'POST',
                    {'sourceRefName': branch(source), 'targetRefName': branch(target), 'title': title, 'description': description})


def tag(repo, name, commit, token=None, auth='pat'):
    if not isinstance(name, str) or not name or not re.fullmatch(r'[0-9a-fA-F]{40}', commit or ''):
        raise ValueError('tag name and 40-character commit ID are required')
    result = _request(repo, 'git/repositories/{repo}/refs', token, auth, 'POST',
                      [{'name': 'refs/tags/' + name, 'oldObjectId': '0' * 40, 'newObjectId': commit}])
    if not result.get('value') or any(not item.get('success') for item in result['value']):
        raise ValueError('Azure rejected the tag update: %s' % result)
    return result


def release(repo, definition_id, token=None, auth='pat', description='', artifacts=None):
    if not isinstance(definition_id, int) or isinstance(definition_id, bool) or definition_id <= 0:
        raise ValueError('a positive release definition ID is required')
    body = {'definitionId': definition_id, 'description': description}
    if artifacts is not None:
        body['artifacts'] = artifacts
    return _request(repo, 'release/releases', token, auth, 'POST', body, release_service=True)


def release_list(repo, token=None, auth='pat', definition_id=None, continuation_token=None):
    query = {}
    if definition_id is not None:
        query['definitionId'] = definition_id
    if continuation_token is not None:
        query['continuationToken'] = continuation_token
    return _request(repo, 'release/releases', token, auth, query=query, release_service=True, include_continuation=True)


def release_assets(repo, release_id, token=None, auth='pat'):
    if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id <= 0:
        raise ValueError('a positive release ID is required')
    result = _request(repo, 'release/releases/%d' % release_id, token, auth, release_service=True)
    return result.get('artifacts', [])


def pipeline(repo, build_id, token=None, auth='pat'):
    if not isinstance(build_id, int) or isinstance(build_id, bool) or build_id <= 0:
        raise ValueError('a positive build ID is required')
    return _request(repo, 'build/builds/%d' % build_id, token, auth)

def pr_status(repo, number, token, auth='pat'):
    return _request(repo, 'git/repositories/{repo}/pullrequests/' + quote(str(number), safe=''), token, auth)

def pipeline_for_commit(repo, commit, token, auth='pat'):
    return _request(repo, 'build/builds', token, auth, query={'repositoryId': repo[2], 'repositoryType': 'TfsGit', 'queryOrder': 'finishTimeDescending', '$top': 100}).get('value', [])


CAPABILITIES |= {'release_tag', 'release_state', 'release_history'}


def release_history(repo, token=None):
    """Azure tag workflows identify versions by Git refs, not classic release IDs."""
    from ._release import metadata
    rows = []
    continuation = None
    while True:
        query = {'filter': 'tags/v', 'peelTags': 'true', '$top': 100}
        if continuation:
            query['continuationToken'] = continuation
        result, continuation = _request(repo, 'git/repositories/{repo}/refs', token,
                                        query=query, include_continuation=True)
        for ref in result.get('value', []):
            name = ref['name'].removeprefix('refs/tags/')
            if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.\d+)?', name):
                continue
            commit = ref.get('peeledObjectId') or ref['objectId']
            builds = _release_builds(repo, name, commit, token)
            date = max((r.get('finishTime') or r.get('queueTime', '') for r in builds), default='')
            rows.append(metadata(name, date, commit))
        if not continuation:
            return rows


def release_tag(repo, name, commit, token=None):
    rows = _request(repo, 'git/repositories/{repo}/refs', token,
                    query={'filter': 'tags/' + name, 'peelTags': 'true'}).get('value', [])
    existing = next((r for r in rows if r.get('name') == 'refs/tags/' + name), None)
    if existing:
        if (existing.get('peeledObjectId') or existing.get('objectId')) != commit:
            raise ValueError('release tag points at a different commit')
    else:
        tag(repo, name, commit, token)


def _release_builds(repo, name, commit, token):
    rows = []
    continuation = None
    while True:
        query = {'repositoryId': repo[2], 'repositoryType': 'TfsGit',
                 'branchName': 'refs/tags/' + name, 'queryOrder': 'queueTimeDescending', '$top': 100}
        if continuation:
            query['continuationToken'] = continuation
        result, continuation = _request(repo, 'build/builds', token, query=query, include_continuation=True)
        rows.extend(r for r in result.get('value', []) if r.get('sourceVersion') == commit and
                    r.get('sourceBranch') == 'refs/tags/' + name)
        if not continuation:
            return rows


def release_state(repo, name, commit, token=None):
    rows = _release_builds(repo, name, commit, token)
    latest = {}
    for row in rows:
        key = row['definition']['id']
        if key not in latest or row['id'] > latest[key]['id']:
            latest[key] = row
    if any(r.get('result') in ('failed', 'canceled', 'partiallySucceeded') for r in latest.values()):
        return 'failure'
    if latest and all(r.get('status') == 'completed' and r.get('result') == 'succeeded' for r in latest.values()):
        return 'success'
    return 'pending'


# Normalized operations own provider-specific integration behavior.
from . import _integration as _normalized
CAPABILITIES |= {'open_pr', 'find_pr', 'pr_state', 'pr_commit', 'pipeline_state', 'credential'}

def pr_state(repo, identifier, token=None):
    return _normalized.state(pr_status(repo, identifier, token))

def pr_commit(repo, identifier, token=None):
    return _normalized.merge_commit(pr_status(repo, identifier, token))

def credential(repo):
    return os.environ.get("AZURE_DEVOPS_TOKEN")

def open_pr(repo, title, source, target, token=None):
    return pull_request(repo, source, target, title, token)["pullRequestId"]

def find_pr(repo, source, target, token=None, commit=None):
    rows = _request(repo, "git/repositories/{repo}/pullrequests", token, query={"searchCriteria.status": "all" if commit else "active", "searchCriteria.sourceRefName": "refs/heads/" + source, "searchCriteria.targetRefName": "refs/heads/" + target}).get("value", [])
    return next((r["pullRequestId"] for r in rows if r.get("sourceRefName") == "refs/heads/" + source and r.get("targetRefName") == "refs/heads/" + target and _normalized.reusable(r, r.get("lastMergeSourceCommit", {}).get("commitId"), commit)), None)

def pipeline_state(repo, commit, token=None):
    values = [str(item.get("result") or item.get("status") or "").lower() for item in pipeline_for_commit(repo, commit, token) if item.get("sourceVersion") == commit]
    return _normalized.pipeline(values)
