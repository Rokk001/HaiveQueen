"""Executable repair contracts and evidence for progress short of package completion."""
import hashlib
import json
from pathlib import Path


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


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
