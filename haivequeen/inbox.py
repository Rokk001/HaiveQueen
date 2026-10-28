"""Inbox folder of a project: <workspace>/.haivequeen/inbox/*.toml carry answers and decisions that the Queen takes over each round."""
import re
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

from . import db, runtime

WHO = ("owner", "coordinator")
ANSWER_KEYS = {"question_id", "text", "by"}
DECISION_KEYS = {"text", "source", "plan", "item"}


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


def parse_file(path: Path, project: str, conn) -> tuple:
    """Parse and check one inbox file without writing anything; return (answers, decisions) as lists of argument dicts.

    Raises InboxError for a parse error, an unknown table or key, a wrong value, an unknown or already answered question
    (or one of another project) and a question answered twice in the same file.
    """
    try:
        raw = path.read_bytes().decode("utf-8")
        data = tomllib.loads(raw)
    except UnicodeDecodeError as exc:
        raise InboxError("not UTF-8: %s" % exc)
    except tomllib.TOMLDecodeError as exc:
        raise InboxError("TOML parse error: %s" % exc)
    for key in data:
        if key not in ("answer", "decision"):
            raise InboxError("unknown top-level entry '%s' (only [[answer]] and [[decision]] are allowed)" % key)
    answers, decisions, seen = [], [], set()
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
    if not answers and not decisions:
        raise InboxError("the file has no [[answer]] and no [[decision]]")
    return answers, decisions


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

    Answers and decisions are stored through db.answer_question and runtime.add_decision, the same functions as `bzz answer` and
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
            answers, decisions = parse_file(path, env.name, env.conn)
            for a in answers:
                db.answer_question(env.conn, a["qid"], a["text"], a["by"])
            for d in decisions:
                runtime.add_decision(env.conn, env.name, d["source"], d["text"], d["plan"], d["item"])
        except (InboxError, KeyError, ValueError, runtime.CmdError) as exc:
            failed = _move(path, folder / "failed", path.name)
            failed.with_name(failed.stem + ".error.txt").write_text("%s\n" % exc, encoding="utf-8")
            env.event("warning inbox file %s rejected: %s" % (path.name, exc), error=True)
            result["failed"].append(path.name)
            continue
        _move(path, folder / "done", datetime.now().strftime("%Y%m%d-%H%M%S-") + path.name)
        env.log("inbox file %s taken over: %d answer(s), %d decision(s)" % (path.name, len(answers), len(decisions)))
        result["done"].append(path.name)
    return result
