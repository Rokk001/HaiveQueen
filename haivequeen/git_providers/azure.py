"""Azure DevOps Services REST adapter; see azure.md for the public interface."""
import base64
import json
import re
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

CAPABILITIES = frozenset({'pull_request', 'tag', 'release', 'release_assets', 'release_list', 'pipeline', 'login_methods'}) | {'pr_status', 'pipeline_for_commit'}


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
    return ('pat', 'bearer')


def _request(repo, path, token, auth='pat', method='GET', body=None, query=None, release_service=False, include_continuation=False):
    if auth not in login_methods(repo) or not isinstance(token, str) or not token:
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


def pull_request(repo, source, target, title, token, auth='pat', description=''):
    if not all(isinstance(v, str) and v for v in (source, target, title)):
        raise ValueError('source, target and title are required')
    def branch(value):
        return value if value.startswith('refs/heads/') else 'refs/heads/' + value
    return _request(repo, 'git/repositories/{repo}/pullrequests', token, auth, 'POST',
                    {'sourceRefName': branch(source), 'targetRefName': branch(target), 'title': title, 'description': description})


def tag(repo, name, commit, token, auth='pat'):
    if not isinstance(name, str) or not name or not re.fullmatch(r'[0-9a-fA-F]{40}', commit or ''):
        raise ValueError('tag name and 40-character commit ID are required')
    result = _request(repo, 'git/repositories/{repo}/refs', token, auth, 'POST',
                      [{'name': 'refs/tags/' + name, 'oldObjectId': '0' * 40, 'newObjectId': commit}])
    if not result.get('value') or any(not item.get('success') for item in result['value']):
        raise ValueError('Azure rejected the tag update: %s' % result)
    return result


def release(repo, definition_id, token, auth='pat', description='', artifacts=None):
    if not isinstance(definition_id, int) or isinstance(definition_id, bool) or definition_id <= 0:
        raise ValueError('a positive release definition ID is required')
    body = {'definitionId': definition_id, 'description': description}
    if artifacts is not None:
        body['artifacts'] = artifacts
    return _request(repo, 'release/releases', token, auth, 'POST', body, release_service=True)


def release_list(repo, token, auth='pat', definition_id=None, continuation_token=None):
    query = {}
    if definition_id is not None:
        query['definitionId'] = definition_id
    if continuation_token is not None:
        query['continuationToken'] = continuation_token
    return _request(repo, 'release/releases', token, auth, query=query, release_service=True, include_continuation=True)


def release_assets(repo, release_id, token, auth='pat'):
    if not isinstance(release_id, int) or isinstance(release_id, bool) or release_id <= 0:
        raise ValueError('a positive release ID is required')
    result = _request(repo, 'release/releases/%d' % release_id, token, auth, release_service=True)
    return result.get('artifacts', [])


def pipeline(repo, build_id, token, auth='pat'):
    if not isinstance(build_id, int) or isinstance(build_id, bool) or build_id <= 0:
        raise ValueError('a positive build ID is required')
    return _request(repo, 'build/builds/%d' % build_id, token, auth)

def pr_status(repo, number, token, auth='pat'):
    return _request(repo, 'git/repositories/{repo}/pullrequests/' + quote(str(number), safe=''), token, auth)

def pipeline_for_commit(repo, commit, token, auth='pat'):
    return _request(repo, 'build/builds', token, auth, query={'repositoryId': repo[2], 'repositoryType': 'TfsGit', 'queryOrder': 'finishTimeDescending', '$top': 100}).get('value', [])
