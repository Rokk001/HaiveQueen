"""Shared agent lifecycle and plan helpers used by the Hive scheduler."""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta

from . import buildjobs, db, interrupt, runtime
from . import scheduler as sch
from .scheduler import ACTIVE, norm_plan

QUEUE_PLAN_ID = "integration-queue"   # pseudo plan name of the fix tasks for blocked queue rows that name no plan
WORDS = {"ready": "ready", "in_run": "in Lauf", "blocked": "blockiert", "integrated": "integriert", "withdrawn": "zurückgezogen"}   # status words the routine and the brain know
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
             "status": str(q["status"]).upper(), "answer": str(q["answer"] or ""), "by": str(q["answered_by"] or "")} for q in runtime.list_questions(env.conn, env.name)]




def set_handled(env, key: str, value: str = None) -> None:
    """Remember an event key as handled (the optional value is a retry counter)."""
    runtime.handled_add(env.conn, env.name, key, value or None)


def remove_handled(env, key: str) -> None:
    """Forget a handled key (the event is asked again in the next round)."""
    runtime.handled_remove(env.conn, env.name, key)










def interrupted_count(agents: list, plan: str, pkg: str, phase: str) -> int:
    """Number of consecutive interrupted ends at the end of the history of one task (same plan, package and phase); a normal end starts the count again."""
    rows = sorted([a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and str(a.get("package")) == pkg and a.get("phase") == phase
                   and a.get("status") in ("done", "failed")], key=lambda a: a["started"])
    n = 0
    for a in reversed(rows):
        if a.get("status") == "failed" and interrupt.is_interrupted(a):
            n += 1
        else:
            break
    return n


# True when the retry after the interrupted end `a` may start now.
def retry_due(env, agents: list, a: dict, now: datetime = None) -> bool:
    """True when the retry after the interrupted end `a` may start now.

    The time of the next try is stored in the agent row (retryAt), so a waiting retry survives a restart of the Queen. A rate limit has none
    yet: a Queen waits for her provider; for older phases a provider with room permits an immediate retry, otherwise the later
    reset time of the providers at their limit (15 minutes when none is known) is stored and waited for. API and connection errors wait 1, 2,
    4, 8 minutes, then 15, counted over the consecutive interrupted ends of the task; sleep and vanished processes are retried at once.
    """
    from . import usage
    now = now or datetime.now()
    stored = db.parse_time(a.get("retryAt"))
    if stored is not None:
        return stored <= now
    kind = interrupt.kind_of(a)
    if kind == interrupt.RATE_LIMIT:
        wins = {p["name"]: usage.limit_windows(env.conn, p) for p in env.providers if p["slots"] > 0}
        limited = sch.row_provider(a)
        if a.get("phase") != "queen" and any(not any(w["used_pct"] >= 100 for w in ws) for n, ws in wins.items() if n != limited):
            return True   # another provider has room: the normal provider choice takes it at once
        # wait for the reset of the window of the limited provider that is at its limit (the later one when both are); 15 minutes when unknown
        resets = [w["reset_epoch"] for w in wins.get(limited, []) if w["used_pct"] >= 100 and w["reset_epoch"]]
        at = datetime.fromtimestamp(max(resets)) if resets else now + timedelta(minutes=float(env.settings.get("rateLimitWaitMinutes", interrupt.RATE_LIMIT_MINUTES)))
        env.update_agent(a["name"], {"retryAt": at.isoformat(timespec="seconds")})
        return at <= now
    if kind in (interrupt.API, interrupt.CONNECTION):
        count = interrupted_count(agents, a["plan"], str(a.get("package")), str(a.get("phase")))
        ended = db.parse_time(a.get("ended_at")) or now
        at = ended + timedelta(minutes=interrupt.backoff_minutes(count, env.settings.get("retryWaitMinutes")))
        env.update_agent(a["name"], {"retryAt": at.isoformat(timespec="seconds")})
        return at <= now
    return True


def waiting_retry(env, agents: list, plan: str, pkg: str, phase: str) -> bool:
    """True when the newest agent of this task (plan, package, phase) ended interrupted and its retry is not due yet."""
    rows = sorted([a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and str(a.get("package")) == pkg and a.get("phase") == phase],
                  key=lambda a: a["started"])
    last = rows[-1] if rows else None
    return bool(last and last.get("status") == "failed" and interrupt.is_interrupted(last) and not retry_due(env, agents, last))






INTERRUPTED_INTEGRATOR_TEXT = ("The previous integrator was interrupted (laptop sleep, network or API problem, or its process vanished). Before anything "
                               "else check the main checkout for an unfinished merge and abort it (git merge --abort), then integrate the ready rows again.")




def repair_stale_agents(env, now: datetime = None, alive=None) -> None:
    """Mark running or starting agents whose runner process is gone as failed, so slots and the dashboard stay correct."""
    from . import platform as osplat, agent_process
    alive = alive or osplat.process_alive
    now = now or datetime.now()
    for a in env.agents("active"):
        if a.get("status") not in ACTIVE:
            continue
        why = None
        if a.get("pid"):
            if not agent_process.is_live(a, alive):
                why = "runner process vanished (ended by executor or crashed)"
        elif a.get("started_at"):
            started = db.parse_time(a["started_at"])
            if started is not None and (now - started).total_seconds() > 120:
                why = "pane never started (no runner process)"
        if why:
            fields = {"status": "failed", "ended_at": db.now(), "last_line": why, "interrupted": interrupt.VANISHED}
            if a.get("pid") and agent_process.pid_reused(a):
                fields.update({"previousPid": a["pid"], "pid": None})
            env.update_agent(a["name"], fields)
            env.log("TECHNICAL NOTE: %s: %s, marked failed" % (a["name"], why))


# ---- final answers of verify agents ----

COUNT_RE = re.compile(r"Offene Anforderungen[^:\r\n]*:\s*(\d+)")   # the count line of a verify answer
RUNNER_FILES = {"final.txt", "prompt.md", "rules.md", "milestones.txt", "stderr.txt", "build-samples.txt", "runner.log", "runner-error.txt"}   # files the runner writes itself, never a report


def read_final(env, name: str) -> str:
    """Text of the final answer of an agent run ('' when missing).

    That is final.txt (the last complete answer of the agent). When it has no 'Offene Anforderungen: <n>' line, the report the agent wrote into
    its run folder is read as well, and its text is appended when it has such a line (a Codex run often ends with only an introductory
    sentence and puts its answer into a report file). Agents name that file as they like (report.txt, result.md, final-report.txt,
    verify-report.txt ...), so every .md or .txt file of the run folder counts except the files the runner itself writes; the newest one with a
    count line wins.
    """
    folder = env.run_dir(name)
    try:
        text = (folder / "final.txt").read_text(encoding="utf-8-sig")
    except OSError:
        text = ""
    if COUNT_RE.search(text):
        return text
    try:
        files = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".md", ".txt") and p.name not in RUNNER_FILES],
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return text
    for p in files:
        try:
            report = p.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if COUNT_RE.search(report):
            return text + "\n" + report
    return text


def verify_zero(env, name: str) -> bool:
    """True when the LAST 'Offene Anforderungen: N' line of the final answer says 0 (lines after it do not matter) and no partial item is listed.

    A partial item is a list or table line (starting with -, *, | or a box-drawing frame character) that says "teilweise", "partial" or "partially" in any case.
    """
    t = read_final(env, name)
    return bool(t) and verify_open_count(env, name) == 0 and not re.search(r"(?im)^\s*[-*|─-╿].*(?:teilweise|\bpartial)", t)


def verify_open_count(env, name: str) -> int:
    """Number of open requirements named at the end of a verify answer; -1 when the answer is missing or has no such line."""
    found = re.findall(r"Offene Anforderungen[^:\r\n]*:\s*(\d+)", read_final(env, name))
    return int(found[-1]) if found else -1




def lock_reason(text: str) -> str:
    """Lock reason (one open point per line) from the tail of a verify answer: bullet marks removed, the closing count line dropped."""
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"^[\s\-*|]+", "", ln).strip().rstrip("|").strip()
        if ln and not re.match(r"^Offene Anforderungen", ln):
            lines.append(ln)
    return "\n".join(lines)[:600]


# ---- packages: stage, failures, lock, class ----



def package_failures(env, agents: list, plan: str, pkg: str) -> int:
    """Failures of one package: failed work agents (not technical ends) plus verifies that reported open items (not for a package closed by build or owner).

    A finished verify without any count line (not in final.txt and not in a report file either) counts as one failed attempt, once per verify.
    """
    mine = [a for a in agents if norm_plan(a["plan"]) == norm_plan(plan) and (str(a.get("package")) == pkg or pkg in a["bundle"])]
    f = sum(1 for a in mine if a.get("phase") == "work" and a.get("status") == "failed" and not interrupt.is_interrupted(a))
    o = 0
    row = next((p for p in env.packages(plan) if p["id"] == pkg), None)
    if not row or row["verify"] == "code":   # open items of a package that build or owner close are no failure
        for v in (a for a in mine if a.get("phase") == "verify" and a.get("status") == "done"):
            if verify_open_count(env, v["name"]) != 0:
                o += 1
    return f + o








def ready_refusal(env, name: str) -> str:
    """Why `bzz ready` of the agent `name` is refused; '' when it is allowed.

    Only the release phase registers a branch as ready, and only after the whole-plan verify (package ALL) ended with 'Offene Anforderungen: 0'.
    The one other case is the fix of a blocked queue row (work phase, package FIX-<tip>), which registers its branch again. An unknown agent
    name is not judged (the call comes from a person or a test).
    """
    row = runtime.get_agent(env.conn, env.name, name)
    if row is None:
        return ""
    a = view(row)
    phase, pkg = str(a.get("phase") or ""), str(a.get("package") or "")
    if phase == "work" and re.match(r"^FIX-", pkg):
        return ""
    if phase != "release":
        return "only the release phase registers a branch as ready (this agent is in phase %s)" % (phase or "unknown")
    ver = sorted([x for x in all_agents(env) if norm_plan(x["plan"]) == norm_plan(a["plan"]) and str(x.get("package")) == "ALL"
                  and x.get("phase") == "verify" and x.get("status") == "done"], key=lambda x: x["started"])
    if not ver or not verify_zero(env, ver[-1]["name"]):
        return 'the whole-plan verify did not end with "Offene Anforderungen: 0"'
    return ""






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




# ---- progress: new commits, stalled packages, stalled plans ----

def git_sha(folder, ref: str = "HEAD") -> str:
    """Commit a ref of the git repository in `folder` points to; '' when the folder is no repository or the ref does not exist."""
    if not folder:
        return ""
    try:
        res = subprocess.run(["git", "-C", str(folder), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def package_slug(env, plan: str, pkg: str) -> str:
    """Name of the sub-branch and worktree an agent of the package uses (plan tag and package id as a slug)."""
    return sch.to_slug(plan_tag(env, plan)) + "-" + sch.to_slug(pkg)


def progress_marker(env, plan: str, pkg: str, cwd) -> str:
    """State of the work of a package: the commit of the agent's folder and the commit of the package's sub-branch ('' when it cannot be read).

    An agent started in the main checkout works in a new worktree on the sub-branch, so only that branch moves; an agent in its own worktree moves HEAD.
    """
    head = git_sha(cwd)
    if not head:
        return ""
    return "%s|%s" % (head, git_sha(env.code_repo, "refs/heads/" + package_slug(env, plan, pkg)))


def record_progress(env, name: str, plan: str, pkg: str, cwd) -> None:
    """Remember the state of the work at the start of an agent (kv key head:<agent>), so a later end can be compared with it."""
    marker = progress_marker(env, plan, pkg, cwd)
    if marker:
        runtime.kv_set(env.conn, env.name, "head:" + name, marker)
























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



# Words of a decision that closes a package: 'done', 'closed', 'erledigt', 'abgeschlossen', 'no further runs' / 'keine weiteren Laeufe'.
DONE_WORDS = re.compile(r"(?i)(?<!not )(?<!nicht )\b(erledigt|done|closed|abgeschlossen)\b|keine\s+weiteren|no\s+further")




# Words by which the text of a question says that a package is locked (a lock question whose item has no LOCK- prefix).
LOCK_QUESTION_WORDS = re.compile(r"(?i)(gesperrt|\blocked\b)")














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








def base_values(env) -> dict:
    """Placeholder values every prompt template gets."""
    s = env.settings
    return {"owner": s.get("owner", "Owner"), "integrationBranch": s.get("integrationBranch", "main"), "compileCheck": s.get("compileCheck", ""),
            "queueFile": "the integration queue", "queueName": "integration queue", "integratorInstructions": s.get("integratorInstructions", ""),
            "buildFlowDoc": s.get("buildFlowDoc", ""), "buildRules": buildjobs.rule_text(s.get("buildJobs")), "project": env.name, "runsDir": str(env.run_dir("")).replace("\\", "/")}






def new_worktree(env, middle: str) -> str:
    """Folder of a new worktree next to the code repo (the workspace when the project has no `codeRepo`): <parent>/<worktreePrefix>-<middle>."""
    return os.path.join(os.path.dirname(str(env.code_repo).rstrip("\\/")), "%s-%s" % (env.settings.get("worktreePrefix", "wt"), middle))



def same_dir(a: str, b: str) -> bool:
    """True when both paths name the same folder (case and separators ignored)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))




def other_workspace(env) -> str:
    """The workspace folder when it is not the code repo (the project has a `codeRepo`), else an empty text."""
    return "" if same_dir(str(env.code_repo), str(env.workspace)) else str(env.workspace)


def pick_cwd(hist: list, repo: str, pkg: str = "", workspace: str = "") -> str:
    """Working folder for a new agent of a plan: the newest worktree of the package `pkg`, else the newest worktree of the plan (never the
    main checkout `repo`, nor the `workspace` folder when one is given, while a worktree is known), else the newest folder of the plan
    (the main checkout). With a separate `workspace` and no worktree in the history (only triage agents ran) the result is `repo`, never the workspace.
    `hist` is sorted by start time."""
    wts = [a for a in hist if not same_dir(str(a["cwd"]), repo) and not (workspace and same_dir(str(a["cwd"]), workspace))]
    own = [a for a in wts if pkg and (str(a.get("package")) == pkg or pkg in a["bundle"])]
    if workspace and not wts:
        return repo
    return str((own or wts or hist)[-1]["cwd"])


# ---- handoff (rule 13) ----

def set_handoff_flags(env, agents: list) -> list:
    """Write the flag file runs/<name>/HANDOFF for every running agent whose context reached contextLimitTokens (the agent is never killed).

    Returns [{Name, Ctx}] of the newly flagged agents.
    """
    res = []
    for a in agents:
        ctx = a.get("ctxTokens")
        limit = sch.context_limit(env.settings, env.providers, sch.row_provider(a), a.get("class"))   # the limit of the agent's model wins over the global one
        if a.get("status") in ACTIVE and ctx is not None and int(ctx) >= limit:
            f = env.run_dir(a["name"]) / "HANDOFF"
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("context %s reached %s\n" % (ctx, datetime.now().isoformat(timespec="seconds")), encoding="utf-8")
                res.append({"Name": str(a["name"]), "Ctx": int(ctx)})
    return res




# ---- the routine ----
