"""Triage of locked packages: technical points are decided automatically, game/balance points go to the owner."""
from . import db, runtime
from .runtime import CmdError

TRIAGE_PHASE = "triage"
TRIAGE_CLASS = "medium"


def parse_points(lock_reason) -> list:
    """Split a lock reason into points: one per line, 'item: text' (item without blanks) or an automatic id pN."""
    points = []
    for n, line in enumerate((lock_reason or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        head, sep, tail = line.partition(":")
        if sep and head.strip() and " " not in head.strip() and tail.strip():
            points.append({"item": head.strip(), "text": tail.strip()})
        else:
            points.append({"item": f"p{n}", "text": line})
    return points or [{"item": "p1", "text": "locked without a reason"}]


def _points(conn, project, plan, package) -> list:
    """All points of a package (from its lock reason); raises CmdError if the package is unknown."""
    row = conn.execute("SELECT lock_reason FROM packages WHERE project=? AND plan_path=? AND id=?",
                       (project, plan, package)).fetchone()
    if row is None:
        raise CmdError(f"unknown package {plan} {package}")
    return parse_points(row["lock_reason"])


def _decided_items(conn, project, plan) -> set:
    """Items of the plan that already have a decision."""
    return {d["item"] for d in runtime.list_decisions(conn, project, plan) if d["item"]}


def _undecided_points(conn, project, plan, package) -> list:
    """Points of a package without a decision (open questions still count as undecided)."""
    decided = _decided_items(conn, project, plan)
    return [p for p in _points(conn, project, plan, package) if p["item"] not in decided]


def _open_points(conn, project, plan, package) -> list:
    """Points of a package that have neither a decision nor an open question."""
    asked = {q["item"] for q in runtime.list_questions(conn, project, "open", plan) if q["item"]}
    return [p for p in _undecided_points(conn, project, plan, package) if p["item"] not in asked]


def _triage_running(conn, project, plan, package) -> bool:
    """True if a triage agent of this package is starting or running."""
    return any(a["plan_path"] == plan and a["package"] == package and a["phase"] == TRIAGE_PHASE
               and a["status"] in runtime.ACTIVE_STATUSES
               for a in runtime.list_agents(conn, project, "active"))


def locked_packages(conn, project) -> list:
    """Locked packages with open points and no running triage agent: [{plan, id, title, lock_reason, points}]."""
    out = []
    for pkg in runtime.list_packages(conn, project):
        if not pkg["locked"] or _triage_running(conn, project, pkg["plan_path"], pkg["id"]):
            continue
        points = _open_points(conn, project, pkg["plan_path"], pkg["id"])
        if points:
            out.append({"plan": pkg["plan_path"], "id": pkg["id"], "title": pkg["title"],
                        "lock_reason": pkg["lock_reason"], "points": points})
    return out


def triage_prompt(project, plan_path, package, points, python_path) -> str:
    """Build the prompt of the triage agent (class medium) for the open points of one locked package."""
    orch = f'"{python_path}" -I haivequeen/cli.py'
    lines = "\n".join(f"- {p['item']}: {p['text']}" for p in points)
    return f"""You are the triage agent for project {project}, plan {plan_path}, package {package}.
The package is locked. Sort every open point below into exactly one of two kinds.

Open points:
{lines}

Kind "technical": documentation, tests, code structure, or a value that follows from the rules of the plan.
Decide it yourself from the plan and the code. Read them first; never ask about something the code or plan answers.
Record it with:
  {orch} triage decide --project {project} --plan {plan_path} --package {package} --item <item> --text "<decision>" --reason "<why>" --source "<file or plan rule>"

Kind "game/balance": game mechanics, rules, balance values, level design, look and feel, or anything the plan does not settle.
Send it to the owner with:
  {orch} triage ask --project {project} --plan {plan_path} --package {package} --item <item> --question "<bare question>" --recommendation "<your recommendation>"

In doubt it is game/balance.
Handle every point once. Report only through these bzz commands; do not edit plan or state files, and do not unlock the package yourself (decide does that after the last point).
"""


def decide(conn, project, plan_path, package, item, text, reason, source_ref) -> dict:
    """Record an automatic decision for one point and unlock the package once no point lacks a decision."""
    if not (reason or "").strip() or not (source_ref or "").strip():
        raise CmdError("a decision needs a reason and a source")
    if item not in {p["item"] for p in _undecided_points(conn, project, plan_path, package)}:
        raise CmdError(f"point {item} of {plan_path} {package} is unknown or already decided")
    full = f"{text} (reason: {reason}; source: {source_ref})"
    did = runtime.add_decision(conn, project, "auto", full, plan_path, item)
    remaining = _undecided_points(conn, project, plan_path, package)
    if not remaining:
        runtime.set_package(conn, project, plan_path, package, locked=False, reason="")
    return {"ok": True, "id": did, "unlocked": not remaining, "remaining": [p["item"] for p in remaining]}


def escalate(conn, project, plan_path, package, item, question, recommendation="") -> dict:
    """Ask the owner about a game/balance point; the package stays locked until the question is answered."""
    if item not in {p["item"] for p in _open_points(conn, project, plan_path, package)}:
        raise CmdError(f"point {item} of {plan_path} {package} is unknown, decided or already asked")
    qid = db.add_question(conn, project, plan_path, item, question, recommendation)
    return {"ok": True, "question_id": qid, "answered_by": "owner", "locked": True}


# ---- questions asked by agents (bzz ask) ----

QUESTION_PKG = "QUESTION-%d"   # package name of the triage agent of one question


def _known_answer(conn, project, plan, item):
    """The stored answer that already settles `item` of `plan`: ('question', id, text) of an answered question, or ('decision', id, text) of an active
    decision of the plan or without a plan. None when there is none (or no item)."""
    if not item:
        return None
    for q in reversed(runtime.list_questions(conn, project, "answered", plan, item)):
        return ("question", q["id"], q["answer"] or "")
    for d in reversed(runtime.list_decisions(conn, project, None, item, active_only=True)):
        if d["plan_path"] in (None, "", plan):
            return ("decision", d["id"], d["text"] or "")
    return None


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


def enabled(conn, project) -> bool:
    """True when the triage is on for the project (setting triage, default on); an unreadable setting counts as on."""
    from . import config, scheduler as sch
    try:
        env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == project), None)
        return bool(env.settings.get("triage", True)) if env else True
    except Exception:
        return True


def intake(conn, project, plan_path, item, question, recommendation="") -> dict:
    """`bzz ask`: a new question first waits for triage; one that a stored decision or answered question already settles is closed right away.
    With the triage switched off nothing waits for it: the question goes straight to the owner."""
    if not enabled(conn, project):
        return {"ok": True, "id": db.add_question(conn, project, plan_path, item, question, recommendation), "status": "open"}
    qid = db.add_question(conn, project, plan_path, item, question, recommendation, status="triage")
    hit = _known_answer(conn, project, plan_path, item)
    if hit:
        return _close_with_reference(conn, qid, *hit)
    return {"ok": True, "id": qid, "status": "triage"}


def close_by_reference(conn, project, qid, kind, ref_id, reason) -> dict:
    """Triage agent: the question is settled by an existing decision or answered question (checked to exist in the project)."""
    if kind == "decision":
        ref = next((d for d in runtime.list_decisions(conn, project, active_only=True) if d["id"] == ref_id), None)
        text = ref["text"] if ref else None
    else:
        ref = next((q for q in runtime.list_questions(conn, project, "answered") if q["id"] == ref_id), None)
        text = ref["answer"] if ref else None
    if ref is None:
        raise CmdError(f"{kind} {ref_id} is unknown or not active in {project}")
    return _close_with_reference(conn, qid, kind, ref_id, "%s; %s" % (reason, text or ""))


def answer_asked(conn, project, qid, text, reason, source_ref) -> dict:
    """Triage agent: a technical question is answered by the Queen (answered_by auto, reason and source recorded); the asking package goes on."""
    if not (reason or "").strip() or not (source_ref or "").strip():
        raise CmdError("an answer needs a reason and a source")
    with db.transaction(conn):
        n = conn.execute("UPDATE questions SET status='open' WHERE id=? AND project=? AND status='triage'", (qid, project)).rowcount
    if not n:
        raise CmdError(f"question {qid} is not waiting for triage")
    return db.answer_question(conn, qid, f"{text} (reason: {reason}; source: {source_ref})", "auto")


def release_asked(conn, project, qid) -> dict:
    """Triage agent: a game/balance/design question stays open for the owner."""
    with db.transaction(conn):
        row = conn.execute("SELECT question FROM questions WHERE id=? AND project=? AND status='triage'", (qid, project)).fetchone()
        if row is None:
            raise CmdError(f"question {qid} is not waiting for triage")
        conn.execute("UPDATE questions SET status='open' WHERE id=?", (qid,))
        db.add_event(conn, project, "question", row["question"], data={"question_id": qid})
    return {"ok": True, "id": qid, "status": "open", "answered_by": "owner"}


def questions_to_triage(conn, project) -> list:
    """Questions waiting for triage without a running triage agent."""
    busy = {a["package"] for a in runtime.list_agents(conn, project, "active") if a["phase"] == TRIAGE_PHASE}
    return [q for q in runtime.list_questions(conn, project, "triage") if QUESTION_PKG % q["id"] not in busy]


def question_prompt(conn, project, q, python_path) -> str:
    """Prompt of the triage agent of one new question: first the stored decisions and answered questions, then technical or game/balance."""
    orch = f'"{python_path}" -I haivequeen/cli.py'
    decs = "\n".join("- decision %s: %s" % (d["id"], (d["text"] or "")[:220]) for d in runtime.list_decisions(conn, project, active_only=True)[-80:])
    ans = "\n".join("- question %s (%s): %s -> %s" % (a["id"], a["item"] or "", (a["question"] or "")[:120], (a["answer"] or "")[:160])
                    for a in runtime.list_questions(conn, project, "answered")[-40:])
    return f"""You are the triage agent for project {project}. An agent asked this question (id {q['id']}, plan {q['plan_path'] or '-'}, item {q['item'] or '-'}):
{q['question']}
Recommendation of the asking agent: {q['recommendation'] or '-'}

Step 1. Search the stored decisions and answered questions below. If one of them already answers the question, close it with a reference:
  {orch} triage close --project {project} --question-id {q['id']} --ref decision|question --ref-id <id> --reason "<why it answers this>"

Step 2. Otherwise sort it into exactly one kind.
Kind "technical": documentation, tests, code structure, or a value that follows from the plan or the code. Read them, then answer it yourself:
  {orch} triage answer --project {project} --question-id {q['id']} --text "<answer>" --reason "<why>" --source "<file or plan rule>"
Kind "game/balance/design": mechanics, rules, balance values, level design, look and feel, or anything the plan does not settle. Leave it for the owner:
  {orch} triage release --project {project} --question-id {q['id']}
In doubt it goes to the owner.
Handle the question once. Do not edit plan or state files.

Stored decisions:
{decs or '(none)'}

Answered questions:
{ans or '(none)'}
"""


# ---- command line ----

def register(sub) -> None:
    """Add the 'triage' commands to the sub-parser collection of haivequeen/cli.py."""
    from . import runtime_cli as cli
    PL = ("--plan", {"required": True})
    PK = ("--package", {"required": True})
    IT = ("--item", {"required": True})

    def h_list(a, c):
        """triage list: locked packages that need triage."""
        return locked_packages(c, a.project)

    def h_prompt(a, c):
        """triage prompt: prompt text for one package (the open points are read from the database)."""
        points = _open_points(c, a.project, a.plan, a.package)
        return {"prompt": triage_prompt(a.project, a.plan, a.package, points, a.python or "python"),
                "points": points}

    def h_decide(a, c):
        """triage decide: record an automatic decision."""
        return decide(c, a.project, a.plan, a.package, a.item, cli.text_arg(a.text), a.reason, a.source)

    def h_ask(a, c):
        """triage ask: escalate a point to the owner."""
        return escalate(c, a.project, a.plan, a.package, a.item, cli.text_arg(a.question), a.recommendation)

    QI = ("--question-id", {"required": True, "type": int})

    def h_close(a, c):
        """triage close: the question is settled by an existing decision or answered question."""
        return close_by_reference(c, a.project, a.question_id, a.ref, a.ref_id, a.reason)

    def h_answer(a, c):
        """triage answer: the Queen answers a technical question."""
        return answer_asked(c, a.project, a.question_id, cli.text_arg(a.text), a.reason, a.source)

    def h_release(a, c):
        """triage release: the question goes to the owner."""
        return release_asked(c, a.project, a.question_id)

    cli.actions(sub, "triage", {
        "close": (h_close, [cli.P, QI, ("--ref", {"required": True, "choices": ["decision", "question"]}),
                            ("--ref-id", {"required": True, "type": int}), ("--reason", {"required": True})]),
        "answer": (h_answer, [cli.P, QI, ("--text", {"required": True}), ("--reason", {"required": True}), ("--source", {"required": True})]),
        "release": (h_release, [cli.P, QI]),
        "list": (h_list, [cli.P]),
        "prompt": (h_prompt, [cli.P, PL, PK, ("--python", {})]),
        "decide": (h_decide, [cli.P, PL, PK, IT, ("--text", {"required": True}), ("--reason", {"required": True}),
                              ("--source", {"required": True})]),
        "ask": (h_ask, [cli.P, PL, PK, IT, ("--question", {"required": True}), ("--recommendation", {"default": ""})]),
    })
