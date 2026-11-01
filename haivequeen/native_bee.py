"""Status panes for native Bees, which have no separate provider process."""
import argparse
import json
import os
import sys
import time
import uuid

from . import config, db, runtime


# Identify a native Bee pane by its Hive and package.
def key(plan, package):
    """Return the storage key for one native Bee package."""
    return 'native-bee:' + json.dumps([plan, package], ensure_ascii=True, separators=(',', ':'))


# Read the persisted native Bee pane record.
def get(conn, project, plan, package):
    """Return the Bee pane record, or None when no record exists."""
    value = runtime.kv_get(conn, project, key(plan, package))
    return json.loads(value) if value else None


# List native Bee pane records for one Apiary.
def records(conn, project):
    """Return the persisted native Bee pane records."""
    return [json.loads(row['value']) for row in runtime.kv_list(conn, project, 'native-bee:')]


# Find native Bee panes and the Hive panes that contain them.
def occupied(conn, panes):
    """Return live native Bee pane ids and their corresponding Hive pane ids."""
    from . import loop
    ids = {p.id for p in panes if p.alive}
    result = set()
    for row in db.rows(conn, "SELECT project,value FROM kv WHERE substr(key,1,11)='native-bee:'"):
        item = json.loads(row['value'])
        if item.get('pane') not in ids:
            continue
        result.add(item['pane'])
        title = loop.hive_pane_title(row['project'], item['plan'], loop.is_test_runtime())
        result.update(p.id for p in panes if p.alive and p.title == title)
    return result


# Read the creation number from a recorded pane ID.
def pane_order(pane):
    """Return a pane's creation number, or zero when it is unavailable."""
    try:
        return int(str(pane).rsplit('.', 1)[1])
    except (IndexError, ValueError):
        return 0


# Open a status pane beside the Queen and earlier Bees.
def start(env, plan, package, name, task, terminal, window, _resume=None):
    """Open and record a native Bee status pane in its Hive row."""
    from . import loop, platform as osplat
    from .terminal.base import TerminalError, pane_title, safe_name
    token = uuid.uuid4().hex
    last_event = env.conn.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
    item = dict(_resume) if _resume else {'plan': plan, 'package': package, 'name': name, 'task': task, 'token': token, 'running': True, 'pane': '', 'started': db.now(), 'after_event': last_event}
    token = item['token']
    title = pane_title(env.name, 'Bee ' + name.upper(), name, '-', loop.is_test_runtime())
    pane_name = '%s__native__%s__%s' % (env.name, name, token)
    with loop.pane_lock():
        live = [p for p in terminal.list_panes(window) if p.alive]
        hive = next((p for p in live if p.title == loop.hive_pane_title(env.name, plan, loop.is_test_runtime())), None)
        if hive is None:
            raise TerminalError('no Hive pane for Bee %s' % name)
        ids = {p.id for p in live}
        previous = get(env.conn, env.name, plan, package)
        if _resume and (not previous or not previous.get('running') or previous.get('token') != token):
            return ''
        if _resume and any(p.id == previous.get('pane') and p.title == title for p in live):
            return previous['pane']
        if _resume and getattr(terminal, 'name', '') in ('wt', 'headless'):
            registered = next((r for r in terminal.registry.records() if r.get('name') == safe_name(pane_name) and r.get('window') == window), None)
            pane = next((p for p in live if registered and p.id == registered.get('id') and p.title == title), None)
            if pane:
                previous['pane'] = pane.id
                previous['order'] = previous.get('order') or pane_order(pane.id)
                runtime.kv_set(env.conn, env.name, key(plan, package), json.dumps(previous))
                return pane.id
        if previous and previous.get('running') and not _resume:
            raise TerminalError('native Bee package is already running')
        existing = [(a.get('pane_id'), pane_order(a.get('pane_id'))) for a in runtime.list_agents(env.conn, env.name, 'active')
                    if a.get('phase') == 'bee' and a.get('plan_path') == plan and a.get('pane_id') in ids]
        existing += [(r.get('pane'), r.get('order') or pane_order(r.get('pane'))) for r in records(env.conn, env.name)
                     if r.get('plan') == plan and r.get('pane') in ids]
        original_order = item.get('order') or pane_order(item.get('pane'))
        if _resume and original_order:
            existing = [entry for entry in existing if entry[1] < original_order]
        target = max(existing, key=lambda entry: entry[1])[0] if existing else hive.id
        size = loop.bee_split_size(env, plan, name, hive.id, bool(existing))
        runtime.kv_set(env.conn, env.name, key(plan, package), json.dumps(item))
        boot = 'import sys;sys.path.insert(0,sys.argv[1]);from haivequeen.native_bee import main;sys.exit(main(sys.argv[2:]))'
        argv = [osplat.python_executable(), '-c', boot, str(loop.TOOLS_DIR), '--project', env.name, '--plan', plan, '--package', package, '--token', token] + loop.forward_env_args()
        try:
            item['pane'] = loop.split_tracked(env, terminal, window, title, env.colour, argv, 'horizontal', size, target, pane_name)
            item['order'] = original_order or pane_order(item['pane'])
        except Exception:
            if _resume:
                runtime.kv_set(env.conn, env.name, key(plan, package), json.dumps(_resume))
            else:
                item['running'] = False
                runtime.kv_set(env.conn, env.name, key(plan, package), json.dumps(item))
            raise
        runtime.kv_set(env.conn, env.name, key(plan, package), json.dumps(item))
    return item['pane']


# Restore missing native Bee displays without restarting their agents.
def ensure(env, terminal, window):
    """Reopen status panes that disappeared while their native Bee runs."""
    from . import loop
    from .terminal import TerminalError
    from .terminal.base import pane_title
    live = {(p.id, p.title) for p in terminal.list_panes(window) if p.alive}
    for item in records(env.conn, env.name):
        title = pane_title(env.name, 'Bee ' + item['name'].upper(), item['name'], '-', loop.is_test_runtime())
        if item.get('running') and (item.get('pane'), title) not in live:
            try:
                start(env, item['plan'], item['package'], item['name'], item['task'], terminal, window, _resume=item)
            except (TerminalError, OSError) as exc:
                env.log('native Bee pane of %s not restored: %s' % (item['name'], exc))


# Tell a native Bee's status pane that its attempt ended.
def finish(conn, project, plan, package):
    """Mark the package's native Bee pane finished, if one exists."""
    from . import loop
    with loop.pane_lock():
        item = get(conn, project, plan, package)
        if item and item.get('running'):
            item['running'] = False
            item['ended'] = db.now()
            runtime.kv_set(conn, project, key(plan, package), json.dumps(item))


# Draw a native Bee status until the Queen reports its result.
def run(conn, project, plan, package, token, out=None, interval=1.0):
    """Show the Bee's task and progress until its recorded attempt ends."""
    from . import header, hives
    out = out or sys.stdout
    while True:
        item = get(conn, project, plan, package)
        if not item or not item.get('running') or item.get('token') != token:
            return
        data = hives.hive_data(conn, project, runtime.list_agents(conn, project, 'active'), plan, include_done=True)
        bee = next((b for hive in data for b in hive['bees'] if b['name'] == item['name']), None)
        row = {'phase': 'bee', 'person': item['name'], 'task': (bee or {}).get('task') or item['task'], 'bee_step': (bee or {}).get('step') or ''}
        width = __import__('shutil').get_terminal_size((80, 24)).columns
        colour = header.project_colour_of(conn, project)
        lines = header.build_head(row, project, colour, max(1, width))
        out.write('\x1b[H\x1b[2J' + header.sgr(colour) + ''.join(text for text, _ in lines[0]) + '\x1b[0m')
        out.flush()
        time.sleep(interval)


# Run the native Bee pane from the terminal launcher.
def main(argv=None):
    """Parse the pane identity and run its status display."""
    parser = argparse.ArgumentParser()
    for name in ('project', 'plan', 'package', 'token'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--env', action='append', default=[])
    args = parser.parse_args(argv)
    for pair in args.env:
        name, _, value = pair.partition('=')
        if name:
            os.environ[name] = value
    from . import header
    header.enable_vt()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    conn = db.connect(config.db_path())
    try:
        run(conn, args.project, args.plan, args.package, args.token)
    finally:
        conn.close()
    return 0
