"""Noninteractive device authorization for a future web profile interface.

Register a public OAuth application with each provider and pass its client ID to
start_device_login. Sessions are process-local; responses never expose device
codes or access tokens. Call poll_device_login after the indicated interval.
"""
import json
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config

_PROVIDERS = {
    'github': ('https://github.com/login/device/code',
               'https://github.com/login/oauth/access_token', 'repo workflow', 'device'),
    'gitlab': ('https://gitlab.com/oauth/authorize_device',
               'https://gitlab.com/oauth/token', 'api', 'device'),
    'azure': ('https://login.microsoftonline.com/organizations/oauth2/v2.0/devicecode',
              'https://login.microsoftonline.com/organizations/oauth2/v2.0/token',
              '499b84ac-1321-427f-aa17-267ca6975798/.default offline_access', 'bearer'),
}
_sessions = {}
_lock = threading.RLock()
_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'


def _post(url, values):
    request = Request(url, data=urlencode(values).encode('ascii'),
                      headers={'Accept': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
    except HTTPError as error:
        try:
            data = error.read()
        finally:
            error.close()
    except (URLError, OSError) as error:
        raise ValueError('device authorization service unavailable') from error
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError) as error:
        raise ValueError('invalid device authorization response') from error


def _public(session):
    result = {'status': session['status'], 'provider': session['provider']}
    for key in ('user_code', 'verification_uri', 'verification_uri_complete', 'interval', 'error'):
        if key in session:
            result[key] = session[key]
    return result


def start_device_login(provider, client_id):
    """Start a device flow and return a session ID and browser instructions."""
    if provider not in _PROVIDERS:
        raise ValueError('device authorization is not supported for this provider')
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError('a public OAuth client ID is required')
    start_url, _, scope, _ = _PROVIDERS[provider]
    response = _post(start_url, {'client_id': client_id, 'scope': scope})
    try:
        device_code = response['device_code']
        user_code = response['user_code']
        uri = response.get('verification_uri') or response['verification_url']
        expires = int(response['expires_in'])
        interval = int(response.get('interval', 5))
        if not all(isinstance(x, str) and x for x in (device_code, user_code, uri)) or expires <= 0 or interval <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('device authorization could not be started') from error
    now = time.monotonic()
    session = {'provider': provider, 'client_id': client_id, 'device_code': device_code,
               'user_code': user_code, 'verification_uri': uri, 'status': 'pending',
               'interval': interval, 'expires': now + expires, 'next_poll': now + interval}
    if response.get('verification_uri_complete'):
        session['verification_uri_complete'] = response['verification_uri_complete']
    with _lock:
        session_id = secrets.token_urlsafe(32)
        _sessions[session_id] = session
    return {'session_id': session_id, **_public(session)}


def device_login_status(session_id):
    """Return the current browser-safe status without contacting the provider."""
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            raise ValueError('unknown device authorization session')
        _expire(session)
        return _public(session)


def _expire(session):
    if session['status'] == 'pending' and time.monotonic() >= session['expires']:
        session.update(status='failed', error='expired_token')
        session.pop('device_code', None)


def poll_device_login(session_id):
    """Poll once when due; never block waiting for the user or reveal tokens."""
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            raise ValueError('unknown device authorization session')
        _expire(session)
        if session['status'] != 'pending' or time.monotonic() < session['next_poll']:
            return _public(session)
        _, token_url, _, method = _PROVIDERS[session['provider']]
        session['next_poll'] = time.monotonic() + session['interval']
        response = _post(token_url, {'client_id': session['client_id'],
                                     'device_code': session['device_code'], 'grant_type': _GRANT})
        token = response.get('access_token')
        if isinstance(token, str) and token:
            oauth = _oauth_metadata(response, session['client_id'])
            if oauth:
                config.save_git_credential(session['provider'], token, method, oauth=oauth)
            else:
                config.save_git_credential(session['provider'], token, method)
            session['status'] = 'success'
            session.pop('device_code', None)
        else:
            error = response.get('error', 'invalid_response')
            if error == 'slow_down':
                session['interval'] += 5
                session['next_poll'] = time.monotonic() + session['interval']
            elif error != 'authorization_pending':
                session.update(status='failed', error=str(error))
                session.pop('device_code', None)
        return _public(session)


def _oauth_metadata(response, client_id, previous_refresh=None):
    """Validate optional expiry and refresh data before persisting it."""
    refresh = response.get('refresh_token', previous_refresh)
    if refresh is not None and (not isinstance(refresh, str) or not refresh or any(c.isspace() for c in refresh)):
        raise ValueError('invalid OAuth response')
    expires = response.get('expires_in')
    if expires is None:
        return {}
    try:
        seconds = int(expires)
        if isinstance(expires, bool) or seconds <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('invalid OAuth response') from error
    result = {'client_id': client_id, 'expires_at': time.time() + seconds}
    if refresh is not None:
        result['refresh_token'] = refresh
    return result


def refresh_credential(provider, credential):
    """Refresh an expired profile credential, serializing rotation across processes."""
    if time.time() < credential['expires_at']:
        return credential
    from . import filetxn
    import json
    with filetxn.locked():
        contents = filetxn.read(config.config_dir() / 'git-credentials.json')
        current = config.credential_store.decode(contents).get(provider)
        if not current or 'expires_at' not in current or time.time() < current['expires_at']:
            return current
        refresh = current.get('refresh_token')
        client_id = current.get('client_id')
        if provider not in _PROVIDERS or not refresh or not client_id:
            raise ValueError('expired OAuth credential requires sign-in')
        response = _post(_PROVIDERS[provider][1], {'client_id': client_id,
                         'grant_type': 'refresh_token', 'refresh_token': refresh})
        token = response.get('access_token')
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise ValueError('OAuth credential refresh failed')
        oauth = _oauth_metadata(response, client_id, refresh)
        if not oauth:
            raise ValueError('invalid OAuth refresh response')
        config.save_git_credential(provider, token, current['method'], origin=current.get('origin'), oauth=oauth)
        return {**current, 'token': token, **oauth}
