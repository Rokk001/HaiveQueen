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

    cli.actions(sub, "triage", {
        "list": (h_list, [cli.P]),
        "prompt": (h_prompt, [cli.P, PL, PK, ("--python", {})]),
        "decide": (h_decide, [cli.P, PL, PK, IT, ("--text", {"required": True}), ("--reason", {"required": True}),
                              ("--source", {"required": True})]),
        "ask": (h_ask, [cli.P, PL, PK, IT, ("--question", {"required": True}), ("--recommendation", {"default": ""})]),
    })
