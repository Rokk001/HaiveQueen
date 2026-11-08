"""The ready gate: what a Queen's `bzz ready` needs before a Plan branch is accepted.

1. The requirement list. The Queen answers the completeness question in the state file beside the plan, in a section `## Completeness`, one line per
   requirement: `- <id>: done | <evidence>` (evidence is a file, commit or test). Every requirement id of the plan (section Requirements of the
   plan, lines `- <id>: <text>`) and of the state file (its own section Requirements) must appear there exactly once with status done and an
   evidence. Requirements covered only by owner-verified packages, and ids an active decision of the owner names (set aside), are listed separately
   and do not block. The count line `Open requirements: <n>` does not decide here.
2. The check agent. A fresh agent (configured class, eligible free provider, took no part in the plan) compares the plan as
   submitted with the list and the branch and ends with `READY-CHECK: PASSED` or `READY-CHECK: FAILED` plus the missing points. Only a passed
   result accepts the branch. When the agent cannot run, `bzz ready` waits and the Queen calls it again next round.
"""
import os
import re
import subprocess
import time

from . import plans, roles, runtime

CHECK_RUNNER = None   # tests replace this: callable(request dict) -> (verdict, text); verdict is passed, failed or unavailable

COMPLETENESS_HEADING = re.compile(r"(?im)^#{1,6}\s*completeness\s*$")
LIST_LINE = re.compile(r"^\s*[-*]\s*([A-Za-z0-9_.-]+)\s*:\s*(.*?)\s*$")
VERDICT_LINE = re.compile(r"(?im)^[ \t]*READY-CHECK:[ \t]*(PASSED|FAILED)\b")
DONE_STATUS = ("done", "erledigt")


class Result:
    """Outcome of the list check: blocking = [(id, reason)], owner / aside = ids listed separately, no_ids = the plan names no requirement id."""

    def __init__(self):
        self.blocking, self.owner, self.aside, self.no_ids, self.ids = [], [], [], False, []


def parse_completeness(text):
    """Entries of the section Completeness of a state file as a list of (id, status, evidence); status is lower case."""
    found, inside = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("#"):
            inside = bool(COMPLETENESS_HEADING.match(line.strip()))
            continue
        m = LIST_LINE.match(line) if inside else None
        if m:
            status, _, evidence = m.group(2).partition("|")
            found.append((m.group(1), status.strip().lower(), evidence.strip()))
    return found


def _names(decision, rid):
    """Only a decision explicitly filed for this requirement sets it aside."""
    return str(decision.get("item") or "").strip().upper() == rid.upper()


def evaluate(plan_text, state_text, decisions, assignments=None, versions=()):
    """Check the requirement list of the state file against the requirement ids of the plan and the state file; returns a Result."""
    res = Result()
    required = {}
    for source in (plan_text, state_text, *versions):
        for rid, text in plans.parse_requirements(source):
            required.setdefault(rid, text)
    res.ids = list(required)
    if not required:
        res.no_ids = True
        return res
    entries = parse_completeness(state_text)
    if assignments is None:
        packages = plans.parse_plan_text(plan_text).packages
        assignments = [(rid, p.verify) for p in packages for rid in p.requirements]
    active = [d for d in decisions if str(d.get("status") or "active") == "active"]
    for rid, text in required.items():
        mine = [e for e in entries if e[0] == rid]
        covering = [verify for requirement, verify in assignments if requirement == rid]
        if covering and all(verify == "owner" for verify in covering):
            res.owner.append(rid)
        elif any(_names(d, rid) for d in active):
            res.aside.append(rid)
        elif not mine:
            res.blocking.append((rid, "missing in the list"))
        elif len(mine) > 1:
            res.blocking.append((rid, "listed more than once"))
        elif mine[0][1] not in DONE_STATUS:
            res.blocking.append((rid, "status %s" % (mine[0][1] or "empty")))
        elif not mine[0][2]:
            res.blocking.append((rid, "no evidence"))
    return res


def refusal_text(res):
    """The text `bzz ready` gives back when the list does not pass."""
    if res.no_ids:
        return ("the plan names no requirement ids: its requirements need ids (section Requirements, one line `- <id>: <text>` per requirement), "
                "then list each in the section Completeness of the state file")
    return ("requirements not done: %s. List every requirement id once in the section Completeness of the state file as `- <id>: done | <evidence>` "
            "(file, commit or test)" % ", ".join("%s (%s)" % b for b in res.blocking))


def parse_verdict(text):
    """(verdict, points) of the answer of the check agent: the LAST READY-CHECK line decides, the lines after it are the missing points."""
    found = list(VERDICT_LINE.finditer(text or ""))
    if not found:
        return "unavailable", "the check agent gave no READY-CHECK verdict"
    last = found[-1]
    return last.group(1).lower(), (text[last.end():].strip() if last.group(1).upper() == "FAILED" else "")


def _read(paths):
    """Text and path of the first readable file of a list ('' and the first path when none is)."""
    for p in paths:
        try:
            with open(p, encoding="utf-8-sig") as f:
                return f.read(), p
        except OSError:
            continue
    return "", paths[0]


class PlanHistoryError(Exception):
    """The plan history could not be read reliably."""


def plan_versions(path, current):
    """Read the current plan and its committed versions, if it has Git history."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        probe = subprocess.run(['git', '-C', folder, 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
        if probe.returncode:
            if 'not a git repository' in probe.stderr.lower():
                return [current]
            raise PlanHistoryError('cannot locate the plan repository')
        root = probe.stdout.strip()
        shallow = subprocess.run(['git', '-C', root, 'rev-parse', '--is-shallow-repository'], capture_output=True, text=True, check=True).stdout.strip()
        if shallow != 'false':
            raise PlanHistoryError('the plan repository has incomplete history')
        relative = os.path.relpath(path, root).replace('\\', '/')
        history = subprocess.run(['git', '-C', root, '-c', 'core.quotePath=false', 'log', '--follow', '--format=COMMIT:%H', '--name-status', '--', relative], capture_output=True, text=True, encoding='utf-8', check=True).stdout
        versions = [current]
        commit = None
        for line in history.splitlines():
            if line.startswith('COMMIT:'):
                commit = line[7:]
            elif line and commit:
                fields = line.split('\t')
                if fields[0].startswith('D'):
                    break   # the file was deleted here: older versions at this path belong to an earlier plan
                result = subprocess.run(['git', '-C', root, 'show', '%s:%s' % (commit, relative)], capture_output=True, check=True)
                versions.append(result.stdout.decode('utf-8-sig', errors='replace'))
                if fields[0].startswith('C'):
                    break   # the file was created here as a copy of another plan: older versions belong to that plan
                if fields[0].startswith('R') and len(fields) == 3 and fields[2] == relative:
                    relative = fields[1]
                commit = None
        return versions
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise PlanHistoryError('cannot read the plan history') from exc


def owner_released(env, plan, queen):
    """True when an active decision of exactly this plan with item owner-release names the Queen's HEAD commit (full id or at least 7 characters) or says "any commit"."""
    releases = [d for d in runtime.list_decisions(env.conn, env.name, plan=plan, item="owner-release", active_only=True)
                if d.get("plan_path") == plan and str(d.get("item") or "").strip().lower() == "owner-release"]
    if not releases:
        return False
    if any("any commit" in str(d.get("text") or "").lower() for d in releases):
        return True
    try:
        head = subprocess.run(["git", "-C", queen.get("cwd") or str(env.workspace), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip().lower()
    except (OSError, subprocess.CalledProcessError):
        return False
    if not head:
        return False
    return any(head.startswith(token) for d in releases for token in re.findall(r"\b[0-9a-f]{7,40}\b", str(d.get("text") or "").lower()))


def gate(env, plan, queen):
    """Run the whole gate for the Queen's `bzz ready`; returns (accepted, message, waiting)."""
    folders = [f for f in (queen.get("cwd"), str(env.workspace)) if f]
    plan_text, plan_file = _read([os.path.join(f, plan) for f in folders])
    state_text, state_file = _read([plans.state_file_path(os.path.join(f, plan)) for f in folders])
    decisions = [d for d in runtime.list_decisions(env.conn, env.name, active_only=True) if not d["plan_path"] or d["plan_path"] == plan]
    assignments = runtime.cut_assignments(env.conn, env.name, plan, plan_text)
    # No trustworthy cut record (older worked plan, reused path, another plan title): require evidence; requirements added after the cut need evidence too.
    try:
        versions = plan_versions(plan_file, plan_text)
    except PlanHistoryError as exc:
        return False, str(exc), True
    res = evaluate(plan_text, state_text, decisions, assignments if assignments is not None else [], versions)
    if res.no_ids or res.blocking:
        return False, refusal_text(res), False
    if owner_released(env, plan, queen):
        return True, "", False
    skipped = ", ".join(res.owner + res.aside) or "none"
    prompt = roles.role_text("ready-check", {"plan_file": plan_file, "state_file": state_file, "cwd": queen.get("cwd") or str(env.workspace), "skipped": skipped, "language": roles.language(env.settings)})
    current = dict.fromkeys(plans.parse_requirements(plan_text))   # only the current version: changed texts of earlier versions are no longer the target
    prompt += "\n\nRequirements of the current plan version:\n" + "\n".join("- %s: %s" % pair for pair in current)
    request = {"env": env, "queen": queen, "plan": plan, "cwd": queen.get("cwd") or str(env.workspace), "prompt": prompt}
    runner = CHECK_RUNNER or (lambda r: start_check_agent(r["env"], r["queen"], r))
    verdict, text = runner(request)
    if verdict == "passed":
        return True, "", False
    if verdict == "failed":
        return False, "the check agent found missing points, fix them and call bzz ready again:\n" + (text or "(none named)"), False
    return False, "the check agent could not run (%s): ready waits, call bzz ready again next round" % (text or "no reason given"), True


def start_check_agent(env, queen, request, sleep=time.sleep):
    """Start the check agent through the agent runner like an Agent and wait for its answer; returns (verdict, text).

    The configured ready-check class runs on an eligible provider with a free slot and limit, and it
    takes a slot like an Agent. No provider, no slot, a failed start, an agent that ends without verdict or a timeout (setting readyCheckTimeoutSec)
    give `unavailable`."""
    from . import agent_process, hives, loop, scheduler
    plan = request["plan"]
    hive = hives.get(env.conn, env.name, plan)
    if not hive:
        return "unavailable", "no Plan of this plan"
    active = runtime.list_agents(env.conn, None, "active")
    running = [{**a, "status": "running"} for a in active if agent_process.counts(a)]
    free = scheduler.provider_free(env.providers, running, env.conn)
    limited = [p["name"] for p in env.providers if scheduler.reserve_zero(p, env.conn)]   # a used-up window (reserve 0) blocks the provider
    cls = str(env.settings.get("readyCheckClass", "medium"))
    candidates = [p for p in env.providers if p["name"] not in env.settings.get("excludedProviders", []) and p["name"] not in limited]
    pick = scheduler.select_provider(candidates, cls, [], free, conn=env.conn)
    if not pick:
        return "unavailable", "no provider with a free slot and class %s" % cls
    provider = pick["name"]
    existing = [a["name"] for a in runtime.list_agents(env.conn, env.name, "all")]
    state = {"names": existing, "busy": [], "plan_status": {scheduler.norm_plan(plan): "RUNNING"}, "by_path": {scheduler.norm_plan(plan): {"path": plan}}}
    ctx = {"env": env, "state": state, "pool": loop.Pool(free, 0), "report": [], "dry": False, "settle": 0}
    action = {"name": "%s-ready-check" % re.sub(r"-\d+$", "", str(queen["name"])), "type": provider, "phase": "verify", "plan": plan, "package": "READY-CHECK",
              "cwd": request["cwd"], "prompt": request["prompt"], "class": cls}
    if not loop._do_start(ctx, action, forced_pick=scheduler.get_provider(env.providers, provider)):
        return "unavailable", "the agent runner could not start the check agent"
    name = state["names"][-1]
    deadline = time.monotonic() + float(env.settings.get("readyCheckTimeoutSec", 3600))
    while time.monotonic() < deadline:
        # bzz done can set a terminal status before the runner writes final.txt.
        row = runtime.get_agent(env.conn, env.name, name)
        if row is not None and row.get("status") not in scheduler.ACTIVE and not agent_process.is_live(row):
            try:
                text = (env.run_dir(name) / "final.txt").read_text(encoding="utf-8-sig")
            except OSError:
                text = ""
            return parse_verdict(text)
        sleep(5)
    return "unavailable", "the check agent did not end in time"
