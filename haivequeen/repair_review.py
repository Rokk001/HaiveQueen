"""Structured agent diagnosis; Python validates evidence, not product meaning."""
import json

from . import runtime, roles


def decisions(conn, project, plan):
    return [d for d in runtime.list_decisions(conn, project, active_only=True)
            if not d.get('plan_path') or d['plan_path'] == plan]


def context(known, history, project, plan):
    """Bounded previews, with existing read-only CLI sources for full evidence."""
    decision_preview = [{'id': d['id'], 'text': str(d.get('text', ''))[:200]} for d in known[-8:]]
    selected = list(history[-3:])
    last_report = next((e for e in reversed(history) if e.get('report')), None)
    if last_report is not None and last_report not in selected:
        selected.insert(0, last_report)
    recent = [{k: str(e[k])[:350] for k in ('event', 'step', 'agent', 'log_file', 'report') if k in e}
              for e in selected]
    sources = ('\nPreviews may be incomplete. Read only the relevant source records/sections; '
               'do not reload entire plans or all logs by default. Before an owner question, '
               'check applicable active decisions. Full existing sources (read-only):\n'
               'bzz decisions list --project %s\n'
               'bzz kv get --project %s --key %s\n' %
               (json.dumps(project), json.dumps(project), json.dumps('orch-repair-history:' + plan)))
    return json.dumps(decision_preview), json.dumps(recent) + sources


def prompt(path, commit, failure_log, known, history, project='', plan=''):
    decision_preview, history_preview = context(known, history, project, plan)
    return roles.role_text("diagnosis", {"output_path": path, "commit": commit,
        "failure_log": failure_log, "decisions": decision_preview,
        "history": history_preview, "decision_ids": json.dumps([d['id'] for d in known])})


def failure_report_prompt(path, commit, failure_log, known, project="", plan=""):
    preview, sources = context(known, [], project, plan)
    return ('\nIf unable to repair, avoid another agent repeating your investigation: write %s '
            'as compact JSON with kind (technical/product/permission/external), commit=%s, '
            'failure_log=%s, reviewed_decisions (all IDs below), evidence (specific observations), '
            'analysis. For technical include next_action and why_new; an optional repair_class '
            'change requires class_reason. For other kinds include question, why_owner and '
            'recommendation. Do not claim changed code is this original commit.\nACTIVE DECISIONS: %s\n' %
            (path, json.dumps(commit), json.dumps(failure_log),
             json.dumps([d['id'] for d in known]) + '\nPreviews: ' + preview + sources))


def read(path, commit, failure_log, known):
    if path.stat().st_size > 65536:
        raise ValueError('diagnosis exceeds 64 KiB')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('diagnosis must be an object')
    if data.get('commit') != commit or data.get('failure_log') != failure_log:
        raise ValueError('diagnosis does not refer to the current commit and failed run')
    if data.get('reviewed_decisions') != [d['id'] for d in known]:
        raise ValueError('review all current decisions in their supplied order')
    kind = data.get('kind')
    if 'repair_class' in data and data['repair_class'] not in ('light', 'medium', 'heavy'):
        raise ValueError('repair_class must be light, medium or heavy')
    if kind not in ('technical', 'product', 'permission', 'external'):
        raise ValueError('diagnosis must classify responsibility')
    fields = ('analysis', 'next_action', 'why_new') if kind == 'technical' else (
        'analysis', 'question', 'why_owner', 'recommendation')
    if any(not isinstance(data.get(k), str) or not data[k].strip() for k in fields):
        raise ValueError('diagnosis lacks concrete findings or next action')
    evidence = data.get('evidence')
    if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
        raise ValueError('diagnosis lacks observed evidence')
    return data
