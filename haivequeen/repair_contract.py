"""Executable repair contracts and evidence for progress short of package completion."""
import hashlib
import json
import shlex
import subprocess
from pathlib import Path


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


def workflow_inputs(cwd, workflow):
    """Check explicit local command inputs without executing an untrusted command.

    Fingerprint their bytes so a cached semantic verdict cannot authorize a
    changed runner. This is a prerequisite check, never a passing test result.
    """
    command = workflow.get('command', '')
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ValueError('Workflow command is malformed: ' + str(exc)) from exc
    if not cwd or not Path(cwd).is_dir():
        raise ValueError('Workflow prerequisite missing: assigned checkout is unavailable')
    root = Path(cwd).resolve()
    inputs = {}
    script_suffixes = {'.py', '.ps1', '.sh', '.cmd', '.bat', '.js', '.mjs', '.cjs', '.ts'}
    for token in tokens:
        value = token.strip('"\'')
        if value in ('cd', 'pushd'):
            raise ValueError('Workflow command must run from the assigned worktree; use an existing runner instead of changing directory')
        if value.startswith('-') and '=' in value:
            value = value.split('=', 1)[1].strip('"\'')
        path = Path(value.replace('\\', '/'))
        if path.suffix.lower() not in script_suffixes:
            continue
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Workflow input must be inside the assigned worktree: ' + value)
        if not resolved.is_file():
            raise ValueError('Workflow prerequisite missing: ' + value +
                             '. Select an existing executable workflow before authorizing implementation; a missing runner is not a failing product baseline.')
        inputs[resolved.relative_to(root).as_posix()] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return inputs


def normalize_workflow(workflow):
    """Accept both documented IDs and descriptive checkpoint objects losslessly."""
    if not isinstance(workflow, dict):
        return workflow
    checkpoints = workflow.get('checkpoints')
    if not isinstance(checkpoints, list):
        return workflow
    descriptions = workflow.get('expectations', {})
    if not isinstance(descriptions, dict) or any(not text(k) or not text(v) for k, v in descriptions.items()):
        raise ValueError('checkpoint expectations must map IDs to text')
    ids, descriptions = [], dict(descriptions)
    for item in checkpoints:
        if isinstance(item, dict):
            if not text(item.get('id')) or not text(item.get('expectation')):
                raise ValueError('checkpoint objects require id and expectation strings')
            key = item['id']
            if key in descriptions and descriptions[key] != item['expectation']:
                raise ValueError('checkpoint expectations conflict')
            descriptions[key] = item['expectation']
        else:
            key = item
        if not text(key) or key in ids:
            raise ValueError('workflow requires fixed unique completion checkpoints')
        ids.append(key)
    workflow['checkpoints'] = ids
    if any(key not in ids for key in descriptions):
        raise ValueError('checkpoint expectation names an unknown ID')
    if descriptions:
        workflow['expectations'] = descriptions
    return workflow


def repair_spec(contract, previous=None):
    """A delegated repair must change behavior, not commission another observation."""
    repair = contract.get('repair')
    if not isinstance(repair, dict) or any(not text(repair.get(k)) for k in ('goal', 'behavior', 'criterion')):
        raise ValueError('repair needs a persistent goal, positive behavior and criterion; perform investigations in diagnosis, not another repair Agent')
    old = ((previous or {}).get('contract') or {}).get('repair') or {}
    old_scope = (((previous or {}).get('contract') or {}).get('workflow') or {}).get('scope')
    if not isinstance(contract.get('workflow'), dict):
        raise ValueError('repair requires a complete workflow with its real entrypoint and focused command')
    new_scope = contract['workflow'].get('scope')
    if new_scope and old_scope != new_scope:
        old = {}
    if old.get('goal') and repair['goal'] != old['goal']:
        raise ValueError('retain the repair goal across follow-ups; narrow implementation, not the goal')
    for key in ('implementation_paths', 'regression_paths'):
        paths = repair.get(key)
        if (not isinstance(paths, list) or not paths or any(not isinstance(v, str) for v in paths)
                or len(set(paths)) != len(paths) or any(not text(v) or Path(v).is_absolute() or '..' in Path(v).parts
                       or '\\' in v or ':' in v or any(c in v for c in '*?[') for v in paths)):
            raise ValueError('repair requires explicit repository-relative ' + key)
    if set(repair['implementation_paths']) & set(repair['regression_paths']):
        raise ValueError('implementation and regression files must be separate')
    if repair['criterion'] not in {c.get('id') for c in contract.get('criteria', [])}:
        raise ValueError('repair criterion must name a prescribed positive check')
    workflow = normalize_workflow(contract.get('workflow'))
    if not isinstance(workflow, dict) or any(not text(workflow.get(k)) for k in ('entrypoint', 'command')):
        raise ValueError('repair requires a complete workflow with its real entrypoint and focused command')
    checkpoints = workflow.get('checkpoints')
    if (not isinstance(checkpoints, list) or not checkpoints or any(not text(v) for v in checkpoints)
            or len(set(checkpoints)) != len(checkpoints)):
        raise ValueError('workflow requires fixed unique completion checkpoints')
    paths = workflow.get('regression_paths')
    if not isinstance(paths, list) or not paths or any(v not in repair['regression_paths'] for v in paths):
        raise ValueError('workflow regressions must be included in the protected regression files')
    advances = repair.get('advances')
    if not isinstance(advances, list) or not advances or any(v not in checkpoints for v in advances):
        raise ValueError('repair must advance named workflow checkpoints, not only a standalone component')
    old_workflow = normalize_workflow(((previous or {}).get('contract') or {}).get('workflow'))
    # An unbound legacy subtask must not freeze the newly requirement-bound workflow.
    if workflow.get('scope') and old_workflow and old_workflow.get('scope') != workflow['scope']:
        old_workflow = None
    if old_workflow and workflow != old_workflow:
        raise ValueError('retain the complete workflow and checkpoints across follow-ups')
    return repair


def implementation_evidence(receipt, assigned, checkout):
    """Bind reported red/green evidence to changed code and unchanged assertions."""
    repair = repair_spec(assigned['contract'])
    baseline = receipt.get('baseline')
    before = assigned.get('commit')
    if not before or not isinstance(baseline, dict) or baseline.get('commit') != before or before == receipt['commit']:
        raise ValueError('repair needs a baseline at the assigned commit and a changed implementation commit')
    check = next(c for c in receipt['checks'] if c['id'] == repair['criterion'])
    if baseline.get('command') != check['command'] or type(baseline.get('exit')) is not int or baseline['exit'] == 0:
        raise ValueError('repair baseline must fail the same prescribed positive check')
    root = Path(receipt['result_path']).parent
    def evidence_file(name):
        if not text(name) or Path(name).is_absolute():
            raise ValueError('baseline evidence must be a relative file')
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file() or not 0 < target.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError('baseline evidence is missing, empty or outside the result directory')
        return target.read_bytes()
    log = evidence_file(baseline.get('log'))
    def git(*args):
        if not checkout:
            raise ValueError('repair checkout is unavailable')
        try:
            result = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('repair commit evidence timed out') from exc
        if result.returncode:
            raise ValueError('repair commit evidence is unavailable')
        return result.stdout
    if git('merge-base', '--is-ancestor', before, receipt['commit']) != b'':
        raise ValueError('repair commit must descend from the assigned baseline')
    changed = set(git('diff', '--name-only', before, receipt['commit'], '--', *repair['implementation_paths']).decode().splitlines())
    if not changed:
        raise ValueError('investigation-only changes are evidence, not implementation progress')
    snapshots = baseline.get('regression_files')
    if not isinstance(snapshots, dict) or set(snapshots) != set(repair['regression_paths']):
        raise ValueError('baseline requires snapshots of every regression file')
    digests = {}
    for name, snapshot in snapshots.items():
        contents = evidence_file(snapshot)
        if git('hash-object', '--path=' + name, str((root / snapshot).resolve())).strip() != git('rev-parse', receipt['commit'] + ':' + name).strip():
            raise ValueError('regression assertions changed after the failing baseline')
        digests[name] = hashlib.sha256(contents).hexdigest()
    workflow = assigned['contract']['workflow']
    prior = assigned.get('workflow_progress') or {}
    workflow_digests = {name: digests[name] for name in workflow['regression_paths']}
    if prior.get('regression_digests') and prior['regression_digests'] != workflow_digests:
        raise ValueError('workflow assertions changed between repair rounds')
    evidence = receipt.get('workflow')
    if evidence is None:
        # A repair step is proven by its focused red/green criterion; the complete
        # workflow runs once as the package's completion evidence, not per step.
        receipt['implementation'] = dict(workflow=dict(scope=workflow.get('scope'), checks=dict(prior.get('checks') or {}),
            regression_digests=workflow_digests), goal=repair['goal'], before=before, changed=sorted(changed),
            baseline_digest=hashlib.sha256(log).hexdigest(), regression_digests=digests, focused=True)
        return receipt
    if not isinstance(evidence, dict) or evidence.get('command') != workflow['command']:
        raise ValueError('optional workflow evidence must name the contract workflow command')
    def outcomes(name):
        try:
            result = json.loads(evidence_file(name))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('workflow log must contain its structured checkpoint results') from exc
        checks = result.get('checks') if isinstance(result, dict) else None
        if (not isinstance(checks, dict) or set(checks) != set(workflow['checkpoints'])
                or any(v not in ('passed', 'failed') for v in checks.values())
                or type(result.get('exit')) is not int
                or (result['exit'] == 0) != all(v == 'passed' for v in checks.values())):
            raise ValueError('workflow evidence must cover every checkpoint with a consistent exit code')
        return checks
    initial, final = outcomes(evidence.get('before')), outcomes(evidence.get('after'))
    for key, status in {**prior.get('checks', {}), **{k:v for k,v in initial.items() if v == 'passed'}}.items():
        if status == 'passed' and (initial.get(key) != 'passed' or final.get(key) != 'passed'):
            raise ValueError('repair regressed a previously passing workflow checkpoint')
    if not any(initial[key] == 'failed' and final[key] == 'passed' for key in repair['advances']):
        raise ValueError('standalone component changed but no required workflow checkpoint improved')
    receipt['implementation'] = dict(workflow=dict(scope=workflow.get('scope'), checks=final, regression_digests=workflow_digests), goal=repair['goal'], before=before, changed=sorted(changed),
        baseline_digest=hashlib.sha256(log).hexdigest(), regression_digests=digests)
    return receipt


def validate(report, previous=None):
    contract = report.get('contract')
    if not isinstance(contract, dict) or not text(contract.get('objective')) or not text(contract.get('why_executable')):
        raise ValueError('technical diagnosis requires an executable repair contract')
    prerequisites = contract.get('prerequisites')
    if not isinstance(prerequisites, list):
        raise ValueError('contract requires explicit prerequisites (empty only if none)')
    by_id = {}
    for item in prerequisites:
        if (not isinstance(item, dict) or not text(item.get('id')) or item['id'] in by_id
                or item.get('status') not in ('verified', 'unresolved') or not text(item.get('reason'))):
            raise ValueError('invalid or duplicate prerequisite')
        evidence = item.get('evidence', [])
        if item['status'] == 'verified' and (not isinstance(evidence, list) or not evidence
                or any(type(i) is not int or not 0 <= i < len(report['evidence']) for i in evidence)):
            raise ValueError('verified prerequisite needs references to observed evidence')
        by_id[item['id']] = item
    for field in ('requires', 'resolves'):
        ids = contract.get(field)
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in by_id for i in ids):
            raise ValueError('contract requires known prerequisite IDs in ' + field)
    if any(by_id[i]['status'] != 'verified' for i in contract['requires']):
        raise ValueError('repair requires an unresolved prerequisite; resolve that prerequisite first')
    if set(contract['resolves']) & set(contract['requires']):
        raise ValueError('repair cannot assume the prerequisite it is meant to establish')
    old = (previous or {}).get('contract') or {}
    unresolved = {p['id'] for p in old.get('prerequisites', []) if p['status'] == 'unresolved'}
    if not unresolved <= by_id.keys():
        raise ValueError('retain unresolved prerequisites from the previous contract')
    criteria = contract.get('criteria')
    if not isinstance(criteria, list) or not criteria:
        raise ValueError('contract requires acceptance criteria')
    seen = set()
    for criterion in criteria:
        if (not isinstance(criterion, dict) or not text(criterion.get('id')) or criterion['id'] in seen
                or not text(criterion.get('expectation')) or not text(criterion.get('check'))):
            raise ValueError('each criterion needs a unique ID, expectation and focused check command')
        seen.add(criterion['id'])
    repair_spec(contract, previous)
    return contract


def instruction(report):
    # No competing shortened task: every safety condition and criterion survives.
    return ('\n'.join(report[k] for k in ('analysis', 'next_action', 'why_new'))
            + ('\nPrevious attempt assessment: ' + json.dumps(report['continuation'], ensure_ascii=False)
               if report.get('continuation') else '')
            + '\nAUTHORITATIVE REPAIR CONTRACT\n' + json.dumps(report['contract'], ensure_ascii=False)
            + '\nContract ID: ' + identity(report['contract']))


def result(path, assigned, attempt, commit):
    """Validate a Queen-reviewed receipt against the exact assigned step and checkout."""
    if not text(commit):
        raise ValueError('progress requires a known current commit')
    path = Path(path)
    if not assigned or assigned.get('attempt') != attempt or not attempt:
        raise ValueError('progress requires the matching assigned repair attempt')
    if path.stat().st_size > 65536:
        raise ValueError('repair result exceeds 64 KiB')
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in
            [('attempt', attempt), ('contract', assigned['id']), ('commit', commit)]):
        raise ValueError('repair result does not match attempt, contract and current commit')
    criteria = assigned['contract']['criteria']
    checks = receipt.get('checks')
    if not isinstance(checks, list) or len(checks) != len(criteria):
        raise ValueError('progress requires verification of every repair criterion')
    by_id = {c['id']: c for c in criteria}
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or not text(check.get('id')) or check['id'] not in by_id or check['id'] in seen:
            raise ValueError('repair check IDs must match the contract exactly')
        seen.add(check['id'])
        if check.get('command') != by_id[check['id']]['check'] or type(check.get('exit')) is not int or check['exit'] != 0:
            raise ValueError('progress requires successful prescribed checks')
        log = check.get('log')
        if not text(log) or Path(log).is_absolute():
            raise ValueError('repair check log must be a local relative path')
        target = (path.resolve().parent / log).resolve()
        if not target.is_relative_to(path.resolve().parent) or not target.is_file() or not 0 < target.stat().st_size <= 4 * 1024 * 1024:
            raise ValueError('repair check log is missing, empty or outside the result directory')
        check['digest'] = hashlib.sha256(target.read_bytes()).hexdigest()
    receipt['result_path'] = str(path.resolve())
    return receipt
