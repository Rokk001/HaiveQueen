"""Interactive configuration of the web listener."""
import getpass
import json

from . import config, web_auth


def cmd_web(args, conn):
    path = config.config_dir() / 'config.toml'
    if args.action == 'off':
        web_auth.off(path)
        print(json.dumps({'ok': True, 'network': False}))
        return 0
    settings = web_auth.read(path)
    username = input('Username: ').strip()
    password = getpass.getpass('Password: ')
    if password != getpass.getpass('Confirm password: '):
        print(json.dumps({'ok': False, 'error': 'Passwords do not match'}))
        return 1
    address = input('Address [%s]: ' % settings['address']).strip() or settings['address']
    port = input('Port [%s]: ' % settings['port']).strip() or str(settings['port'])
    certificate = input('Certificate file (optional): ').strip()
    key = input('Key file (optional): ').strip()
    try:
        web_auth.setup(path, username, password, address, int(port), certificate, key)
    except (ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    if not certificate:
        print('Warning: HTTP sends the password unencrypted over the network.')
    print(json.dumps({'ok': True}))
    return 0


def register(sub):
    parser = sub.add_parser('web', help='configure web access')
    parser.add_argument('action', choices=['setup', 'off'])
    parser.set_defaults(fn=cmd_web)
