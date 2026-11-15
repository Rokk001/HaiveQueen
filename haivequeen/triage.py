"""Questions of the agents (bzz ask): one whose item an earlier decision or answered question already settles is closed with a reference, so the owner is not asked twice."""
from . import db, runtime
from .runtime import CmdError


def _known_answer(conn, project, plan, item):
    """The stored answer that already settles `item` of `plan`: ('question', id, text) of an answered question, or ('decision', id, text) of an active
    decision of the plan or without a plan. None when there is none (or no item)."""
    if not item or item == "owner-check" or item.startswith("owner-check-"):
        return None
    # Automatic duplicate references carry no new instruction. Their later
    # timestamps must never outrank the actual owner/coordinator decision.
    questions = [q for q in runtime.list_questions(conn, project, "answered", plan, item)
                 if q["answered_by"] in ("owner", "coordinator")]
    candidates = [(q["answered_at"] or q["created_at"] or "", 0, q["id"],
                   ("question", q["id"], q["answer"] or "")) for q in questions]
    for d in runtime.list_decisions(conn, project, None, item, active_only=True):
        if d["plan_path"] not in (None, "", plan):
            continue
        # answer_question also records the same answer as a decision. Keep
        # the question reference for that mirror, but rank independent
        # decisions ahead when the timestamp has only second precision.
        mirror = next((q for q in questions
                       if d["ts"] == q["answered_at"]
                       and d["text"] == f"{q['question']} -> {q['answer']}"), None)
        reference = (("question", mirror["id"], mirror["answer"] or "") if mirror
                     else ("decision", d["id"], d["text"] or ""))
        candidates.append((d["ts"] or "", 1, d["id"], reference))
    return max(candidates)[3] if candidates else None



def _close_with_reference(conn, qid, kind, ref_id, text) -> dict:
    """Close a question in triage: answered by auto with a reference to the decision or question that settles it (no new decision)."""
    answer = "Duplicate: already settled by %s %s (%s)" % (kind, ref_id, text[:300])
    with db.transaction(conn):
        row = conn.execute("SELECT project, status FROM questions WHERE id=?", (qid,)).fetchone()
        if row is None or row["status"] != "triage":
            raise CmdError(f"question {qid} is not waiting for triage")
        conn.execute("UPDATE questions SET status='answered', answer=?, answered_by='auto', answered_at=? WHERE id=?", (answer, db.now(), qid))
        db.add_event(conn, row["project"], "answer", answer, data={"question_id": qid, "by": "auto"})
    return {"ok": True, "id": qid, "status": "answered", "answered_by": "auto", "answer": answer}


def owner_check(item):
    return item == 'owner-check' or str(item or '').startswith('owner-check-')


def _scope(env, question):
    import hashlib
    import json
    from . import bees, integration, repair_review
    pair = bees.plan_worktree(env, question['plan_path']) if question['plan_path'] else None
    folder = pair[1] if pair else env.code_repo
    known = repair_review.decisions(env.conn, env.name, question['plan_path'])
    identity = hashlib.sha256(json.dumps([question['question'], question['recommendation'], known],
                                       sort_keys=True).encode()).hexdigest()
    return folder, known, {'commit': integration.head_commit(folder), 'failure_log': 'question:' + identity}


def advance(env, free_mb, alive=None):
    """Review undecided agent questions, leaving product choices with the owner."""
    import json
    from . import agent_process, bees, hives, loop, platform, repair_review, scheduler
    conn = env.conn
    for question in runtime.list_questions(conn, env.name, status='triage'):
        plan, qid = question['plan_path'], question['id']
        key = 'question-review:' + str(qid)
        state = json.loads(runtime.kv_get(conn, env.name, key) or '{}')
        agents = runtime.list_agents(conn, None, 'all')
        worker = next((a for a in agents if a['project'] == env.name and a['name'] == state.get('agent')), None)
        if worker and agent_process.counts(worker, alive or platform.process_alive):
            continue
        if known_answer := _known_answer(conn, env.name, plan, question['item']):
            _close_with_reference(conn, qid, *known_answer)
            continue
        folder, known, current = _scope(env, question)
        if state.get('agent'):
            try:
                if current != state['scope']:
                    # New evidence is not a malformed provider response.
                    state = {'starts': state.get('starts', 0)}
                else:
                    report = repair_review.read(env.run_dir(state['agent']) / 'question-diagnosis.json',
                                                current['commit'], current['failure_log'], known)
                    with db.transaction(conn):
                        if report['kind'] == 'technical':
                            answer = '\n\n'.join((report['analysis'], report['next_action'], report['why_new'],
                                                    '\n'.join(report['evidence'])))
                            # A diagnosis is an instruction, never successful verification.
                            conn.execute("UPDATE questions SET status='open' WHERE id=? AND status='triage'", (qid,))
                            db.answer_question(conn, qid, answer, 'coordinator')
                        else:
                            text = '\n\n'.join((report['question'], report['why_owner'], report['analysis'],
                                                  '\n'.join(report['evidence'])))
                            conn.execute("UPDATE questions SET status='open',question=?,recommendation=? WHERE id=? AND status='triage'",
                                         (text, report['recommendation'], qid))
                            db.add_event(conn, env.name, 'question', text, data={'question_id': qid})
                    runtime.kv_set(conn, env.name, key, json.dumps({'report': report, 'phase': 'completed'}))
                    continue
            except (OSError, ValueError, KeyError) as exc:
                message = repair_review.reject(state, current, str(exc), state['agent'])
                state.update(agent=None, feedback=str(exc))
                if message:
                    with db.transaction(conn):
                        conn.execute("UPDATE questions SET status='open',question=?,recommendation=? WHERE id=? AND status='triage'",
                                     (message, 'Restore valid diagnosis output; do not bypass verification.', qid))
                        db.add_event(conn, env.name, 'question', message, data={'question_id': qid})
                runtime.kv_set(conn, env.name, key, json.dumps(state))
                continue
        if bees.drain_refusal() or free_mb / 1024 < loop._min_ram(env):
            continue
        plan_row = conn.execute('SELECT status FROM plans WHERE project=? AND path=?', (env.name, plan)).fetchone()
        if plan_row and plan_row[0] in ('CANCELLED', 'DONE'):
            continue
        if hives.start_block_reason(conn, env.name, plan):
            continue
        running = [{**a, 'status': 'running'} for a in agents if agent_process.counts(a, alive or platform.process_alive)]
        free = scheduler.provider_free(env.providers, running, conn)
        providers = [{**p, 'use': 'preferred'} for p in env.providers
                     if p['name'] not in env.settings.get('excludedProviders', [])
                     and 'Coding' in scheduler.provider_capabilities(p) and not scheduler.reserve_zero(p, conn)]
        cls = env.settings.get('diagnosisClass', 'medium')
        pick = scheduler.select_provider(providers, cls, [], free, conn=conn)
        if not pick or not folder:
            continue
        names = {a['name'] for a in agents if a['project'] == env.name}
        serial = state.get('starts', 0) + 1
        name = 'question-review-%s-%s' % (qid, serial)
        while name in names:
            serial += 1
            name = 'question-review-%s-%s' % (qid, serial)
        text = repair_review.prompt(env.run_dir(name) / 'question-diagnosis.json', current['commit'],
                                    current['failure_log'], known, [], project=env.name, plan=plan or '')
        text += ('\nReview this pending agent question before owner escalation. Its failure_log is an identity, not a file. '
                 'Read source evidence only as needed; technical answers instruct the Queen/Agent and do not mark work done. '
                 'Honor owner-reserved topics. Check the actual conflict, not just an earlier failure description.\n' +
                 json.dumps({'id': qid, 'plan': plan, 'item': question['item'],
                             'question': question['question'][:4000], 'recommendation': question['recommendation'][:1000],
                             'owner_topics': env.settings.get('ownerTopics', []), 'feedback': state.get('feedback', '')}) +
                 '\nFull question: bzz questions --project ' + json.dumps(env.name) +
                 (' --plan ' + json.dumps(plan) if plan else '') +
                 (' --item ' + json.dumps(question['item']) if question['item'] else '') + '\n')
        start = {'names': [a['name'] for a in agents if a['project'] == env.name], 'busy': [],
                 'plan_status': {scheduler.norm_plan(plan or ''): 'RUNNING'},
                 'by_path': {scheduler.norm_plan(plan or ''): {'path': plan or ''}}}
        ctx = {'env': env, 'state': start, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'settle': 0}
        action = {'name': name, 'type': pick['name'], 'phase': 'fix', 'plan': plan or '', 'package': '',
                  'cwd': str(folder), 'class': cls, 'prompt': text}
        if loop._do_start(ctx, action, forced_pick=pick):
            state.update(agent=start['names'][-1], scope=current, starts=serial)
            runtime.kv_set(conn, env.name, key, json.dumps(state))
