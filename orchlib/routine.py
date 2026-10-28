"""Routine part of an orchestrator round: everything that needs no language model is decided here by script.

Ports routine.ps1 one to one onto the database. `get_routine` returns deterministic actions (same format as the brain's) and the events
that need the brain. A `ProjectEnv` (see scheduler.py) carries the connection, the project name, the settings and the run folders.
"""
import hashlib
import json
import os
import re
from datetime import datetime

from . import db, runtime
from . import scheduler as sch
from .scheduler import ACTIVE, norm_plan

QUEUE_PLAN_ID = "integration-queue"   # pseudo plan name of the fix tasks for blocked queue rows that name no plan
WORDS = {"ready": "ready", "in_run": "in Lauf", "blocked": "blockiert", "integrated": "integriert"}   # status words the routine and the brain know
HANDOFF_TAIL = "Prüfe nach jedem"   # start of the handoff sentence of an agent prompt (cut off when a follow-up agent reuses the task)


# ---- snapshot helpers ----

def view(row: dict) -> dict:
    """Agent row as the routine reads it: the database columns plus plan, bundle (list) and started (start time plus row id, so rows
    started in the same second keep their creation order when compared as text)."""
    d = dict(row)
    d["plan"] = d.get("plan_path") or ""
    d["bundle"] = json.loads(d["bundle_json"]) if d.get("bundle_json") else []
    d["started"] = "%s#%09d" % (d.get("started_at") or "", d.get("id") or 0)
    return d


def all_agents(env) -> list:
    """All agent rows of the project (archived and active) as routine views, oldest first."""
    return [view(r) for r in env.agents("all")]


def plan_rows(env) -> list:
    """Plans of the project after a fresh read of the plan files (rows with path, title, status); plans whose file is gone are left out."""
    res = runtime.sync_plans(env.conn, env.name, str(env.workspace))
    return [p for p in res["plans"] if (env.workspace / p["path"]).is_file()]


def queue_rows(env) -> dict:
    """Integration queue rows, the last row per branch: branch -> {branch, tip (9 characters), plan, status word, note, date}."""
    rows = {}
    for r in sorted(db.queue_rows(env.conn, env.name), key=lambda x: x["id"]):
        tip = str(r["tip"] or "")
        rows[str(r["branch"])] = {"branch": str(r["branch"]), "tip": tip[:9], "plan": str(r["plan_path"] or ""),
                                  "status": WORDS.get(r["status"], r["status"]), "note": str(r["note"] or ""),
                                  "date": str(r["updated_at"] or "")}
    return rows


def question_rows(env) -> list:
    """Questions of the project as rows with id (Q-<number>), plan, item, status (OPEN or ANSWERED) and answer."""
    return [{"id": "Q-%s" % q["id"], "plan": str(q["plan_path"] or ""), "item": str(q["item"] or ""),
             "status": str(q["status"]).upper(), "answer": str(q["answer"] or "")} for q in runtime.list_questions(env.conn, env.name)]


def handled_map(env) -> dict:
    """Event keys the brain already handled: key -> value (a retry counter) or the time."""
    return {r["key"]: str(r["value"] or r["ts"] or "") for r in db.rows(env.conn, "SELECT key, ts, value FROM handled WHERE project=?", (env.name,))}


def set_handled(env, key: str, value: str = None) -> None:
    """Remember an event key as handled (the optional value is a retry counter)."""
    runtime.handled_add(env.conn, env.name, key, value or None)


def remove_handled(env, key: str) -> None:
    """Forget a handled key (the event is asked again in the next round)."""
    runtime.handled_remove(env.conn, env.name, key)


def frozen_keys(env) -> list:
    """Frozen plan|package keys of the project (lower case)."""
    return [r["key"].lower() for r in db.rows(env.conn, "SELECT key FROM nofollow WHERE project=?", (env.name,))]


def freeze_key(plan: str, pkg: str) -> str:
    """Key of a package for the freeze list: plan file name (lower case) and package id (lower case)."""
    return norm_plan(plan).split("/")[-1] + "|" + str(pkg).lower()


def is_frozen(env, plan: str, pkg: str) -> bool:
    """True when the package must not get any follow-up task (it is on the nofollow list)."""
    return freeze_key(plan, pkg) in frozen_keys(env)


def blocked_tasks(agents: list) -> list:
    """Tasks (plan|package|phase) whose last 2 starts failed with the same error: blocked, never restarted. Returns dicts Key, Name, Error, Plan, Package, Phase."""
    res = []
    groups = {}
    for a in agents:
        if a.get("status") == "failed":
            groups.setdefault("%s|%s|%s" % (a["plan"], a.get("package"), a.get("phase")), []).append(a)
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r["started"])
        a, b = rows[-1], rows[-2]
        last_a, last_b = str(a.get("last_line") or ""), str(b.get("last_line") or "")
        # a pane that never came up says nothing about the task, so it never blocks it
        if last_a == last_b and not re.match(r"^pane never started", last_a):
            res.append({"Key": key, "Name": str(a.get("name")), "Error": last_a, "Plan": a["plan"],
                        "Package": str(a.get("package")), "Phase": str(a.get("phase"))})
    return res


def repair_stale_agents(env, now: datetime = None, alive=None) -> None:
    """Mark running or starting agents whose runner process is gone as failed, so slots and the dashboard stay correct."""
    from . import platform as osplat
    alive = alive or osplat.process_alive
    now = now or datetime.now()
    for a in env.agents("active"):
        if a.get("status") not in ACTIVE:
            continue
        why = None
        if a.get("pid"):
            if not alive(a["pid"]):
                why = "runner process vanished (ended by executor or crashed)"
        elif a.get("started_at"):
            try:
                if (now - datetime.fromisoformat(a["started_at"])).total_seconds() > 120:
                    why = "pane never started (no runner process)"
            except ValueError:
                pass
        if why:
            env.update_agent(a["name"], {"status": "failed", "ended_at": db.now(), "last_line": why, "paneClosed": True})
            env.log("TECHNICAL NOTE: %s: %s, marked failed" % (a["name"], why))


# ---- final answers of verify agents ----

def read_final(env, name: str) -> str:
    """Text of the final answer file of an agent run ('' when missing)."""
    try:
        return (env.run_dir(name) / "final.txt").read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def verify_zero(env, name: str) -> bool:
    """True when the LAST 'Offene Anforderungen: N' line of the final answer says 0 (lines after it do not matter) and no partial item is listed."""
    t = read_final(env, name)
    return bool(t) and verify_open_count(env, name) == 0 and not re.search(r"(?im)^\s*[-*|].*teilweise", t)


def verify_open_count(env, name: str) -> int:
    """Number of open requirements named at the end of a verify answer; -1 when the answer is missing or has no such line."""
    found = re.findall(r"Offene Anforderungen[^:\r\n]*:\s*(\d+)", read_final(env, name))
    return int(found[-1]) if found else -1


def final_tail(env, name: str) -> str:
    """The last 1800 characters of a verify answer, for the brain."""
    t = read_final(env, name)
    return t[-1800:] if len(t) > 1800 else t


def lock_reason(text: str) -> str:
    """Lock reason (one open point per line) from the tail of a verify answer: bullet marks removed, the closing count line dropped."""
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^[\s\-*|]+", "", ln).strip().rstrip("|").strip()
        if ln and not re.match(r"^Offene Anforderungen", ln):
            lines.append(ln)
    return "\n".join(lines)[:600]


# ---- packages: stage, failures, lock, class ----

def pkg_stage(env, agents: list, plan: str, pkg: str) -> dict:
    """Stage of one package from the agent history: new, running, needVerify, verified, open (verify found items), failed, handoff.

    Returns {Stage, Work, Verify}. The whole-plan stage (ALL) also counts the follow-up work of the plan (Q-* and FIX-*).
    """
    mine = [a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and (
        str(a.get("package")) == pkg or pkg in a["bundle"] or (pkg == "ALL" and re.match(r"^(Q|FIX)-", str(a.get("package") or ""))))]
    if any(a.get("status") in ACTIVE for a in mine):
        return {"Stage": "running", "Work": None, "Verify": None}
    work = sorted([a for a in mine if a.get("phase") == "work"], key=lambda a: a["started"])
    ver = sorted([a for a in mine if a.get("phase") == "verify"], key=lambda a: a["started"])
    w = work[-1] if work else None
    v = ver[-1] if ver else None
    latest = sorted(mine, key=lambda a: a["started"])
    if latest and latest[-1].get("status") == "done" and (env.run_dir(latest[-1]["name"]) / "HANDOFF").exists():
        return {"Stage": "handoff", "Work": w, "Verify": v}   # ended with a handoff: its -cN continuation is started by the routine
    if not w and not v:
        return {"Stage": "new", "Work": None, "Verify": None}
    if w and w.get("status") == "failed" and (not v or v["started"] < w["started"]):
        return {"Stage": "failed", "Work": w, "Verify": v}
    if w and (not v or v["started"] < w["started"]):
        return {"Stage": "needVerify", "Work": w, "Verify": v}
    if v and v.get("status") == "failed":
        return {"Stage": "failed", "Work": w, "Verify": v}
    if v and v.get("status") == "done":
        return {"Stage": "verified" if verify_zero(env, v["name"]) else "open", "Work": w, "Verify": v}
    return {"Stage": "new", "Work": w, "Verify": v}


def package_failures(env, agents: list, plan: str, pkg: str) -> int:
    """Failures of one package: failed work agents (not 'pane never started') plus verifies that reported open items."""
    mine = [a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and (str(a.get("package")) == pkg or pkg in a["bundle"])]
    f = sum(1 for a in mine if a.get("phase") == "work" and a.get("status") == "failed"
            and not re.match(r"^pane never started", str(a.get("last_line") or "")))
    o = 0
    for v in (a for a in mine if a.get("phase") == "verify" and a.get("status") == "done"):
        m = re.search(r"Offene Anforderungen:\s*(\d+)", read_final(env, v["name"]))
        if m and int(m.group(1)) > 0:
            o += 1
    return f + o


def released_failures(env, plan: str, pkg: str) -> int:
    """Failures that were already forgiven when a lock of this package was released (triage decision or answer); 0 when never released."""
    try:
        return int(runtime.kv_get(env.conn, env.name, "lockrel:%s|%s" % (plan, pkg)) or 0)
    except ValueError:
        return 0


def lock_limit(env, plan: str, pkg: str) -> int:
    """Failure count above which the package is locked: escalateAfter plus the failures forgiven by an earlier release."""
    return int(env.settings.get("escalateAfter", 2)) + released_failures(env, plan, pkg)


def package_locked(env, plan: str, pkg: str, agents: list = None) -> bool:
    """True when the last agent of the package was a verify and its failures exceed the lock limit (work on it is locked)."""
    if re.match(r"^(Q|FIX)-", pkg):
        return False
    agents = agents if agents is not None else [view(r) for r in env.agents("active")]
    mine = sorted([a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and (str(a.get("package")) == pkg or pkg in a["bundle"])],
                  key=lambda a: a["started"])
    if not mine or mine[-1].get("phase") != "verify":
        return False
    return package_failures(env, agents, plan, pkg) > lock_limit(env, plan, pkg)


def start_class(env, phase: str, plan: str, pkg: str, sent: str = "") -> str:
    """Class of a work start, decided centrally: the class of the package row in the plan table (else the class sent), raised by escalation, then the phase minimum."""
    base = sch.class_index(sent)
    failures = 0
    if phase == "work":
        row = next((p for p in env.packages(plan) if p["id"] == pkg), None)
        if row:
            base = sch.class_index(row["cls"])   # the table class is the base: a class sent with the action may already be escalated
        failures = package_failures(env, [view(r) for r in env.agents("active")], plan, pkg)
    return sch.resolve_agent_class(env.settings, phase, sch.CLASS_NAMES[base], failures)


def package_needs_for(env, plan: str, pkg: str) -> list:
    """Capabilities the package of the plan needs (such work goes only to providers that have them)."""
    row = next((p for p in env.packages(plan) if p["id"] == pkg), None)
    return sch.package_needs(env.settings, row["content"], row["capability"]) if row else []


# ---- done detection, decisions ----

def state_done_packages(env, rel: str, pkg_ids: list, rows: list) -> dict:
    """Packages the packages table or the integration queue already show as finished: id -> evidence.

    Sources: packages of the plan with phase done; queue rows (any status except blocked) whose note says 'Paket ALL' or names the package.
    """
    res = {}
    for p in env.packages(rel):
        if p["phase"] == "done" and p["id"] in pkg_ids:
            res[p["id"]] = "packages table: done"
    rel_n = norm_plan(rel)
    for r in rows:
        if re.match(r"^(blockiert|verworfen|ersetzt)", str(r["status"])):
            continue
        pl = norm_plan(r["plan"]).lstrip("./")
        if not pl or not rel_n.endswith(pl):
            continue
        note = str(r["note"])
        if re.search(r"(?i)Pakete?\s+ALL\b", note):
            for i in pkg_ids:
                res.setdefault(i, "queue %s: %s" % (r["branch"], r["status"]))
        else:
            for i in pkg_ids:
                if re.search(r"(?i)Pakete?\s+(?:[A-Za-z]+\d+\s*[-,]\s*)*" + re.escape(i) + r"\b", note):
                    res.setdefault(i, "queue %s: %s" % (r["branch"], r["status"]))
    return res


# Words of a decision that closes a package: 'done', 'closed', 'erledigt', 'abgeschlossen', 'no further runs' / 'keine weiteren Laeufe'.
DONE_WORDS = re.compile(r"(?i)(?<!not )(?<!nicht )\b(erledigt|done|closed|abgeschlossen)\b|keine\s+weiteren|no\s+further")


def _about_plan(row_plan, rel: str) -> bool:
    """True when a decision or question belongs to this plan, or to no plan at all."""
    return not row_plan or norm_plan(row_plan) == norm_plan(rel)


# Words by which the text of a question says that a package is locked (a lock question whose item has no LOCK- prefix).
LOCK_QUESTION_WORDS = re.compile(r"(?i)(gesperrt|locked)")


def lock_resolution(env, plan: str, pkg: str):
    """Why the lock of a package counts as resolved, or None: a decision for item LOCK-<package> (source owner, coordinator or auto),
    an answered question with that item, or with item "<package> <title>" when its text says the package is gesperrt/locked (answered by owner,
    coordinator or auto), or a decision that closes the package."""
    tag = "LOCK-" + pkg.upper()
    for d in runtime.list_decisions(env.conn, env.name):
        if _about_plan(d["plan_path"], plan) and str(d["item"] or "").strip().upper() == tag:
            return "decision %s by %s" % (d["id"], d["source"])
    for q in runtime.list_questions(env.conn, env.name, "answered"):
        if _about_plan(q["plan_path"], plan) and (str(q["item"] or "").strip().upper() == tag or (
                item_token(q["item"]) == pkg.upper() and LOCK_QUESTION_WORDS.search(str(q["question"] or "")))):
            return "question %s answered by %s" % (q["id"], q["answered_by"])
    return done_decisions(env, plan, [pkg]).get(pkg)


def done_decisions(env, rel: str, pkg_ids: list) -> dict:
    """Packages a decision (or an answer) closes: id -> evidence. The text must say the package is done (or that no further runs follow)
    and name the package (item LOCK-<package>, <package> or "<package> <title>"; a decision without an item names it as a word in the text)."""
    res = {}
    texts = [("decision %s" % d["id"], d["plan_path"], d["item"], d["text"]) for d in runtime.list_decisions(env.conn, env.name)]
    texts += [("question %s" % q["id"], q["plan_path"], q["item"], q["answer"]) for q in runtime.list_questions(env.conn, env.name, "answered")]
    for ev, plan, item, text in texts:
        if not _about_plan(plan, rel) or not DONE_WORDS.search(str(text or "")):
            continue
        for i in pkg_ids:
            it = str(item or "").strip().upper()
            named = it in (i.upper(), "LOCK-" + i.upper()) or item_token(item) in (i.upper(), "LOCK-" + i.upper()) or (not it and re.search(
                r"(?<![A-Za-z0-9-])" + re.escape(i) + r"(?![A-Za-z0-9])", str(text)))
            if named:
                res.setdefault(i, ev)
    return res


def item_token(item: str) -> str:
    """Short id of a question item ('ALL-V5 mood decay' -> 'ALL-V5', 'M1' -> 'M1')."""
    m = re.match(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", str(item or "").strip())
    return m.group(0).upper() if m else ""


def decided_items(env, questions: list) -> dict:
    """Item ids already decided or asked: 'plan|ID' (or '*|ID' for a decision without a plan) from decisions, plus 'plan|ID' of every known question."""
    res = {}
    for d in runtime.list_decisions(env.conn, env.name):
        tok = item_token(d["item"])
        if tok:
            res[(norm_plan(d["plan_path"]) + "|" + tok) if d["plan_path"] else "*|" + tok] = "decisions"
    for q in questions:
        res[norm_plan(q["plan"]) + "|" + item_token(q["item"])] = "questions " + q["id"]
    return res


def item_decided(decided: dict, plan: str, item: str):
    """Source (decisions, or questions and the id) when the item of this plan is already decided or asked, else None."""
    tok = item_token(item)
    if not tok:
        return None
    return decided.get(norm_plan(plan) + "|" + tok) or decided.get("*|" + tok)


def answer_text(env, token: str) -> str:
    """Answer text of the item (at most 1200 characters): the answers of the answered questions of that item, else its decisions."""
    if not token:
        return ""
    parts = [str(q["answer"]).strip() for q in runtime.list_questions(env.conn, env.name, "answered")
             if item_token(q["item"]) == token.upper() and str(q["answer"] or "").strip()]
    if not parts:
        parts = [str(d["text"]).strip() for d in runtime.list_decisions(env.conn, env.name)
                 if item_token(d["item"]) == token.upper() and str(d["text"] or "").strip()]
    return " ".join(parts)[:1200]


# ---- names, prompts, actions ----

def plan_root(pattern: str) -> str:
    """Folder part of a plan glob up to the first wildcard component ('docs/plans/**/*.md' -> 'docs/plans')."""
    parts = []
    for comp in pattern.replace("\\", "/").split("/")[:-1]:
        if any(ch in comp for ch in "*?["):
            break
        parts.append(comp)
    return "/".join(parts)


def plan_tag(env, plan: str) -> str:
    """Short plan name for agent names: the plan's folder in upper case; a stand-alone plan file (directly in a plan root, or matching
    planStandaloneRegex) uses its own file stem without the plan suffix."""
    p = str(plan).replace("\\", "/")
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    stem = p.rsplit("/", 1)[-1]
    rx = env.settings.get("planStandaloneRegex") or ""
    if rx:
        m = re.search(rx, stem)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    roots = {plan_root(g) for g in env.plan_globs}
    if not parent or parent in roots:
        return re.sub(env.settings.get("planSuffixRegex") or "-?PLAN$", "", os.path.splitext(stem)[0]).upper()
    return parent.rsplit("/", 1)[-1].upper()


def new_agent_name(base: str, agents: list, chosen: list) -> str:
    """Unique agent name: adds -2, -3 ... when the name was used before (also in the archive) or is already chosen."""
    n, k = base, 1
    while any(a.get("name") == n for a in agents) or n in chosen:
        k += 1
        n = "%s-%d" % (base, k)
    return n


def verify_prompt(plan: str, pkg: str) -> str:
    """The verify question for one package (or the whole plan with package ALL)."""
    what = "(alle Pakete)" if pkg == "ALL" else "(Paket %s)" % pkg
    return ("Wurden alle Tasks aus dem Plan %s %s umgesetzt? Gibt es Anforderungen, welche nicht umgesetzt wurden? Liste jede nicht oder nur "
            "teilweise umgesetzte Anforderung mit Beleg (Datei:Zeile oder Commit) und schliesse mit 'Offene Anforderungen: <Anzahl>'. "
            "Schreib die Antwort in die State-Datei. Aendere nichts am Code. Follow all rules in section 1 of AGENT-RULES.md." % (plan, what))


def start_action(name, provider, phase, plan, pkg, cwd, prompt, graphics=False, cls="medium", bundle=()) -> dict:
    """Build one start_agent action; `provider` is the preferred provider name."""
    return {"action": "start_agent", "name": name, "type": provider, "phase": phase, "plan": plan, "package": pkg, "cwd": cwd,
            "prompt": prompt, "graphics": bool(graphics), "class": cls, "bundle": list(bundle)}


def base_values(env) -> dict:
    """Placeholder values every prompt template gets."""
    s = env.settings
    return {"owner": s.get("owner", "Owner"), "integrationBranch": s.get("integrationBranch", "main"), "compileCheck": s.get("compileCheck", ""),
            "queueFile": "the integration queue", "queueName": "integration queue", "integratorInstructions": s.get("integratorInstructions", ""),
            "buildFlowDoc": s.get("buildFlowDoc", ""), "project": env.name, "runsDir": str(env.run_dir("")).replace("\\", "/")}


def prompt(env, name: str, values: dict) -> str:
    """Expanded prompt template of the project."""
    return sch.prompt_text(env.settings, name, values, base_values(env))


def new_worktree(env, middle: str) -> str:
    """Folder of a new worktree next to the workspace: <parent>/<worktreePrefix>-<middle>."""
    return os.path.join(os.path.dirname(str(env.workspace).rstrip("\\/")), "%s-%s" % (env.settings.get("worktreePrefix", "wt"), middle))


# ---- handoff (rule 13) ----

def set_handoff_flags(env, agents: list) -> list:
    """Write the flag file runs/<name>/HANDOFF for every running agent whose context reached contextLimitTokens (the agent is never killed).

    Returns [{Name, Ctx}] of the newly flagged agents.
    """
    limit = int(env.settings.get("contextLimitTokens") or 300000)
    res = []
    for a in agents:
        ctx = a.get("ctxTokens")
        if a.get("status") in ACTIVE and ctx is not None and int(ctx) >= limit:
            f = env.run_dir(a["name"]) / "HANDOFF"
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("context %s reached %s\n" % (ctx, datetime.now().isoformat(timespec="seconds")), encoding="utf-8")
                res.append({"Name": str(a["name"]), "Ctx": int(ctx)})
    return res


def handoff_continuations(env, all_rows: list, free: dict, workers: list, taken: list) -> list:
    """Fresh continuation agents (-c2, -c3 ...) for agents that ended with a handoff; the task text comes from the prompt file of the ended agent.

    `workers` is a one-element list holding the worker count (updated in place).
    """
    acts = []
    for a in (r for r in all_rows if r.get("status") == "done"):
        if not (env.run_dir(a["name"]) / "HANDOFF").exists():
            continue
        base = re.sub(r"-c\d+$", "", str(a["name"]))
        if any(re.match("^" + re.escape(base) + r"-c\d+$", str(r.get("name"))) and r["started"] > a["started"] for r in all_rows):
            continue
        is_int = a.get("phase") == "integrate"
        prov = sch.row_provider(a)
        if is_int:
            if int(free.get("int", 0)) <= 0:
                continue
        elif workers[0] <= 0 or int(free.get(prov, 0)) <= 0:
            continue
        body = ""
        pf = env.run_dir(a["name"]) / "prompt.md"
        if pf.is_file():
            t = pf.read_text(encoding="utf-8-sig").lstrip("﻿")
            i = t.find("TASK")
            if i >= 0:
                t = t[i + 4:].lstrip()
            j = t.rfind(HANDOFF_TAIL)
            if j > 0:
                t = t[:j].rstrip()
            body = t
        k = 2
        while any(r.get("name") == "%s-c%d" % (base, k) for r in all_rows) or "%s-c%d" % (base, k) in taken:
            k += 1
        nm = "%s-c%d" % (base, k)
        if is_int:
            free["int"] = int(free.get("int", 0)) - 1
        else:
            free[prov] = int(free.get(prov, 0)) - 1
            workers[0] -= 1
        acts.append(start_action(nm, prov, str(a.get("phase")), a["plan"], str(a.get("package") or ""), str(a.get("cwd") or ""),
                                 body + "\n\nLies zuerst 'Handoff " + str(a["name"]) + "' in der State-Datei des Plans und mach von dort weiter.",
                                 False, str(a.get("class") or "medium"), a["bundle"]))
    return acts


# ---- the routine ----

def get_routine(env, ctx: dict) -> dict:
    """The routine decision of one round.

    `ctx`: plans (startable plans: path, status), queue (open integration rows), all_rows (every queue row), questions, can_start_integrator,
    free (free slots per provider name, plus workers and int). Returns {actions, events}: start_agent, lock_package, plan_done and ask_owner
    actions; events for the brain (type, key, plan, package, agent, text).
    """
    s = env.settings
    allr = all_agents(env)
    handled = handled_map(env)
    acts, events, chosen = [], [], []
    free = {str(k): int(v) for k, v in ctx["free"].items() if k != "workers"}   # free slots per provider name (and int = integrator slots)
    workers = [max(0, int(ctx["free"].get("workers", 0)))]
    blocked = blocked_tasks(allr)
    decided = decided_items(env, ctx["questions"])
    repo = str(env.workspace)
    providers = env.providers
    triage_on = bool(s.get("triage", True))

    def blocked_for(rel, pkg):
        """True when the task (plan, package) is on the blocked list."""
        return any(norm_plan(b["Plan"]) == norm_plan(rel) and b["Package"] == pkg for b in blocked)

    def select(cls, needs, **kw):
        """Provider for a task of this class (shared limits source of the project)."""
        return sch.select_provider(providers, cls, needs, free, conn=env.conn, limits=ctx.get("limits"), **kw)

    def exists(rel, pk):
        """True when a non-failed agent row with this package of the plan exists."""
        return any(norm_plan(a["plan"]) == norm_plan(rel) and str(a.get("package")) == pk and a.get("status") != "failed" for a in allr)

    # Priority 1 and 2 (before everything else takes a slot): integration fixes for blocked queue rows, then work for really answered questions.
    for r in [x for x in ctx["all_rows"] if str(x["status"]).startswith("blockiert")]:
        rp = next((p for p in ctx["plans"] if norm_plan(p["path"]) == norm_plan(r["plan"])
                   or norm_plan(r["plan"]).endswith(norm_plan(p["path"]))), None)
        no_plan = rp is None and re.match(r"(?i)^\s*(keiner?|none|n/?a|-+|–)?\s*$", str(r["plan"]))   # plan column 'keiner': the row text is the task
        if (rp is None and not no_plan) or workers[0] <= 0:
            continue
        tip = str(r["tip"])[:7]
        pk = "FIX-" + tip
        if no_plan:
            rel_p = QUEUE_PLAN_ID
            if exists(rel_p, pk) or blocked_for(rel_p, pk):
                continue
            pv = select("medium", [])
            if not pv:
                continue
            nm = new_agent_name(pk + "-work", allr, chosen)
            chosen.append(nm)
            free[pv["name"]] = free.get(pv["name"], 0) - 1
            workers[0] -= 1
            acts.append(start_action(nm, pv["name"], "work", rel_p, pk, repo, prompt(env, "fixBlockedNoPlan", {
                "branch": r["branch"], "tip": r["tip"], "status": (r["status"] + " " + r["note"]).strip(), "cwd": repo,
                "newWorktree": new_worktree(env, "fix-" + sch.to_slug(r["branch"]))})))
            continue
        rel_p = str(rp["path"])
        hist = sorted([a for a in allr if norm_plan(a["plan"]) == norm_plan(rel_p) and a.get("cwd")], key=lambda a: a["started"])
        if not hist:
            continue
        if exists(rel_p, pk) or blocked_for(rel_p, pk):   # a failed start is tried again (stops after two failures with the same error)
            continue
        pv = select("medium", [])
        if not pv:
            continue
        nm = new_agent_name("%s-%s-work" % (plan_tag(env, rel_p), pk), allr, chosen)
        chosen.append(nm)
        free[pv["name"]] = free.get(pv["name"], 0) - 1
        workers[0] -= 1
        acts.append(start_action(nm, pv["name"], "work", rel_p, pk, str(hist[-1]["cwd"]), prompt(env, "fixBlocked", {
            "plan": rel_p, "branch": r["branch"], "tip": r["tip"], "status": r["status"], "cwd": str(hist[-1]["cwd"])})))
    for q in [x for x in ctx["questions"] if x["status"] == "ANSWERED"]:
        if re.search(r"(?i)duplicate", str(q["answer"])):
            continue   # the coordinator marked it as a duplicate of an answered question
        rp = next((p for p in ctx["plans"] if norm_plan(p["path"]) == norm_plan(q["plan"])), None)
        if rp is None or workers[0] <= 0:
            continue
        rel_p = str(rp["path"])
        tok = item_token(q["item"])
        pk = "Q-" + tok
        if any(p["id"].upper() == tok and p["phase"] == "done" for p in env.packages(rel_p)):
            continue   # the item already stands as done in the packages table
        if exists(rel_p, pk):
            continue
        hist = sorted([a for a in allr if norm_plan(a["plan"]) == norm_plan(rel_p) and a.get("cwd")], key=lambda a: a["started"])
        if not hist:
            continue
        pv = select("medium", [])
        if not pv:
            continue
        nm = new_agent_name("%s-%s-work" % (plan_tag(env, rel_p), pk), allr, chosen)
        chosen.append(nm)
        free[pv["name"]] = free.get(pv["name"], 0) - 1
        workers[0] -= 1
        acts.append(start_action(nm, pv["name"], "work", rel_p, pk, str(hist[-1]["cwd"]), prompt(env, "answeredQuestion", {
            "plan": rel_p, "item": q["item"], "qid": q["id"], "answer": answer_text(env, tok), "cwd": str(hist[-1]["cwd"])})))

    for pl in ctx["plans"]:
        rel = str(pl["path"])
        tag = plan_tag(env, rel)
        pkgs = env.packages(rel)
        hist = sorted([a for a in allr if norm_plan(a["plan"]) == norm_plan(rel) and a.get("cwd")], key=lambda a: a["started"])
        cwd = str(hist[-1]["cwd"]) if hist else ""
        # New plan: the script cannot plan it (no package table or no known worktree) -> brain, once.
        if not pkgs or not cwd:
            if "newplan:" + rel not in handled:
                events.append({"type": "new_plan", "key": "newplan:" + rel, "plan": rel, "package": "", "agent": "",
                               "text": "Plan %s is %s and has %s: plan its packages (worktree, branch, first work agents)." % (
                                   rel, pl["status"], "no known worktree yet" if pkgs else "no readable package table")})
                continue
            if not pkgs:
                continue
            cwd = repo   # the brain was asked already: the routine starts the packages itself in the main checkout (never skip the plan for good)
        stages = {k["id"]: pkg_stage(env, allr, rel, k["id"]) for k in pkgs}
        # A package without agent history that the packages table or the queue already show as finished is never startable (stage stateDone).
        sdone = state_done_packages(env, rel, [k["id"] for k in pkgs], ctx["all_rows"])
        for k in pkgs:
            if stages[k["id"]]["Stage"] in ("new", "open") and k["id"] in sdone:
                stages[k["id"]] = {"Stage": "stateDone", "Work": None, "Verify": None, "Evidence": sdone[k["id"]]}
        # A decision or answer that closes a package ('done, no further runs') ends it for good: dependents start, no more work or verify runs.
        closed = done_decisions(env, rel, [k["id"] for k in pkgs])
        for k in pkgs:
            if stages[k["id"]]["Stage"] not in ("running", "verified", "stateDone") and k["id"] in closed:
                stages[k["id"]] = {"Stage": "stateDone", "Work": None, "Verify": None, "Evidence": closed[k["id"]]}
        # Frozen packages (nofollow list): no follow-up task of any kind.
        for k in pkgs:
            if stages[k["id"]]["Stage"] != "running" and is_frozen(env, rel, k["id"]):
                stages[k["id"]] = {"Stage": "stateDone", "Work": None, "Verify": None, "Evidence": "NOFOLLOW"}
        # Lock: a package whose verifications reported open items more often than the escalation allows gets no further work agent,
        # no event for the brain and no follow-up task; it is reported once as a lock with the open items.
        locked = {}
        for k in [p["id"] for p in pkgs] + ["ALL"]:
            lst = pkg_stage(env, allr, rel, "ALL") if k == "ALL" else stages[k]
            if lst["Stage"] == "open" and lst["Verify"] and package_failures(env, allr, rel, k) > lock_limit(env, rel, k):
                locked[k] = True
                acts.append({"action": "lock_package", "plan": rel, "package": k, "agent": str(lst["Verify"]["name"]),
                             "text": final_tail(env, str(lst["Verify"]["name"]))})
        # Events for the brain: failed agents, verify answers with open items.
        for k in pkgs:
            st = stages[k["id"]]
            if st["Stage"] == "failed" and not blocked_for(rel, k["id"]):
                ag = st["Verify"] if st["Verify"] and st["Verify"].get("status") == "failed" else st["Work"]
                if "failed:" + ag["name"] not in handled:
                    events.append({"type": "agent_failed", "key": "failed:" + ag["name"], "plan": rel, "package": k["id"], "agent": str(ag["name"]),
                                   "text": "Agent %s (%s) failed: %s" % (ag["name"], ag.get("phase"), ag.get("last_line"))})
            # A verify answer with open items for a package that is already finished creates no work task (the open items are only noted).
            if st["Stage"] == "open" and k["id"] not in locked and k["id"] not in sdone and "verify:" + st["Verify"]["name"] not in handled:
                events.append({"type": "verify_open", "key": "verify:" + st["Verify"]["name"], "plan": rel, "package": k["id"],
                               "agent": str(st["Verify"]["name"]), "text": final_tail(env, str(st["Verify"]["name"]))})
        # Verify after work.
        for k in pkgs:
            st = stages[k["id"]]
            retry = (st["Stage"] == "failed" and st["Verify"] and st["Verify"].get("status") == "failed" and st["Work"]
                     and st["Work"]["started"] < st["Verify"]["started"] and not blocked_for(rel, k["id"]))   # a verify that crashed is run again
            if (st["Stage"] != "needVerify" and not retry) or workers[0] <= 0:
                continue
            pv = select("medium", [], avoid=sch.row_provider(st["Work"]))   # verify never below medium; another provider than the work agent when one is free
            if not pv:
                continue
            nm = new_agent_name("%s-%s-verify" % (tag, k["id"]), allr, chosen)
            chosen.append(nm)
            free[pv["name"]] = free.get(pv["name"], 0) - 1
            workers[0] -= 1
            acts.append(start_action(nm, pv["name"], "verify", rel, k["id"], str(st["Work"]["cwd"]), verify_prompt(rel, k["id"])))
        # A verify answer without any count line (stage open, count -1) cannot be judged: the verify is run again, at most 3 verifies per package.
        for k in pkgs:
            st = stages[k["id"]]
            if (st["Stage"] != "open" or workers[0] <= 0 or not st["Verify"] or k["id"] in locked or k["id"] in sdone
                    or blocked_for(rel, k["id"]) or verify_open_count(env, str(st["Verify"]["name"])) != -1):
                continue
            done_v = sum(1 for a in allr if norm_plan(a["plan"]) == norm_plan(rel) and str(a.get("package")) == k["id"]
                         and a.get("phase") == "verify" and a.get("status") == "done")
            pv = select("medium", [], avoid=sch.row_provider(st["Verify"])) if done_v < 3 else None
            if not pv:
                continue
            nm = new_agent_name("%s-%s-verify" % (tag, k["id"]), allr, chosen)
            chosen.append(nm)
            free[pv["name"]] = free.get(pv["name"], 0) - 1
            workers[0] -= 1
            acts.append(start_action(nm, pv["name"], "verify", rel, k["id"], str((st["Work"] or st["Verify"]).get("cwd") or cwd), verify_prompt(rel, k["id"])))
        # Follow-up tasks derived from the STATE (found again after every restart of the loop): (1) a verify with open requirements and no
        # work after it -> a work agent for exactly those items (at most 3 rounds per package); (2) a work agent that failed technically
        # ('pane never started') -> started again (at most 3 times).
        for k in [p["id"] for p in pkgs] + ["ALL"]:
            if workers[0] <= 0:
                break
            st = pkg_stage(env, allr, rel, "ALL") if k == "ALL" else stages[k]
            if blocked_for(rel, k) or k in locked or is_frozen(env, rel, k):
                continue
            kmine = [a for a in allr if norm_plan(a["plan"]) == norm_plan(rel) and str(a.get("package")) == k]
            wcwd = str(st["Work"]["cwd"]) if st["Work"] and st["Work"].get("cwd") else cwd
            wtype = sch.row_provider(st["Work"]) if st["Work"] else ""
            text = ""
            if (st["Stage"] == "open" and st["Verify"] and verify_open_count(env, str(st["Verify"]["name"])) > 0
                    and sum(1 for a in kmine if a.get("phase") == "verify" and a.get("status") == "done") < 4):
                ans = str(env.run_dir(str(st["Verify"]["name"])) / "final.txt").replace("\\", "/")
                text = prompt(env, "verifyOpenWork", {"plan": rel, "pkg": k, "verifier": str(st["Verify"]["name"]), "answerPath": ans, "cwd": wcwd})
            elif (st["Stage"] == "failed" and st["Work"] and st["Work"].get("status") == "failed"
                  and re.match(r"^pane never started", str(st["Work"].get("last_line") or ""))
                  and (not st["Verify"] or st["Verify"]["started"] < st["Work"]["started"])
                  and sum(1 for a in kmine if a.get("phase") == "work" and a.get("status") == "failed"
                          and re.match(r"^pane never started", str(a.get("last_line") or ""))) < 3):
                text = prompt(env, "restartWork", {"plan": rel, "pkg": k, "previous": str(st["Work"]["name"]), "cwd": wcwd})
            if not text:
                continue
            kc = next((p for p in pkgs if p["id"] == k), None)
            kcls = sch.resolve_agent_class(s, "work", kc["cls"] if kc else "medium", package_failures(env, allr, rel, k))
            kneeds = sch.package_needs(s, kc["content"], kc["capability"]) if kc else []
            pv = select(kcls, kneeds, prefer=wtype)   # the follow-up stays with the provider of the earlier task when that one is a candidate
            if not pv:
                continue
            nm = new_agent_name("%s-%s-work" % (tag, k), allr, chosen)
            chosen.append(nm)
            free[pv["name"]] = free.get(pv["name"], 0) - 1
            workers[0] -= 1
            acts.append(start_action(nm, pv["name"], "work", rel, k, wcwd, text, bool(kneeds), kcls))
        # Whole-plan verify, release, plan done.
        all_verified = all(stages[p["id"]]["Stage"] in ("verified", "stateDone") for p in pkgs)
        all_st = pkg_stage(env, allr, rel, "ALL")
        all_retry = (all_st["Stage"] == "failed" and all_st["Verify"] and all_st["Verify"].get("status") == "failed"
                     and not blocked_for(rel, "ALL"))   # a crashed whole-plan verify is run again
        if all_verified and (all_st["Stage"] in ("new", "needVerify") or all_retry) and workers[0] > 0:
            last_w = sorted([a for a in allr if norm_plan(a["plan"]) == norm_plan(rel) and a.get("phase") == "work"], key=lambda a: a["started"])
            pv = select("medium", [], avoid=sch.row_provider(last_w[-1]) if last_w else "")
            if pv:
                nm = new_agent_name("%s-ALL-verify" % tag, allr, chosen)
                chosen.append(nm)
                free[pv["name"]] = free.get(pv["name"], 0) - 1
                workers[0] -= 1
                acts.append(start_action(nm, pv["name"], "verify", rel, "ALL", cwd, verify_prompt(rel, "ALL")))
        if all_st["Stage"] == "open" and "ALL" not in locked and "verify:" + all_st["Verify"]["name"] not in handled:
            events.append({"type": "verify_open", "key": "verify:" + all_st["Verify"]["name"], "plan": rel, "package": "ALL",
                           "agent": str(all_st["Verify"]["name"]), "text": final_tail(env, str(all_st["Verify"]["name"]))})
        rels = [a for a in allr if norm_plan(a["plan"]) == norm_plan(rel) and a.get("phase") == "release"]
        rel_pv = select("medium", [], protocol="claude") if all_st["Stage"] == "verified" and not rels and workers[0] > 0 else None   # release and integrator agents are Claude Code agents
        if rel_pv:
            nm = new_agent_name("%s-release" % tag, allr, chosen)
            chosen.append(nm)
            free[rel_pv["name"]] = free.get(rel_pv["name"], 0) - 1
            workers[0] -= 1
            acts.append(start_action(nm, rel_pv["name"], "release", rel, "REL", cwd, prompt(env, "release", {"plan": rel, "cwd": cwd})))
        open_rows = [r for r in ctx["queue"] if str(r["plan"]).lower().endswith(rel.lower())]
        if (all_st["Stage"] == "verified" and pl["status"] == "IN_PROGRESS"
                and any(a.get("status") == "done" for a in rels) and not open_rows):
            acts.append({"action": "plan_done", "plan": rel})
        # New work: fill the remaining slots with startable packages (dependencies verified, nothing running).
        bundled = []

        def is_startable(p):
            """True when the package has no agent yet, all its dependencies are verified or done and it is not blocked."""
            return (stages[p["id"]]["Stage"] == "new"
                    and all(d in stages and stages[d]["Stage"] in ("verified", "stateDone") for d in p["depends"])
                    and not blocked_for(rel, p["id"]))

        for k in pkgs:
            if stages[k["id"]]["Stage"] != "new" or workers[0] <= 0 or k["id"] in bundled:
                continue
            if any(d not in stages or stages[d]["Stage"] not in ("verified", "stateDone") for d in k["depends"]):
                continue
            if blocked_for(rel, k["id"]):
                continue
            needs = sch.package_needs(s, k["content"], k["capability"])
            nk = ",".join(needs)
            if needs and not sch.capability_available(providers, needs):
                # No provider with the needed capability has slots above 0: the package stays where it is and is reported once.
                acts.append({"action": "ask_owner", "plan": rel, "item": "CAP-" + k["id"],
                             "question": "Package %s needs the capability '%s' but no provider with it has slots above 0, so the package is not started." % (k["id"], ", ".join(needs)),
                             "recommendation": "Set slots above 0 for a provider with this capability in the machine settings, or tell me how this package should run."})
                continue
            kcls = sch.resolve_agent_class(s, "work", k["cls"], 0)
            pv = select(kcls, needs)
            if not pv:
                continue
            # Bundling: small (light) packages of the same plan with the same capabilities run in ONE agent, because the start context costs
            # more than a small task; at most bundle.maxPackages per agent.
            members = [k]
            if k["cls"] == "light":
                for o in pkgs:
                    if len(members) >= int((s.get("bundle") or {}).get("maxPackages", 3)):
                        break
                    if (o["id"] == k["id"] or o["id"] in bundled or o["cls"] != "light"
                            or ",".join(sch.package_needs(s, o["content"], o["capability"])) != nk):
                        continue
                    if is_startable(o):
                        members.append(o)
            bundled += [m["id"] for m in members]
            nm = new_agent_name("%s-%s-work" % (tag, k["id"]), allr, chosen)
            chosen.append(nm)
            free[pv["name"]] = free.get(pv["name"], 0) - 1
            workers[0] -= 1
            texts = []
            for m in members:
                same = os.path.normcase(os.path.abspath(cwd)) == os.path.normcase(os.path.abspath(repo))
                slug = sch.to_slug(tag) + "-" + sch.to_slug(m["id"])
                wt_note = (prompt(env, "workInMainCheckout", {"cwd": cwd, "newWorktree": new_worktree(env, slug), "subBranch": slug})
                           if same else prompt(env, "workInWorktree", {"cwd": cwd}))
                texts.append("Plan: %s, package %s. %s Names of worktree folders and branches you create: only lower-case letters, digits and hyphens (for this package: %s). Do the package exactly as the plan describes it." % (rel, m["id"], wt_note, slug))
            tail = " Follow all rules in section 1 of AGENT-RULES.md, update the plan's state, end when the package is finished."
            if len(members) > 1:
                text = ("This agent works %d small packages of plan %s one after the other (bundled so that the start context is paid once). "
                        "Finish each package and record it in the state before you start the next. Packages in this order: %s.\n\n" % (
                            len(members), rel, ", ".join(m["id"] for m in members)) + "\n\n".join(texts) + tail)
            else:
                text = texts[0] + tail
            acts.append(start_action(nm, pv["name"], "work", rel, k["id"], cwd, text, bool(needs), kcls, [m["id"] for m in members]))
        # Lock questions waiting for the brain (kv key lock:<plan>|<package>, written by lock_package) -> brain. With triage on, the triage
        # agent handles them and the brain gets no event.
        if not triage_on:
            for lk in runtime.kv_list(env.conn, env.name, "lock:%s|" % rel):
                txt = str(lk["value"]).strip()
                ids = re.findall(r"(?<![A-Za-z0-9-])[A-Za-z]{1,4}(?:-[A-Za-z]+)?-?\d+[A-Za-z]?(?![A-Za-z0-9])", txt)
                all_decided = bool(ids) and all(item_decided(decided, rel, i) for i in ids)   # every item named in the text is already decided or asked
                if txt and not all_decided:
                    h = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:10]
                    key = "open:%s:%s" % (rel, h)
                    if key not in handled:
                        events.append({"type": "open_for_owner", "key": key, "plan": rel, "package": "", "agent": "", "text": txt[:1500]})
    # Context limit (rule 13): agents that ended with a handoff get a fresh continuation agent.
    cont = handoff_continuations(env, allr, free, workers, chosen)
    chosen += [c["name"] for c in cont]
    acts += cont
    # Integration: a ready row without a running integrator starts the integrator. Blocked rows are handled at the top (fix agent, no brain event).
    int_pv = None
    if ctx["can_start_integrator"] and int(ctx["free"].get("int", 0)) > 0:
        int_pv = next((p for p in providers if p["protocol"] == "claude" and p["slots"] > 0 and (p["classes"].get("medium") or {}).get("model")), None)   # the integrator is a Claude Code agent (class medium); it uses no worker slot
    if int_pv:
        nm = new_agent_name("INTEGRATOR-" + datetime.now().strftime("%m%d-%H%M"), allr, chosen)
        acts.append(start_action(nm, int_pv["name"], "integrate", QUEUE_PLAN_ID, "INT-" + datetime.now().strftime("%m%d%H%M"), repo,
                                 prompt(env, "integrator", {})))
    return {"actions": acts, "events": events}
