"""Evidence-bound coordination of distinct integration diagnostics."""
import hashlib
import json
import uuid

from . import integration, repair_review, runtime


def _snapshot(conn, env, plan):
    from . import orchestrator as orch
    state = orch.load(conn, env.name, plan) or {}
    if state.get('phase') != 'fix' or orch._plan_status(conn, env.name, plan) in ('CANCELLED', 'DONE'):
        return None
    fix = state.get('fix') or {}
    folder = orch._worktree(env, plan)
    head = integration.head_commit(folder) if folder else ''
    if not head or fix.get('step') not in ('Check', 'Build'):
        return None
    evidence = {'commit': head, 'log': fix.get('log_file'), 'error': fix.get('error'),
                'step': fix['step'], 'commands': orch.commands(env),
                'decisions': repair_review.decisions(conn, env.name, plan)}
    scope = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {'plan': plan, 'scope': scope, 'commit': head, 'step': fix['step'],
            'failure_log': fix.get('log_file'), 'error_preview': str(fix.get('error', ''))[:240],
            'state_key': orch.KEY + plan}


def candidates(conn, env, plan):
    from . import orchestrator as orch
    result = []
    for row in runtime.kv_list(conn, env.name, orch.KEY):
        peer = row['key'][len(orch.KEY):]
        if peer != plan and (snapshot := _snapshot(conn, env, peer)):
            result.append(snapshot)
    return sorted(result, key=lambda item: item['plan'])[:8]


def prompt(peers):
    if not peers:
        return ''
    return ('\nOTHER FAILED INTEGRATIONS (bounded candidates, same project):\n' + json.dumps(peers) +
            '\nInspect relevant existing logs/code only if a common cause is plausible. Different wording '
            'alone neither proves nor disproves it. If concrete evidence establishes the same cause, '
            'include optional shared_cause = {plan: candidate plan, scope: candidate scope, cause: explanation, '
            'evidence: {current plan: [observations with source references], candidate plan: [observations '
            'with source references]}} in the technical report. Otherwise omit shared_cause. '
            'Do not claim unrelated work is dependent. Each plan still needs its own full verification.\n')


def validate(conn, env, plan, state, peers, proposal):
    if not isinstance(proposal, dict):
        raise ValueError('shared cause must be an object')
    peer = proposal.get('plan')
    offered = next((p for p in peers if p['plan'] == peer), None)
    if not offered or peer == plan:
        raise ValueError('shared cause peer was not offered in this project')
    current = _snapshot(conn, env, peer)
    if not current or current['scope'] != offered['scope'] or proposal.get('scope') != current['scope']:
        raise ValueError('shared cause peer evidence changed; inspect the current failure')
    if not isinstance(proposal.get('cause'), str) or not proposal['cause'].strip():
        raise ValueError('shared cause needs a concrete explanation')
    evidence = proposal.get('evidence')
    if not isinstance(evidence, dict) or any(
        not isinstance(evidence.get(p), list) or not evidence[p] or
        any(not isinstance(e, str) or not e.strip() for e in evidence[p]) for p in (plan, peer)
    ):
        raise ValueError('shared cause needs observed evidence for both plans')
    return proposal


def attach(conn, env, plan, state, binding):
    from . import orchestrator as orch
    peer = binding['plan']
    leader = orch.load(conn, env.name, peer)
    key = 'orch-shared-repair:' + uuid.uuid4().hex
    group = {'leader': peer, 'step': leader['fix']['step'], 'diagnostic': binding['cause'],
             'evidence': binding['evidence'], 'scope': binding['scope'],
             'members': {peer: leader['fix'].get('log_file'), plan: state['fix'].get('log_file')}}
    runtime.kv_set(conn, env.name, key, json.dumps(group))
    leader.setdefault('shared_causes', []).append(key)
    # Transfer existing dependents as well. The peer must still be in fix phase,
    # so it cannot already be waiting on this plan and form a dependency cycle.
    for existing in state.pop('shared_causes', []):
        previous = json.loads(runtime.kv_get(conn, env.name, existing) or '{}')
        if previous.get('leader') == plan and not previous.get('verified_commit'):
            previous['leader'] = peer
            runtime.kv_set(conn, env.name, existing, json.dumps(previous))
            if existing not in leader['shared_causes']:
                leader['shared_causes'].append(existing)
    orch.save(conn, env.name, peer, leader)
    state.update(phase='shared-repair', shared_repair=key)
    state['fix']['agent'] = None
    orch._record_repair(conn, env.name, plan, 'shared-repair-wait', leader=peer,
                       log_file=state['fix'].get('log_file'), group=key, evidence=binding['evidence'])
    orch._blocker(conn, env, plan, 'waiting for verified shared repair: ' + peer)
    orch._set_status(conn, env, plan, 'WAITING')
