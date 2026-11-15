"""Opt-in integration acceptance in an EXISTING isolated installation.
Real scheduler, agent runners, provider CLI, Git, checks and result consumption.
Starts from two accepted ready plans; does not test Queen decomposition or live update.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require_inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or root not in path.parents:
        raise ValueError('Path must be inside the existing test installation')
    return path


def validate_root(root):
    root = Path(root).resolve()
    for name in ('config/config.toml', 'data/haivequeen.db', 'venv'):
        path = require_inside(root, root / name)
        if not path.exists():
            raise ValueError('Existing test installation required: ' + name)
    return root


def finished_runner(row):
    extra = json.loads(row.get('extra_json') or '{}')
    return bool(row.get('status') == 'done' and row.get('pid') and
                (row.get('processIdentity') or extra.get('processIdentity')) and row.get('exit_code') == 0)


def grant_runner_access(folder):
    if os.name != 'nt':
        return
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
    sid = next(csv.reader(identity.strip().splitlines()))[1]
    subprocess.run(['icacls', str(folder), '/grant', '*' + sid + ':(OI)(CI)F', '/T', '/Q'],
                   check=True, capture_output=True)


def git(folder, *args):
    result = subprocess.run(['git', '-C', str(folder), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-root', required=True, type=Path)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', required=True, choices=['low', 'medium', 'high'])
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    root = validate_root(args.test_root)
    os.environ.update(BZZ_DATA_DIR=str(root / 'data'), BZZ_CONFIG_DIR=str(root / 'config'))
    for key in ('BZZ_TEST_NOSTART', 'BZZ_FAKE_AGENT', 'BZZ_NO_LOGIN_CHECK', 'BZZ_LOG_DIR', 'BZZ_RUNTIME_OVERRIDE'):
        os.environ.pop(key, None)
    from haivequeen import config, db, loop, runtime, scheduler, settings, hives, orchestrator, agent_process, profile
    if loop.loop_running() or loop.start_pending():
        raise RuntimeError('Existing test Orchestrator is active; refusing to interfere')
    conn = db.connect(config.db_path())
    if any(agent_process.counts(row) for row in runtime.list_agents(conn, None, 'active')):
        raise RuntimeError('Existing test agents are active; refusing to interfere')
    for row in conn.execute('SELECT workspace FROM projects'):
        if row[0]:
            require_inside(root, row[0])
    if not profile.has_login({'protocol': 'codex'}):
        raise RuntimeError('The existing test Codex profile needs a login')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    name = 'acceptance-live-' + stamp
    fixture = root / 'data' / name
    fixture.mkdir()
    repo, remote = fixture / 'repo', fixture / 'remote.git'
    repo.mkdir()
    git(fixture, 'init', '--bare', '-q', '-b', 'main', str(remote))
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Acceptance')
    git(repo, 'config', 'user.email', 'acceptance@example.invalid')
    (repo / '.gitignore').write_text('.haivequeen/\nplans/\n')
    (repo / 'RenderManager.cpp').write_text('// base comment\nint getBrighterMaterial() { return 1; }\n')
    (repo / 'validate.py').write_text("import sys,subprocess\nfrom pathlib import Path\ns=Path('RenderManager.cpp').read_text()\nassert '<<<<<<<' not in s and 'return 1;' in s\nif sys.argv[1]=='build':\n assert Path('index.txt').exists(), 'Generate index.txt containing ready, commit it, then rerun this check'\n assert Path('index.txt').read_text().strip()=='ready'\n subprocess.run(['git','ls-files','--error-unmatch','index.txt'],check=True)\n")
    git(repo, 'add', '--', '.gitignore', 'RenderManager.cpp', 'validate.py')
    git(repo, 'commit', '-qm', 'Seed acceptance project')
    git(repo, 'remote', 'add', 'origin', str(remote))
    git(repo, 'push', '-qu', 'origin', 'main')
    (repo / '.haivequeen').mkdir()
    (repo / 'plans').mkdir()
    check = '"%s" validate.py check' % sys.executable
    build = '"%s" validate.py build' % sys.executable
    (repo / '.haivequeen/project.toml').write_text('name = '+json.dumps(name)+'\nplanGlobs = ["plans/*-PLAN.md"]\ndeleteFinishedWorktrees = false\ncompileCheck = '+json.dumps(check)+'\nbuildCommand = '+json.dumps(build)+'\nexcludedProviders = ["claude"]\n')
    db.upsert_project(conn, name, str(repo), 'white', 0)
    paths = []
    worktrees = []
    for i in (1, 2):
        branch = 'plan-%d' % i
        folder = fixture / branch
        git(repo, 'worktree', 'add', '-q', '-b', branch, str(folder))
        worktrees.append(folder)
        (folder / 'RenderManager.cpp').write_text('// retain brighter material\nint getBrighterMaterial() { return 1; }\n')
        git(folder, 'add', '--', 'RenderManager.cpp')
        git(folder, 'commit', '-qm', 'Change comment on plan branch')
        path = 'plans/ACCEPTANCE-%d-PLAN.md' % i
        paths.append(path)
        (repo / path).write_text('+++\nstatus = "RUNNING"\nbranch = '+json.dumps(branch)+'\nworktree = '+json.dumps(str(folder))+'\n+++\n# Integration acceptance\nPreserve getBrighterMaterial behavior and both comment meanings. Only fix the current failed step. Generate the required index artifact only after Build reports it missing. Only use this synthetic repository and its local remote.\n')
        runtime.sync_plans(conn, name, str(repo))
        hives.create(conn, name, path, 'acceptance-codex', 'white')
        ready = db.add_ready(conn, name, branch, git(folder, 'rev-parse', 'HEAD'), path, 'Accepted ready checkpoint for integration acceptance')
        conn.execute("UPDATE hives SET ready_id=?,state='running' WHERE project=? AND plan_path=?", (ready['id'], name, path))
    (repo / 'RenderManager.cpp').write_text('// reuse the bright variant\nint getBrighterMaterial() { return 1; }\n')
    git(repo, 'add', '--', 'RenderManager.cpp')
    git(repo, 'commit', '-qm', 'Create upstream comment conflict')
    git(repo, 'push', '-q', 'origin', 'main')
    runs = root / 'data/runs' / name
    runs.mkdir(parents=True)
    grant_runner_access(fixture)
    grant_runner_access(runs)
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for folder in [repo, *worktrees]:
        os.environ['GIT_CONFIG_KEY_%d' % count] = 'safe.directory'
        os.environ['GIT_CONFIG_VALUE_%d' % count] = str(folder)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    config_path = root / 'config/config.toml'
    original = config_path.read_bytes()
    (fixture / 'original-config.toml').write_bytes(original)
    stop_path = root / 'data/STOP'
    original_stop = stop_path.read_bytes() if stop_path.exists() else None
    if original_stop is not None:
        (fixture / 'original-STOP').write_bytes(original_stop)
    report_path = root / 'data/logs/live-repair-result.json'
    report_path.parent.mkdir(exist_ok=True)
    rows = []
    result = {'passed': False, 'project': name, 'fixture': str(fixture), 'scope': 'real scheduler, runner processes, provider CLI, Git and verification; accepted-ready checkpoint'}
    try:
        cfg = settings.load_raw(config_path)
        cfg['providers'] = [{'name': 'acceptance-codex', 'protocol': 'codex', 'command': 'codex', 'slots': 2, 'use': 'preferred', 'classes': {c: {'model': args.model, 'effort': args.effort} for c in ('light','medium','heavy')}, 'args': ['exec','--ephemeral','--ignore-user-config','--ignore-rules','--approve-for-me','--add-dir',str(root / 'data'),'--add-dir',str(root / 'config'),'-c','windows.sandbox="elevated"','--model','{model}','-c','model_reasoning_effort="{effort}"','--json']}]
        settings.save_file(config_path, cfg)
        if original_stop is not None:
            stop_path.unlink()
        machine = config.load_config()
        envs = [env for env in scheduler.load_projects(conn, machine) if env.name == name]
        deadline = time.monotonic() + args.timeout
        previous = None
        while time.monotonic() < deadline:
            loop.run_round(conn, machine, envs, settle=0)
            rows = runtime.list_agents(conn, name, 'all')
            summary = [(r['name'],r['status'],r.get('pid')) for r in rows]
            if summary != previous:
                print(json.dumps(summary), flush=True)
                previous = summary
            questions = [q for q in runtime.list_questions(conn, name, 'open') if q['item'] != 'owner-check']
            if questions:
                raise RuntimeError('Technical escalation: '+json.dumps(questions))
            histories = [orchestrator.repair_history(conn,name,path) for path in paths]
            if all(any(e['event']=='verified' for e in history) for history in histories) and not any(agent_process.counts(r) for r in rows):
                assert rows and all(finished_runner(r) for r in rows), rows
                assert any(e.get('step')=='Build' and e['event']=='failure' for history in histories for e in history)
                assert all({'Merge','Check','Build','Push'} <= set(next(e for e in reversed(history) if e['event']=='verified')['steps']) for history in histories)
                result.update(passed=True, runners=summary, histories=histories)
                break
            time.sleep(2)
        else:
            raise TimeoutError('Acceptance deadline reached')
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        live = [r for r in runtime.list_agents(conn,name,'all') if agent_process.counts(r)]
        result['live_runners'] = [r['name'] for r in live]
        if not live:
            config_path.write_bytes(original)
            if original_stop is not None:
                stop_path.write_bytes(original_stop)
            result['configuration_restored'] = True
        else:
            result['configuration_restored'] = False
        report_path.write_text(json.dumps(result,indent=2), encoding='utf-8')
        conn.close()
        print(json.dumps({k:v for k,v in result.items() if k!='histories'}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
