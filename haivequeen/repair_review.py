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
    return size_instructions() + roles.role_text("diagnosis", {"output_path": path, "commit": commit,
        "failure_log": failure_log, "decisions": decision_preview,
        "history": history_preview, "decision_ids": json.dumps([d['id'] for d in known])})


def observations(review):
    """Ignore formatting/action wording when comparing the claimed retry basis."""
    return sorted(set(' '.join(item.split()).casefold() for item in review.get('evidence', [])))


def failure_report_prompt(path, commit, failure_log, known, project="", plan=""):
    preview, sources = context(known, [], project, plan)
    return size_instructions() + ('\nIf unable to repair, avoid another agent repeating your investigation: write %s '
            'as compact JSON with kind (technical/product/permission/external), commit=%s, '
            'failure_log=%s, reviewed_decisions (all IDs below), evidence (specific observations), '
            'analysis. For technical include next_action and why_new; an optional repair_class '
            'change requires class_reason. For other kinds include question, why_owner and '
            'recommendation. Do not claim changed code is this original commit.\nACTIVE DECISIONS: %s\n' %
            (path, json.dumps(commit), json.dumps(failure_log),
             json.dumps([d['id'] for d in known]) + '\nPreviews: ' + preview + sources))


# Separate structured instructions from supporting observations. Read bounded bytes
# even if a file grows after stat; formatting is not semantic context.
REPORT_BYTES = 256 * 1024
CORE_BYTES = 64 * 1024
EVIDENCE_BYTES = 128 * 1024


def size_instructions():
    return ('Diagnosis budgets: UTF-8 file at most %s bytes; compact JSON excluding '
            'top-level evidence at most %s bytes; evidence at most %s bytes. '
            'Keep observations concise with file/log references; do not copy histories. '
            'Preserve all required contract fields, decisions and workflow coverage. '
            'Measure UTF-8 bytes before submitting; never truncate JSON or drop requirements. '
            % (REPORT_BYTES, CORE_BYTES, EVIDENCE_BYTES))


def read_document(path):
    with path.open('rb') as stream:
        raw = stream.read(REPORT_BYTES + 1)
    if len(raw) > REPORT_BYTES:
        raise ValueError('diagnosis file exceeds %s bytes. ' % REPORT_BYTES + size_instructions())
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('diagnosis must be an object')
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    core = {k: v for k, v in data.items() if k != 'evidence'}
    for label, value, budget in (('structured report', core, CORE_BYTES),
                                  ('evidence', data.get('evidence'), EVIDENCE_BYTES)):
        actual = size(value)
        if actual > budget:
            largest = sorted(((k, size(v)) for k, v in core.items()), key=lambda x: x[1], reverse=True)[:3]
            raise ValueError('diagnosis %s uses %s bytes; limit %s. Largest structured fields: %s. '
                             % (label, actual, budget, largest) + size_instructions())
    return data


def read(path, commit, failure_log, known, *, bound_failure=False):
    data = read_document(path)
    if not isinstance(data, dict):
        raise ValueError('diagnosis must be an object')
    # Only a scheduler-bound package run can obtain this opaque identity from HQ.
    # Commit and reviewed decisions remain explicit claims validated below.
    if bound_failure:
        data['failure_log'] = failure_log
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


def reject(state, scope, reason, agent):
    """Allow one protocol correction per evidence scope, never an endless review loop."""
    rejection = state.get('review_rejection', {})
    count = rejection.get('count', 0) if rejection.get('scope') == scope else 0
    state['review_rejection'] = {'scope': scope, 'count': count + 1, 'reason': reason, 'agent': agent}
    if count == 0:
        return ''
    return ('The configured diagnosis provider returned no valid structured diagnosis after a correction attempt. '
            'The project repair is not verified and its retry guard remains active. '
            'Please check the diagnosis provider configuration or arrange a HaiveQueen protocol repair, '
            'then confirm when the prerequisite is restored. '
            'Rejected report from agent %s: %s. The report and execution logs remain in that agent run directory.'
            % (agent, reason))


def correction_prompt(previous_run, reason):
    """Keep original observations available when only the receipt needs correction."""
    return size_instructions() + ('\nPROTOCOL CORRECTION, SAME EVIDENCE\n'
            'Read the prior diagnosis at %s and its execution logs in %s. '
            'Reuse its valid observations and correct this validation error: %s. '
            'Do not repeat the investigation solely to repair the report format. '
            'Inspect additional evidence only where missing, contradictory or stale. '
            'Write the corrected report to your own output path above; preserve the original.\n'
            % (previous_run / 'repair-diagnosis.json', previous_run, reason))
