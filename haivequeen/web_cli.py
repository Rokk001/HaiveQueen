"""Interactive configuration of the web listener."""
import getpass
import json

from . import config, web_auth, web_service, web_server


def cmd_web(args, conn):
    if args.action == 'serve':
        return web_service.Service().run()
    if args.action in ('start', 'stop', 'restart'):
        try:
            result = getattr(web_service, args.action)()
        except (OSError, ValueError) as exc:
            print(json.dumps({'ok': False, 'error': str(exc)}))
            return 1
        print(json.dumps(result))
        return 0 if result.get('ok') else 1
    path = config.config_dir() / 'config.toml'
    if args.action == 'off':
        web_auth.off(path)
        result = web_service.control('reload')
        if result and not result['ok']:
            print(json.dumps(result))
            return 1
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
        web_server.validate_tls({'certificate': certificate, 'key': key})
        web_auth.setup(path, username, password, address, int(port), certificate, key)
    except (ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    if not certificate:
        print('Warning: HTTP sends the password unencrypted over the network.')
    result = web_service.control('reload')
    if result and not result['ok']:
        print(json.dumps(result))
        return 1
    print(json.dumps({'ok': True}))
    return 0


def register(sub):
    parser = sub.add_parser('web', help='configure and control the web server',
        description='Web server: setup enables network access; off returns it to loopback. '
                    'Start, stop and restart control the listener independently of the Orchestrator. '
                    'Serve runs the listener in the foreground for a background service.')
    parser.add_argument('action', choices=['setup', 'off', 'start', 'stop', 'restart', 'serve'])
    parser.set_defaults(fn=cmd_web)
