"""Owner question listing and answer service."""
import re

from . import bees, db, dashboard_data, runtime
from .i18n import tr

REFRESH_MS = 5000


# Describe one question as a list line.
def line_text(q) -> str:
    """One list line of a question: id, Project, Plan and item."""
    return "#%s  %s | %s | %s" % (q["id"], q["project"], dashboard_data.hive_name(q["plan_path"] or ""), q["item"] or "")


# Read the questions that wait for an answer.
def open_questions(conn) -> list:
    """The open questions of all Projects, oldest first."""
    return runtime.list_questions(conn, status="open")


# Read the steps of a plan.
def plan_packages(conn, project, plan_path) -> list:
    """Ids of the packages of a plan in file order (empty when the plan has none)."""
    return [r[0] for r in conn.execute("SELECT id FROM packages WHERE project=? AND plan_path=? ORDER BY rowid", (project, plan_path))]


# Store the owner's answer and mark the rejected steps.
def answer(conn, qid, text, rejected=(), apiary=False) -> None:
    """Answer an ordinary question and mark rejected packages; owner-check text is only a note.

    Empty text raises ValueError; unknown or closed questions raise KeyError before packages change."""
    text = (text or "").strip()
    if not text:
        raise ValueError(tr("Write an answer first."))
    row = conn.execute("SELECT project, plan_path, item FROM questions WHERE id=?", (qid,)).fetchone()
    db.answer_question(conn, qid, text, "owner", apiary=bool(apiary))
    if row['item'] == 'owner-check' or str(row['item'] or '').startswith('owner-check-'):
        return  # Free text is a note; package rejection requires an explicit decision.
    for package in rejected:
        bees.set_package_state(conn, row["project"], row["plan_path"], package, "rejected", "owner")


# The text of a refused answer.
def error_text(exc) -> str:
    """The message of a refused answer in the language; the database functions raise it in English."""
    text = str(exc).strip("'\"")
    found = re.fullmatch(r"(?:question (\S+) is already answered)|(?:unknown question (\S+))", text)
    if not found:
        return tr(text)
    return tr("question %s is already answered", found.group(1)) if found.group(1) else tr("unknown question %s", found.group(2))
