"""Bind repair workflows to project-owned requirements, not a diagnosis's subtask."""
import json
from pathlib import Path

from . import plans, repair_contract, repair_review, runtime


def reference(conn, project, plan, package):
    row = conn.execute('SELECT workspace FROM projects WHERE name=?', (project,)).fetchone()
    path = Path(row[0]) / plan if row and row[0] else None
    current = path.read_text(encoding='utf-8-sig') if path and path.is_file() else ''
    submitted = runtime.get_submission(conn, project, plan)
    parsed = plans.parse_plan_text(current or submitted or '')
    item = next((p for p in parsed.packages if p.id == package), None)
    original = next((p for p in plans.parse_plan_text(submitted or '').packages if p.id == package), None)
    if original and original.requirements:
        item = original
    requirements = dict(plans.parse_requirements(submitted or ''))
    state_path = Path(plans.state_file_path(str(path))) if path else None
    if state_path and state_path.is_file():
        for key, value in plans.parse_requirements(state_path.read_text(encoding='utf-8-sig')):
            requirements.setdefault(key, value)
    ids = item.requirements if item else []
    missing = (['submitted plan'] if ids and submitted is None else []) + [key for key in ids if key not in requirements]
    if requirements and not ids:
        missing.append('package requirement assignment')
    selected = {key: requirements.get(key) for key in ids}
    if not selected:
        row = conn.execute('SELECT title FROM packages WHERE project=? AND plan_path=? AND id=?',
                           (project, plan, package)).fetchone()
        selected = {'package:' + package: item.title if item else (row[0] if row else package)}
    data = dict(project=project, plan=plan, package=package, requirements=selected, missing=missing,
                decisions=repair_review.decisions(conn, project, plan))
    return dict(id=repair_contract.identity(data), requirements=selected, missing=missing,
                sources=[plan, plans.state_file_path(plan)],
                decisions=[d['id'] for d in data['decisions']])


def validate(contract, reference):
    if reference['missing']:
        raise ValueError('repair scope source is incomplete: ' + ', '.join(reference['missing']))
    workflow = contract['workflow']
    if workflow.get('scope') != reference['id']:
        raise ValueError('workflow must bind the current package requirements and decisions using the supplied scope ID')
    coverage = workflow.get('coverage')
    if not isinstance(coverage, dict) or set(coverage) != set(reference['requirements']):
        raise ValueError('workflow coverage must retain every package requirement, including the complete execution path')
    for checkpoints in coverage.values():
        if isinstance(checkpoints, dict):
            if (type(checkpoints.get('decision')) is not int or checkpoints['decision'] not in reference['decisions'] or
                    not repair_contract.text(checkpoints.get('reason'))):
                raise ValueError('excluded requirement needs an active decision and its reason')
            continue
        if (not isinstance(checkpoints, list) or not checkpoints or
                any(c not in workflow['checkpoints'] for c in checkpoints)):
            raise ValueError('each package requirement needs named workflow checkpoints')
    if workflow['entrypoint'] in contract['repair']['regression_paths']:
        raise ValueError('workflow entrypoint must name the product execution path, not a test file')
    return contract


def instruction(reference):
    return ('\nHQ package scope (independent of diagnosis): ' + json.dumps(reference, ensure_ascii=False)
            + '\nRetain this scope ID and cover every requirement in workflow.coverage. Read the named '
              'source sections and applicable decisions. Describe the product entrypoint and visible '
              'success/failure/recovery outcomes, including already passing checkpoints. A prerequisite '
              'or helper test cannot replace the required execution path; a bounded repair must make '
              'progress through that path. Reuse existing evidence; do not invent requirements.\n')


def review(env, plan, package, report, cwd, free_mb, alive):
    """Review the complete proposal; reuse only an identical reviewed input."""
    from . import agent_process, bees, db, loop, readygate
    ref = reference(env.conn, env.name, plan, package)
    validate(report['contract'], ref)
    inputs = repair_contract.workflow_inputs(cwd, report['contract']['workflow'])
    proposal = dict(contract=report['contract'], next_action=report.get('next_action', ''))
    fingerprint = repair_contract.identity(['repair-proposal-v3', ref['id'], proposal, inputs])
    key = 'repair-workflow-review:' + json.dumps([plan, package], separators=(',', ':'))
    record = json.loads(runtime.kv_get(env.conn, env.name, key) or '{}')
    if record.get('agent'):
        row = runtime.get_agent(env.conn, env.name, record['agent'])
        if row and agent_process.counts(row, alive):
            return 'pending', 'The existing workflow scope review is running.'
        if record.get('fingerprint') == fingerprint:
            if 'verdict' not in record:
                record['verdict'], record['reason'] = readygate.ended_verdict(row, readygate.answer_text(env.run_dir(record['agent'])))
                runtime.kv_set(env.conn, env.name, key, json.dumps(record))
            return record['verdict'], record.get('reason', '')
    if bees.drain_refusal():
        return 'pending', 'Workflow scope review waits for update admission.'
    if free_mb / 1024 < loop._min_ram(env):
        return 'pending', 'Workflow scope review waits for available memory.'
    prompt = ('Review this proposed repair workflow, not implementation completion. Read only the '
              'referenced requirements, applicable decisions and relevant entrypoint. Do its checkpoints '
              'cover the whole required product execution, including success and required failure/recovery? '
              'Also assess whether the selected repair advances this workflow using its criteria and prerequisites. '
              'Verify that the workflow command is executable in this checkout, including module targets and dependencies; '
              'do not approve invented runners or mistake missing-command errors for a red product baseline. '
              'Reject a helper or prerequisite presented as the whole workflow, even when coverage lists '
              'every requirement. Respect superseding decisions and already accepted work. Do not repair '
              'code or run product processes. End with READY-CHECK: PASSED or READY-CHECK: FAILED, followed '
              'by a concrete missing execution boundary and requirement ID if failed.\n'
              + instruction(ref) + '\nProposed repair and complete workflow: ' + json.dumps(proposal, ensure_ascii=False)
              + '\nDecisions: bzz decisions list --project ' + json.dumps(env.name))
    request = dict(plan=plan, cwd=str(cwd), prompt=prompt, package='WORKFLOW-CHECK:' + package)
    with db.transaction(env.conn):
        # Another scheduler may have registered a checker since our initial read.
        latest = json.loads(runtime.kv_get(env.conn, env.name, key) or '{}')
        if latest != record:
            return 'pending', 'A concurrent workflow review will be observed next round.'
        name, reason = readygate._launch_check_agent(env, {'name': 'workflow'}, request)
        if name:
            runtime.kv_set(env.conn, env.name, key, json.dumps(dict(fingerprint=fingerprint, agent=name)))
    return 'pending', reason or 'Workflow scope review started.'
