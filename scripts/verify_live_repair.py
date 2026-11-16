"""Opt-in integration acceptance in an EXISTING isolated installation.
Real scheduler, agent runners, provider CLI, Git, checks and result consumption.
By default starts from accepted ready plans; --from-submission also exercises
real Queen decomposition. Does not test the product's live update mechanism.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

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


def terminal_runner(row):
    extra = json.loads(row.get('extra_json') or '{}')
    return bool(row.get('status') in ('done', 'failed', 'interrupted') and row.get('pid') and
                (row.get('processIdentity') or extra.get('processIdentity')) and row.get('exit_code') is not None)


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


def refresh_test_quota(conn):
    """Opt-in real account observation in the isolated profile, never invented quota.

    Automatic quota polling intentionally avoids test data directories. This
    explicit live acceptance uses the same reader and snapshot contract.
    """
    from haivequeen import usage, runtime
    provider = {'name': 'acceptance-codex', 'protocol': 'codex', 'command': 'codex'}
    reply = usage.read_codex_rate_limits(provider)
    limits = (reply.get('rateLimitsByLimitId') or {}).get('codex') or reply.get('rateLimits') or {}
    windows = usage._codex_windows(limits)
    if not any(windows.values()):
        raise RuntimeError('Live test quota is unknown; no start is authorized')
    snapshot = {'saved': datetime.now(timezone.utc).isoformat(), 'source': 'codex:account/rateLimits/read'}
    for key, name in (('fiveHour', 'five_hour'), ('sevenDay', 'seven_day')):
        if windows[name]:
            snapshot[key] = {'utilization': windows[name]['pct'] / 100, 'resetsAt': windows[name]['at']}
    runtime.kv_set(conn, usage.GLOBAL_PROJECT, usage.CODEX_USAGE_KEY + ':acceptance-codex', json.dumps(snapshot))


def provider_wrapper(fixture, cli):
    """Keep normal health probes valid: the command itself forwards CLI arguments."""
    import shlex
    wrapper = fixture / ('provider.cmd' if os.name == 'nt' else 'provider')
    command = [sys.executable, str(ROOT / 'scripts/hardening_provider_fault.py'), str(fixture), cli]
    if os.name == 'nt':
        wrapper.write_text('@' + subprocess.list2cmdline(command) + ' %*\n', encoding='utf-8')
    else:
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n', encoding='utf-8')
        wrapper.chmod(0o700)
    return str(wrapper)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-root', required=True, type=Path)
    parser.add_argument('--model', required=True)
    parser.add_argument('--effort', required=True, choices=['low', 'medium', 'high'])
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--from-submission', action='store_true', help='Real Queen cut through owner acceptance; inject two provider failures')
    parser.add_argument('--resume-fixture', type=Path, help='Resume this acceptance fixture after observer restart')
    parser.add_argument('--normal-provider-mode', action='store_true', help='Use the owner-authorized unsandboxed provider mode only in the validated test installation')
    args = parser.parse_args()
    root = validate_root(args.test_root)
    os.environ.update(BZZ_DATA_DIR=str(root / 'data'), BZZ_CONFIG_DIR=str(root / 'config'))
    for key in ('BZZ_TEST_NOSTART', 'BZZ_FAKE_AGENT', 'BZZ_NO_LOGIN_CHECK', 'BZZ_LOG_DIR', 'BZZ_RUNTIME_OVERRIDE'):
        os.environ.pop(key, None)
    from haivequeen import config, db, loop, runtime, scheduler, settings, hives, orchestrator, agent_process, profile
    if loop.loop_running() or loop.start_pending():
        raise RuntimeError('Existing test Orchestrator is active; refusing to interfere')
    conn = db.connect(config.db_path())
    resume = require_inside(root, args.resume_fixture) if args.resume_fixture else None
    if resume and (not (resume / 'original-config.toml').is_file() or not resume.name.startswith('acceptance-live-')):
        raise RuntimeError('Not a recorded acceptance fixture')
    if any(agent_process.counts(row) and (not resume or row['project'] != resume.name) for row in runtime.list_agents(conn, None, 'active')):
        raise RuntimeError('Existing test agents are active; refusing to interfere')
    for row in conn.execute('SELECT workspace FROM projects'):
        if row[0]:
            require_inside(root, row[0])
    if not profile.has_login({'protocol': 'codex'}):
        raise RuntimeError('The existing test Codex profile needs a login')
    if resume:
        fixture, name = resume, resume.name
        repo = fixture / 'repo'
        paths = ['plans/ACCEPTANCE-%d-PLAN.md' % i for i in (1, 2)]
        worktrees = []
        registered = conn.execute('SELECT workspace FROM projects WHERE name=?', (name,)).fetchone()
        if not registered or Path(registered[0]).resolve() != repo.resolve():
            raise RuntimeError('Fixture project registration differs')
    else:
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
        if args.from_submission:
            validation = repo / 'validate.py'
            validation.write_text(validation.read_text().replace("if sys.argv[1]=='build':", "if sys.argv[1] in ('check','build'):"), encoding='utf-8')
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
        if args.from_submission:
            for i, body in enumerate((
                '# Complete CLI workflow\n\n## Requirements\n'
                '- R1: Add a Python CLI app.py with `record KEY VALUE` and `read KEY`. Store data in ledger.json in the current directory.\n'
                '- R2: Separate storage from command parsing. Recording the same key/value twice is idempotent; replacing a value is supported. A missing key returns exit 2 and a useful message.\n'
                '- R3: Persist atomically so interruption before replacement retains the prior valid ledger. After restarting the CLI, reads use persisted data.\n'
                '- R4: Add focused executable tests of the real CLI, including replacement, duplicate records, missing key, restart and interrupted write. Demonstrate the joined CLI/storage path.\n',
                '# Simple documentation change\n\n## Requirements\n'
                '- R1: Add USAGE.md explaining that this repository is an isolated HQ acceptance fixture with a local Git remote; no production installation is involved.\n'
            ), 1):
                path = 'plans/ACCEPTANCE-%d-PLAN.md' % i
                paths.append(path)
                (repo / path).write_text('+++\nstatus = "SENT"\n+++\n' + body, encoding='utf-8')
            runtime.sync_plans(conn, name, str(repo))
        else:
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
    runs.mkdir(parents=True, exist_ok=True)
    grant_runner_access(fixture)
    grant_runner_access(runs)
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for folder in [repo, *worktrees]:
        os.environ['GIT_CONFIG_KEY_%d' % count] = 'safe.directory'
        os.environ['GIT_CONFIG_VALUE_%d' % count] = str(folder)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    config_path = root / 'config/config.toml'
    original = (fixture / 'original-config.toml').read_bytes() if resume else config_path.read_bytes()
    if not resume:
        (fixture / 'original-config.toml').write_bytes(original)
    stop_path = root / 'data/STOP'
    original_stop = ((fixture / 'original-STOP').read_bytes() if (fixture / 'original-STOP').exists() else None) if resume else (stop_path.read_bytes() if stop_path.exists() else None)
    if original_stop is not None:
        (fixture / 'original-STOP').write_bytes(original_stop)
    report_path = root / 'data/logs/live-repair-result.json'
    report_path.parent.mkdir(exist_ok=True)
    rows = []
    result = {'passed': False, 'project': name, 'fixture': str(fixture), 'scope': ('real Queen cut, runners, provider CLI, Git, integration and owner acceptance; provider boundary injects empty repair and malformed diagnosis' if args.from_submission else 'real scheduler, runner processes, provider CLI, Git and verification; accepted-ready checkpoint')}
    try:
        cfg = settings.load_raw(config_path)
        cfg['providers'] = [{'name': 'acceptance-codex', 'protocol': 'codex', 'command': 'codex', 'slots': 2, 'use': 'preferred', 'classes': {c: {'model': args.model, 'effort': args.effort} for c in ('light','medium','heavy')}, 'args': ['exec','--ephemeral','--ignore-user-config','--ignore-rules','--approve-for-me','--add-dir',str(root / 'data'),'--add-dir',str(root / 'config'),'-c','windows.sandbox="elevated"','--model','{model}','-c','model_reasoning_effort="{effort}"','--json']}]
        if args.normal_provider_mode:
            cfg['providers'][0]['args'] = ['exec', '--json', '--skip-git-repo-check',
                '-m', '{model}', '-c', 'model_reasoning_effort={effort}',
                '--dangerously-bypass-approvals-and-sandbox', '--ignore-user-config', '-C', '{cwd}']
        if args.from_submission:
            import shutil
            cli = shutil.which('codex')
            if not cli:
                raise RuntimeError('Codex CLI is unavailable')
            provider = cfg['providers'][0]
            provider['command'] = provider_wrapper(fixture, cli)
            provider['beeMode'] = 'subagent'
            provider['slots'] = 4
            for cls, effort in (('light', 'low'), ('medium', 'medium'), ('heavy', args.effort)):
                provider['classes'][cls]['effort'] = effort
            # Explicit existing settings stay untouched outside this temporary test configuration.
            cfg.pop('queenClass', None)
            cfg.pop('diagnosisClass', None)
        if args.from_submission:
            project_path = repo / '.haivequeen/project.toml'
            project_settings = settings.load_raw(project_path)
            project_settings['deployCommand'] = subprocess.list2cmdline([
                sys.executable, str(ROOT / 'scripts/hardening_delivery.py'), str(root), str(fixture / 'delivered')])
            settings.save_file(project_path, project_settings)
        settings.save_file(config_path, cfg)
        if original_stop is not None:
            stop_path.unlink()
        machine = config.load_config()
        envs = [env for env in scheduler.load_projects(conn, machine) if env.name == name]
        deadline = time.monotonic() + args.timeout
        previous = None
        quota_at = 0
        while time.monotonic() < deadline:
            if time.monotonic() - quota_at >= 60:
                refresh_test_quota(conn)
                quota_at = time.monotonic()
            machine = config.load_config()
            envs = [env for env in scheduler.load_projects(conn, machine) if env.name == name]
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
            completed = all(any(e['event']=='verified' for e in history) for history in histories)
            if args.from_submission:
                completed = all(any(q['item']=='owner-check' and q['plan_path']==path for q in runtime.list_questions(conn, name, 'open')) for path in paths)
            if completed and not any(agent_process.counts(r) for r in rows):
                assert rows and all((terminal_runner(r) if args.from_submission else finished_runner(r)) for r in rows), rows
                assert any(e.get('step')==('Check' if args.from_submission else 'Build') and e['event']=='failure' for history in histories for e in history)
                if not args.from_submission:
                    assert all({'Merge','Check','Build','Push'} <= set(next(e for e in reversed(history) if e['event']=='verified')['steps']) for history in histories)
                else:
                    assert (fixture / 'empty-repair').exists() and (fixture / 'malformed-diagnosis').exists()
                    owner_questions = [q for q in runtime.list_questions(conn, name, 'open') if q['item']=='owner-check']
                    assert len(owner_questions) == len(paths), owner_questions
                    delivery = json.loads((fixture / 'delivered/receipt.json').read_text(encoding='utf-8'))
                    assert delivery['commit'] == git(repo, 'ls-remote', 'origin', 'refs/heads/main').split()[0]
                    result['delivery'] = delivery
                    result['owner_questions'] = [q['id'] for q in owner_questions]
                    result['cuts'] = {path: (repo / path).read_text(encoding='utf-8') for path in paths}
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
        report_text = json.dumps(result, indent=2)
        report_path.write_text(report_text, encoding='utf-8')
        (fixture / 'acceptance-result.json').write_text(report_text, encoding='utf-8')
        conn.close()
        print(json.dumps({k:v for k,v in result.items() if k!='histories'}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
