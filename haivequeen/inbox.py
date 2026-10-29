"""Inbox folder of a project: <workspace>/.haivequeen/inbox/*.toml carry answers, decisions and queue orders that the Queen takes over each round."""
import re
import shutil
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from . import db, runtime

WHO = ("owner", "coordinator")
ANSWER_KEYS = {"question_id", "text", "by"}
DECISION_KEYS = {"text", "source", "plan", "item"}
QUEUE_KEYS = {"branch", "tip", "action", "reason"}
QUEUE_ACTIONS = ("withdraw", "mark_cleanup", "requeue")
OPEN_STATUSES = ("ready", "blocked")   # queue rows an order may close or re-queue; a row in a run or already integrated is never touched
CLEANUP_PREFIX = "branch-cleanup:"   # kv key of the cleanup mark of a branch


class InboxError(Exception):
    """An inbox file is not valid; the message names the reason (with the line where one is known)."""


def inbox_dir(workspace) -> Path:
    """Folder where writers drop their *.toml files for a workspace."""
    return Path(workspace) / ".haivequeen" / "inbox"


def _header_lines(text: str, table: str) -> list:
    """Line numbers (1-based) of the [[table]] headers in the file text, in file order."""
    return [n for n, line in enumerate(text.splitlines(), 1) if re.match(r"^\s*\[\[\s*%s\s*\]\]" % re.escape(table), line)]


def _plain_text(entry: dict, key: str, where: str, required: bool = True):
    """Return a string value of an entry; raise InboxError when it is missing or empty (if required) or not a string."""
    val = entry.get(key)
    if val is None:
        if required:
            raise InboxError("%s: %s is missing" % (where, key))
        return None
    if not isinstance(val, str) or (required and not val.strip()):
        raise InboxError("%s: %s must be a non-empty text" % (where, key))
    return val


def is_ancestor(repo, tip: str, ref: str) -> bool:
    """True when commit `tip` is contained in `ref` (git merge-base --is-ancestor); raises InboxError when git cannot tell."""
    try:
        res = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", tip, ref], capture_output=True, text=True)
    except OSError as exc:
        raise InboxError("git cannot be run: %s" % exc)
    if res.returncode not in (0, 1):
        raise InboxError("git merge-base --is-ancestor %s %s failed: %s" % (tip, ref, res.stderr.strip()))
    return res.returncode == 0


def _queue_order(entry: dict, where: str, project: str, conn, repo, ref: str) -> dict:
    """Check one [[queue]] entry against the queue and git without writing anything; return {action, branch, reason, ids}.

    withdraw needs the tip of every selected row to be in `ref` unless the reason is exactly "ueberholt"; requeue selects the newest open row only.
    """
    extra = set(entry) - QUEUE_KEYS
    if extra:
        raise InboxError("%s: unknown key %s" % (where, ", ".join(sorted(extra))))
    branch, reason = _plain_text(entry, "branch", where), _plain_text(entry, "reason", where)
    tip = _plain_text(entry, "tip", where, False)
    action = _plain_text(entry, "action", where)
    if action not in QUEUE_ACTIONS:
        raise InboxError("%s: action must be withdraw, mark_cleanup or requeue, not '%s'" % (where, action))
    rows = list(conn.execute("SELECT id, tip, status FROM queue WHERE project=? AND branch=? ORDER BY id", (project, branch)))
    if tip:
        rows = [r for r in rows if r["tip"] and (r["tip"].startswith(tip) or tip.startswith(r["tip"]))]
    if not rows:
        raise InboxError("%s: no queue row of branch %s%s" % (where, branch, " with tip " + tip if tip else ""))
    if action == "mark_cleanup":
        return {"action": action, "branch": branch, "reason": reason, "ids": [r["id"] for r in rows]}
    rows = [r for r in rows if r["status"] in OPEN_STATUSES]
    if not rows:
        raise InboxError("%s: branch %s has no ready or blocked row%s" % (where, branch, " with tip " + tip if tip else ""))
    if action == "requeue":
        return {"action": action, "branch": branch, "reason": reason, "ids": [rows[-1]["id"]]}
    if reason.strip().lower() != "ueberholt":
        for r in rows:
            if not r["tip"]:
                raise InboxError("%s: row %d of branch %s has no tip to check against %s" % (where, r["id"], branch, ref))
            if not is_ancestor(repo, r["tip"], ref):
                raise InboxError("%s: tip %s of branch %s is not in %s and the reason is not 'ueberholt'" % (where, r["tip"], branch, ref))
    return {"action": action, "branch": branch, "reason": reason, "ids": [r["id"] for r in rows]}


def apply_queue_order(conn, project: str, order: dict) -> None:
    """Carry out a checked queue order: withdraw (status withdrawn, note keeps the old status word and the reason), mark_cleanup (kv mark) or requeue (status ready)."""
    if order["action"] == "mark_cleanup":
        runtime.kv_set(conn, project, CLEANUP_PREFIX + order["branch"], "erledigt, Aufraeumen erlaubt: " + order["reason"])
        return
    with db.transaction(conn):
        for qid in order["ids"]:
            if order["action"] == "withdraw":
                old = conn.execute("SELECT status FROM queue WHERE id=?", (qid,)).fetchone()["status"]
                conn.execute("UPDATE queue SET status='withdrawn', note=?, updated_at=? WHERE id=?", ("%s (%s)" % (old, order["reason"]), db.now(), qid))
            else:
                conn.execute("UPDATE queue SET status='ready', note=?, updated_at=? WHERE id=?", ("requeued: " + order["reason"], db.now(), qid))


def parse_file(path: Path, project: str, conn, repo=None, ref: str = "origin/integration/all") -> tuple:
    """Parse and check one inbox file without writing anything; return (answers, decisions, queue orders) as lists of argument dicts.

    Raises InboxError for a parse error, an unknown table or key, a wrong value, an unknown or already answered question
    (or one of another project), a question answered twice in the same file and a queue order that cannot be carried out.
    `repo` and `ref` are the git checkout and the integration ref that a withdraw order is checked against.
    """
    try:
        raw = path.read_bytes().decode("utf-8")
        data = tomllib.loads(raw)
    except UnicodeDecodeError as exc:
        raise InboxError("not UTF-8: %s" % exc)
    except tomllib.TOMLDecodeError as exc:
        raise InboxError("TOML parse error: %s" % exc)
    for key in data:
        if key not in ("answer", "decision", "queue"):
            raise InboxError("unknown top-level entry '%s' (only [[answer]], [[decision]] and [[queue]] are allowed)" % key)
    answers, decisions, orders, seen = [], [], [], set()
    a_lines, d_lines = _header_lines(raw, "answer"), _header_lines(raw, "decision")
    for i, entry in enumerate(data.get("answer", [])):
        where = "[[answer]] at line %s" % (a_lines[i] if i < len(a_lines) else "?")
        if not isinstance(entry, dict):
            raise InboxError("%s: answer must be a table" % where)
        extra = set(entry) - ANSWER_KEYS
        if extra:
            raise InboxError("%s: unknown key %s" % (where, ", ".join(sorted(extra))))
        qid = entry.get("question_id")
        if isinstance(qid, bool) or not isinstance(qid, int):
            raise InboxError("%s: question_id must be a whole number" % where)
        text, by = _plain_text(entry, "text", where), _plain_text(entry, "by", where)
        if by not in WHO:
            raise InboxError("%s: by must be owner or coordinator, not '%s'" % (where, by))
        row = conn.execute("SELECT project, status FROM questions WHERE id=?", (qid,)).fetchone()
        if row is None or row["project"] != project:
            raise InboxError("%s: unknown question %d" % (where, qid))
        if row["status"] != "open" or qid in seen:
            raise InboxError("%s: question %d is already answered" % (where, qid))
        seen.add(qid)
        answers.append({"qid": qid, "text": text, "by": by})
    for i, entry in enumerate(data.get("decision", [])):
        where = "[[decision]] at line %s" % (d_lines[i] if i < len(d_lines) else "?")
        if not isinstance(entry, dict):
            raise InboxError("%s: decision must be a table" % where)
        extra = set(entry) - DECISION_KEYS
        if extra:
            raise InboxError("%s: unknown key %s" % (where, ", ".join(sorted(extra))))
        text, source = _plain_text(entry, "text", where), _plain_text(entry, "source", where)
        if source not in WHO:
            raise InboxError("%s: source must be owner or coordinator, not '%s'" % (where, source))
        decisions.append({"text": text, "source": source, "plan": _plain_text(entry, "plan", where, False),
                          "item": _plain_text(entry, "item", where, False)})
    q_lines = _header_lines(raw, "queue")
    for i, entry in enumerate(data.get("queue", [])):
        where = "[[queue]] at line %s" % (q_lines[i] if i < len(q_lines) else "?")
        if not isinstance(entry, dict):
            raise InboxError("%s: queue order must be a table" % where)
        orders.append(_queue_order(entry, where, project, conn, repo, ref))
    if not answers and not decisions and not orders:
        raise InboxError("the file has no [[answer]], no [[decision]] and no [[queue]]")
    return answers, decisions, orders


def _move(path: Path, folder: Path, name: str) -> Path:
    """Move a file into folder under name (a timestamp is put in front when that name is taken) and return the new path."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists():
        target = folder / (datetime.now().strftime("%Y%m%d-%H%M%S-") + name)
    shutil.move(str(path), str(target))
    return target


def process_inbox(env) -> dict:
    """Take over every *.toml file in the inbox of the project, oldest first; return {done: [names], failed: [names]}.

    Queue orders are checked first and carried out after the answers and decisions. Answers and decisions are stored through db.answer_question and runtime.add_decision, the same functions as `bzz answer` and
    `bzz decisions add`. A file is taken over completely or not at all (checked first); it then moves to done/<YYYYMMDD-HHMMSS>-<name>.toml.
    An invalid file moves to failed/ with <name>.error.txt next to it and a warning event. Other file endings (.tmp) are ignored.
    """
    result = {"done": [], "failed": []}
    folder = inbox_dir(env.workspace)
    if not folder.is_dir():
        return result
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix == ".toml"), key=lambda p: (p.stat().st_mtime, p.name))
    for path in files:
        try:
            answers, decisions, orders = parse_file(path, env.name, env.conn, env.settings.get("codeRepo") or env.settings.get("repo") or env.workspace,
                                                    "origin/" + str(env.settings.get("integrationBranch") or "integration/all"))
            for a in answers:
                db.answer_question(env.conn, a["qid"], a["text"], a["by"])
            for d in decisions:
                runtime.add_decision(env.conn, env.name, d["source"], d["text"], d["plan"], d["item"])
            for o in orders:
                apply_queue_order(env.conn, env.name, o)
                env.log("inbox queue order %s: branch %s, rows %s (%s)" % (o["action"], o["branch"], o["ids"], o["reason"]))
        except (InboxError, KeyError, ValueError, runtime.CmdError) as exc:
            failed = _move(path, folder / "failed", path.name)
            failed.with_name(failed.stem + ".error.txt").write_text("%s\n" % exc, encoding="utf-8")
            env.event("warning inbox file %s rejected: %s" % (path.name, exc), error=True)
            result["failed"].append(path.name)
            continue
        _move(path, folder / "done", datetime.now().strftime("%Y%m%d-%H%M%S-") + path.name)
        env.log("inbox file %s taken over: %d answer(s), %d decision(s), %d queue order(s)" % (path.name, len(answers), len(decisions), len(orders)))
        result["done"].append(path.name)
    return result
