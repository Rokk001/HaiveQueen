"""Confirmed MCP configuration and tool evidence in HaiveQueen's own CLI profile."""
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from . import profile, filetxn, db

NATIVE = frozenset(('Coding', 'Queen', 'Build', 'Deploy', 'Confidential data'))
SUPPORTED = frozenset(('claude', 'codex', 'opencode', 'kilo'))
_lock = threading.RLock()


def paths(provider):
    key = hashlib.sha256(str(provider.get('name', '')).encode()).hexdigest()[:16]
    home = profile.provider_home(provider)
    return home / ('hq-mcp-' + key + '.json'), home / ('hq-mcp-' + key + '-state.json')


def validate(value):
    if not isinstance(value, dict) or set(value) != {'mcpServers', 'capabilities'}:
        raise ValueError('Supply mcpServers and capabilities objects.')
    servers, bindings = value['mcpServers'], value['capabilities']
    if not isinstance(servers, dict) or not isinstance(bindings, dict) or len(servers) > 20:
        raise ValueError('Invalid MCP configuration')
    for name, server in servers.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not isinstance(server, dict):
            raise ValueError('Invalid MCP server name')
        allowed = {'command', 'args', 'env', 'type'} if 'command' in server else {'url', 'headers', 'type'}
        if set(server) - allowed:
            raise ValueError('Unsupported MCP configuration fields: ' + ', '.join(sorted(set(server)-allowed)))
        if 'command' in server:
            if not isinstance(server['command'], str) or not server['command'].strip() or server.get('type', 'stdio') != 'stdio':
                raise ValueError('A stdio server needs a command')
            if not isinstance(server.get('args', []), list) or not all(isinstance(a, str) for a in server.get('args', [])):
                raise ValueError('MCP arguments must be a list of strings')
            mapping = server.get('env', {})
        else:
            url = urlsplit(str(server.get('url', '')))
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or server.get('type', 'http') != 'http':
                raise ValueError('A remote server needs an HTTP MCP endpoint')
            mapping = server.get('headers', {})
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in mapping.items()):
            raise ValueError('MCP environment and headers must contain strings')
    for capability, binding in bindings.items():
        if not isinstance(capability, str) or not capability.strip() or not isinstance(binding, dict) or set(binding) != {'server','tools'}:
            raise ValueError('Each capability needs server and tools')
        tools = binding['tools']
        if binding['server'] not in servers or not isinstance(tools, list) or not tools or not all(isinstance(t,str) and t for t in tools):
            raise ValueError('Each capability needs an existing server and nonempty tool names')
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('MCP endpoint redirects are not followed')


def probe(server, provider):
    """Initialize and enumerate tools only; no tool calls or automatic package installation."""
    deadline = time.monotonic() + 20
    process = None
    tracker = None
    messages = queue.Queue(maxsize=256)
    headers = {'Content-Type':'application/json', 'Accept':'application/json, text/event-stream', **server.get('headers', {})}
    opener = build_opener(NoRedirect())
    try:
        if 'command' in server:
            # The owner confirms the supplied command before this function runs.
            process = subprocess.Popen([server['command'], *server.get('args', [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, env={**profile.environment(provider), **server.get('env', {})},
                                       cwd=profile.provider_home(provider), text=True, encoding='utf-8', errors='replace')
            from . import proctree
            tracker = proctree.Tracker(process.pid).start()
            def read():
                try:
                    while True:
                        line = process.stdout.readline(1048577)
                        if not line or len(line) > 1048576:
                            break
                        messages.put(json.loads(line), timeout=1)
                except (ValueError, OSError, queue.Full):
                    pass
                finally:
                    try: messages.put(None, timeout=1)
                    except queue.Full: pass
            threading.Thread(target=read, daemon=True).start()
        def exchange(method, params=None, ident=None):
            message = {'jsonrpc':'2.0', 'method':method}
            if params is not None: message['params']=params
            if ident is not None: message['id']=ident
            remaining = max(.1, deadline-time.monotonic())
            if remaining <= .1: raise TimeoutError('MCP discovery timed out')
            if process:
                process.stdin.write(json.dumps(message)+'\n');process.stdin.flush()
                if ident is None:return {}
                while time.monotonic() < deadline:
                    reply=messages.get(timeout=max(.1,deadline-time.monotonic()))
                    if reply is None:raise ValueError('MCP server closed')
                    if isinstance(reply,dict) and reply.get('id') == ident:break
                else:raise TimeoutError('MCP discovery timed out')
            else:
                request=Request(server['url'],data=json.dumps(message).encode(),headers=headers,method='POST')
                with opener.open(request,timeout=remaining) as response:
                    if response.headers.get('Mcp-Session-Id'):headers['Mcp-Session-Id']=response.headers['Mcp-Session-Id']
                    if ident is None:return {}
                    if 'text/event-stream' in response.headers.get('Content-Type',''):
                        reply=None;total=0;data=[]
                        while True:
                            raw=response.readline(1048577)
                            if not raw:break
                            total+=len(raw)
                            if total>1048576 or time.monotonic()>deadline:raise TimeoutError('MCP discovery limit reached')
                            line=raw.decode('utf-8').rstrip('\r\n')
                            if line.startswith('data:'):data.append(line[5:].lstrip())
                            elif not line and data:
                                item=json.loads('\n'.join(data));data=[]
                                if isinstance(item,dict) and item.get('id')==ident:reply=item;break
                        if reply is None:raise ValueError('Missing MCP response')
                    else:
                        reply=json.loads(response.read(1048577))
            if not isinstance(reply,dict) or reply.get('id')!=ident or 'error' in reply or not isinstance(reply.get('result'),dict):
                raise ValueError('MCP request failed')
            return reply['result']
        initialized=exchange('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'HaiveQueen','version':'2.0'}},1)
        headers['MCP-Protocol-Version']=str(initialized.get('protocolVersion','2025-06-18'))
        if 'tools' not in initialized.get('capabilities',{}):raise ValueError('MCP server does not advertise tools')
        exchange('notifications/initialized')
        tools=set();cursor=None;seen=set()
        for page in range(32):
            result=exchange('tools/list',{'cursor':cursor} if cursor else {},page+2)
            tools.update(item['name'] for item in result.get('tools',[]) if isinstance(item,dict) and isinstance(item.get('name'),str))
            cursor=result.get('nextCursor')
            if not cursor:return sorted(tools)
            if cursor in seen:raise ValueError('MCP pagination repeated')
            seen.add(cursor)
        raise ValueError('MCP tool listing exceeds page limit')
    finally:
        if process:
            try:
                process.stdin.close()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            finally:
                if tracker:tracker.finish()
                process.stdout.close()


def configure(provider, value, confirmed=False):
    if confirmed is not True:
        raise ValueError('Confirm importing and starting the supplied MCP server configuration in the HaiveQueen profile.')
    if provider.get('protocol') not in SUPPORTED:
        raise ValueError('MCP profile import is supported for Claude Code, Codex, OpenCode and Kilo.')
    value=validate(value)
    dialect=1
    if provider.get('protocol')=='opencode':
        from .cli_updates import version
        result=subprocess.run([str(provider.get('command') or 'opencode'),'--version'],capture_output=True,text=True,timeout=8,env=profile.environment(provider))
        installed=version(result.stdout)
        if result.returncode or installed is None:raise ValueError('Cannot determine OpenCode MCP configuration format')
        dialect=2 if installed[0]>=2 else 1
    config_path,state_path=paths(provider)
    with _lock:
        config_path.parent.mkdir(parents=True,exist_ok=True)
        filetxn.atomic_write(config_path,(json.dumps({'mcpServers':value['mcpServers'], **({'hqDialect':dialect} if provider.get('protocol') in ('opencode','kilo') else {})},indent=2)+'\n').encode('utf-8'))
        # Invalidate previous proof before contacting any new configuration.
        state={'bindings':value['capabilities'],'available':[],'servers':{},'checked_at':db.now()}
        filetxn.atomic_write(state_path,(json.dumps(state)+'\n').encode('utf-8'))
        for name,server in value['mcpServers'].items():
            try:
                tools=probe(server,provider)
                state['servers'][name]={'status':'ready','tools':tools}
            except (OSError, ValueError, KeyError, TypeError, queue.Empty, subprocess.SubprocessError):
                state['servers'][name]={'status':'unavailable','tools':[]}
        from .scheduler import canonical_capability
        for capability,binding in value['capabilities'].items():
            found=state['servers'][binding['server']]
            if found['status']=='ready' and set(binding['tools']).issubset(found['tools']):state['available'].append(canonical_capability(capability))
        state['digest']=hashlib.sha256(config_path.read_bytes()).hexdigest()
        filetxn.atomic_write(state_path,(json.dumps(state)+'\n').encode('utf-8'))
        return status(provider)


def status(provider):
    config_path,state_path=paths(provider)
    try:
        state=json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('digest')!=hashlib.sha256(config_path.read_bytes()).hexdigest():return {'available':[], 'servers':{}, 'message':'Configuration needs verification'}
        return {k:state[k] for k in ('available','servers','checked_at')}
    except (OSError,ValueError,KeyError,TypeError):
        return {'available':[], 'servers':{}, 'message':'Missing setup'}


def toml_value(value):
    if isinstance(value,dict):return '{'+', '.join(json.dumps(k)+' = '+toml_value(v) for k,v in value.items())+'}'
    if isinstance(value,list):return '['+', '.join(toml_value(v) for v in value)+']'
    return json.dumps(value)


def arguments(provider):
    config_path,_=paths(provider)
    if not config_path.is_file():return []
    if provider.get('protocol')=='claude':return ['--mcp-config',str(config_path)]
    if provider.get('protocol')=='codex':
        servers=json.loads(config_path.read_text(encoding='utf-8'))['mcpServers'];args=[]
        for name,server in servers.items():
            entry={k:v for k,v in server.items() if k!='type'}
            if 'headers' in entry:entry['http_headers']=entry.pop('headers')
            args.extend(['-c','mcp_servers.'+json.dumps(name)+'='+toml_value(entry)])
        return args
    return []


def environment(provider, base):
    protocol=provider.get('protocol')
    if protocol not in ('opencode','kilo'):return {}
    config_path,_=paths(provider)
    if not config_path.is_file():return {}
    saved=json.loads(config_path.read_text(encoding='utf-8'))
    key='OPENCODE_CONFIG_CONTENT' if protocol=='opencode' else 'KILO_CONFIG_CONTENT'
    content=json.loads(base.get(key) or '{}')
    servers={}
    for name,server in saved['mcpServers'].items():
        if 'command' in server:
            servers[name]={'type':'local','command':[server['command'],*server.get('args',[])],'environment':server.get('env',{})}
        else:
            servers[name]={'type':'remote','url':server['url'],'headers':server.get('headers',{})}
    if saved.get('hqDialect')==2:
        content.setdefault('mcp',{}).setdefault('servers',{}).update(servers)
    else:
        content.setdefault('mcp',{}).update(servers)
    return {key:json.dumps(content)}
