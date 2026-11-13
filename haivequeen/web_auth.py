"""Web credentials and listener settings stored in the machine configuration."""
import hashlib
import hmac
import secrets

from . import filetxn, gui_config

_DEFAULTS = {'address': '0.0.0.0', 'port': 8484, 'certificate': '', 'key': '',
             'network': False, 'username': '', 'password': ''}
_ITERATIONS = 600_000


@filetxn.coordinated
def read(path):
    data = gui_config.load_raw(path)
    return {**_DEFAULTS, **data.get('web', {})}


def verify(settings, username, password):
    try:
        algorithm, iterations, salt, expected = settings['password'].split('$')
        if algorithm != 'pbkdf2_sha256' or int(iterations) != _ITERATIONS:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(settings['username'].encode(), username.encode()) and hmac.compare_digest(expected, digest)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def bind_address(settings):
    if not settings.get('network') or not settings.get('username') or not settings.get('password'):
        return '127.0.0.1'
    return settings['address']


@filetxn.coordinated
def setup(path, username, password, address, port, certificate, key):
    if (any(not isinstance(value, str) for value in (username, password, address, certificate, key))
            or not username.strip() or not password or not address.strip()
            or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        raise ValueError('Username, password, address and a valid port are required')
    if bool(certificate) != bool(key):
        raise ValueError('Both certificate and key are required for HTTPS')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _ITERATIONS).hex()
    data = gui_config.load_raw(path)
    data['web'] = {'username': username, 'password': f'pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest}',
                   'address': address, 'port': port, 'certificate': certificate, 'key': key, 'network': True}
    gui_config.save_file(path, data)


@filetxn.coordinated
def off(path):
    data = gui_config.load_raw(path)
    settings = dict(data.get('web', {}))
    settings['network'] = False
    data['web'] = settings
    gui_config.save_file(path, data)
