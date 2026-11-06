"""Questions of the agents (bzz ask): one whose item an earlier decision or answered question already settles is closed with a reference, so the owner is not asked twice."""
from . import db, runtime
from .runtime import CmdError


def _known_answer(conn, project, plan, item):
    """The stored answer that already settles `item` of `plan`: ('question', id, text) of an answered question, or ('decision', id, text) of an active
    decision of the plan or without a plan. None when there is none (or no item)."""
    if not item or item == "owner-check" or item.startswith("owner-check-"):
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
