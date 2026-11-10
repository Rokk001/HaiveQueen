"""The integration steps of a Project: the switches decide which steps run, the capabilities decide who does them.

A switched-on Build or Deploy step the Queen's provider cannot do becomes a package for a provider that can (ids in plans.STEP_PACKAGES); the rows live
beside the packages of the plan file and survive reading it again. With no such provider the owner is asked once. `bzz ready` waits for the Build package,
`bzz integration release` and the closing of the plan wait for the Deploy package.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime

from . import db, localtime, plans, platform as osplat, proctree, roles, routine, runtime, scheduler as sch
from .i18n import tr

BUILD = plans.STEP_PACKAGES["Build"]
DEPLOY = plans.STEP_PACKAGES["Deploy"]
COMPILE_MILESTONE = re.compile(r"^(\w+)\t\S+ Compile test\b")   # the live line of a compile check in milestones.txt (the dashboard shows it)
COMPILE_KEY = "compile-check:"   # + Queen name: the stored result of `bzz compile-check` for that Queen run
POLL_SECONDS = 1.0   # how often a compile check looks at its process and at the lock of the machine
COMPILE_AGENT_KEY = "compile-agent:"   # + name or worktree: the stored result of an Agent; it never counts for `bzz ready`


# The three switches of an Apiary.
def switches(settings):
    """{compileCheck, build, deploy} of the settings (scheduler.build_settings fills in the defaults, also for a Project from before the switches)."""
    return {"compileCheck": bool(str(settings.get("compileCheck") or "").strip()), "build": bool(settings.get("buildEnabled", True)),
            "deploy": bool(settings.get("deployEnabled", False))}


# Who does each step.
def step_states(env, provider_name):
    """{Build, Deploy}: off (switched off), queen (her provider has the capability), package (another provider has it) or owner (no provider has it)."""
    from . import bees
    on = switches(env.settings)
    excluded = env.settings.get("excludedProviders", [])
    offered = [p for p in env.providers if p["name"] not in excluded and p.get("slots", 1) > 0]
    own = sch.get_provider(env.providers, provider_name)
    states = {}
    for capability, enabled in (("Build", on["build"]), ("Deploy", on["deploy"])):
        if not enabled:
            states[capability] = "off"
        elif own is not None and bees.capable_providers([own], [capability]):   # only the ticks decide; a provider no longer in the settings has none
            states[capability] = "queen"
        else:
            states[capability] = "package" if bees.capable_providers(offered, [capability]) else "owner"
    return states


# Create and remove the step packages of a plan.
def sync(env, plan, provider_name):
    """Bring the step packages of the plan in line with the switches and the capabilities; asks the owner once for a step no provider can do; returns the states."""
    states = step_states(env, provider_name)
    existing = {p["id"] for p in runtime.list_packages(env.conn, env.name, plan)}
    language = roles.language(env.settings)
    for capability, package in plans.STEP_PACKAGES.items():
        if states[capability] != "owner":   # the owner need not answer any more, and an open question would hold the plan
            for question in runtime.list_questions(env.conn, env.name, status="open", plan=plan, item=package):
                db.answer_question(env.conn, question["id"], tr("No longer needed: the step is switched off or a provider can do it now.", language=language), "auto")
        if states[capability] in ("off", "queen"):
            if package in existing:
                env.conn.execute("DELETE FROM packages WHERE project=? AND plan_path=? AND id=?", (env.name, plan, package))
            continue
        if package not in existing:
            env.conn.execute("INSERT INTO packages (project, plan_path, id, title, class, agent_type, capability, verify, depends_on) VALUES (?,?,?,?,?,?,?,?,?)",
                             (env.name, plan, package, tr("Integration step: %s", tr(capability, language=language), language=language), str(env.settings.get("queenClass", "medium")), "", capability, "code", json.dumps(["all"])))
        if states[capability] == "owner":
            text = tr("No configured provider offers the capability %s. How should this package continue?", capability, language=language)
            if not runtime.list_questions(env.conn, env.name, plan=plan, item=package):
                db.add_question(env.conn, env.name, plan, package, text, tr("Configure an eligible provider.", language=language))
    return states


# The step packages that still hold the plan back.
def open_steps(env, plan, capabilities=("Build", "Deploy")):
    """Ids of the step packages of the plan that exist and are not done, in the order of the steps."""
    from . import bees
    bees.ensure_attempts(env.conn)
    rows = {p["id"] for p in runtime.list_packages(env.conn, env.name, plan)}
    done = {r[0] for r in env.conn.execute("SELECT package FROM package_progress WHERE project=? AND plan_path=? AND state='done'", (env.name, plan))}
    return [package for capability, package in plans.STEP_PACKAGES.items()
            if capability in capabilities and package in rows and package not in done and not bees.package_done(env.conn, env.name, plan, package)]


# Who may close a step package.
def done_refusal(conn, project, plan, package):
    """Why the Queen may not set a step package done, or '': an Agent result closes it, by hand only after an answer of the owner to its question."""
    from . import bees
    if package not in plans.STEP_PACKAGES.values() or bees.package_done(conn, project, plan, package):
        return ""
    if any(q.get("answered_by") != "auto" for q in runtime.list_questions(conn, project, status="answered", plan=plan, item=package)):
        return ""
    return "integration step package %s is closed by the result of its Agent or after an answer of the owner; start an Agent for it" % package


# Whether a Bee was started for a step package.
def result_refusal(conn, project, plan, package):
    """Why a succeeded result of a step package is refused, or '': it needs a started Agent."""
    from . import bees
    if package not in plans.STEP_PACKAGES.values() or bees.package_done(conn, project, plan, package):
        return ""
    bees.ensure_attempts(conn)
    if conn.execute("SELECT 1 FROM bee_assignments WHERE project=? AND plan_path=? AND package=?", (project, plan, package)).fetchone():
        return ""
    return "no Agent was started for integration step package %s; start one with bzz agent start" % package


# A new integration needs its steps again.
def reset(conn, project, plan):
    """Forget the step packages of the plan and their results: after an owner check that says something is not right, the next integration builds and deploys again."""
    from . import bees
    bees.ensure_attempts(conn)
    for package in plans.STEP_PACKAGES.values():
        conn.execute("DELETE FROM packages WHERE project=? AND plan_path=? AND id=?", (project, plan, package))
        conn.execute("DELETE FROM package_progress WHERE project=? AND plan_path=? AND package=?", (project, plan, package))
        conn.execute("DELETE FROM bee_completed WHERE project=? AND plan_path=? AND package=?", (project, plan, package))
        conn.execute("UPDATE questions SET item=? WHERE project=? AND plan_path=? AND item=? AND status='answered'", (package + "-answered", project, plan, package))   # the new integration asks again


# The commit a worktree stands on.
def head_commit(cwd):
    """The full commit ID of HEAD in the folder, or '' when it cannot be read."""
    if not cwd:
        return ""
    try:
        done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


# The stored result of the compile check of a Queen run.
def compile_result(conn, project, queen_name):
    """The result `bzz compile-check` stored for the Queen ({ok, exit, commit, time, seconds, command}), or None."""
    raw = runtime.kv_get(conn, project, COMPILE_KEY + queen_name)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    return data if isinstance(data, dict) else None


# The lock that lets one compile check run at a time per machine.
def _compile_lock_path():
    """The lock file of the machine (in the machine data folder)."""
    from . import config
    return config.data_dir() / "compile-check.lock"


# Wait for the machine-wide compile check lock.
def _acquire_compile_lock(poll):
    """Create the lock file with this process id once no other check holds it (a lock whose process is gone is taken over); returns the path."""
    path = _compile_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                holder = int(path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                holder = None   # being written or unreadable: look again
            if holder is not None and holder > 0 and not osplat.process_alive(holder):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            time.sleep(poll)
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as lock:
            lock.write(str(os.getpid()))
        return path


# Run the compile command and watch its progress.
def _watch_compile(command, cwd, stall_seconds, max_seconds, poll):
    """Run `command` in `cwd` until it ends; returns (exit code, output text). The check ends red (124) after max_seconds of running and red (125,
    "stalled") when for stall_seconds neither a new output line appeared nor the CPU time of its process tree grew."""
    import collections
    import threading
    lines = collections.deque(maxlen=400)
    state = {"lines": 0}

    def read(stream):
        for line in iter(stream.readline, ""):
            lines.append(line.rstrip("\r\n"))
            state["lines"] += 1

    # The configured command line is the owner's own text, so the system shell runs it (named explicitly, no shell flag): cmd /s keeps a quoted program path intact.
    line = 'cmd.exe /d /s /c "%s"' % command if os.name == "nt" else ["/bin/sh", "-c", command]
    proc = subprocess.Popen(line, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    proctree.exempt_register(proc.pid)   # started by HaiveQueen, not by the agent: the cleanup of the agent's leftover processes leaves it alone
    reader = threading.Thread(target=read, args=(proc.stdout,), daemon=True)
    reader.start()
    started = last_progress = time.monotonic()
    seen_lines, seen_cpu, ended = 0, -1.0, None
    try:
        while proc.poll() is None:
            now = time.monotonic()
            cpu = proctree.tree_cpu_seconds(proc.pid)
            if state["lines"] != seen_lines or abs(cpu - seen_cpu) > 0.001:
                seen_lines, seen_cpu, last_progress = state["lines"], cpu, now
            if now - started > max_seconds:
                ended = (124, "the compile check ran longer than %g minutes (compileMaxMinutes)" % (max_seconds / 60.0))
            elif now - last_progress > stall_seconds:
                ended = (125, "stalled: the compile check made no output and used no CPU for %g minutes (compileStallMinutes)" % (stall_seconds / 60.0))
            if ended:
                osplat.kill_process_tree(proc.pid)
                break
            time.sleep(poll)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=5)
    finally:
        proctree.exempt_clear(proc.pid)
    text = "\n".join(lines)
    if ended:
        return ended[0], ended[1] + ("; last output lines:\n" + "\n".join(list(lines)[-20:]) if lines else "; no output at all")
    return proc.returncode, text


# Run the compile check of the project for a Queen or an Agent.
def run_compile_check(env, queen, cwd=None, key=None, poll=None, milestones=True):
    """Run the setting compileCheck in the worktree `cwd` (default: that of `queen`, the row of the caller), store {ok, exit, commit, time, seconds, command} under
    `key` (default: the result of the Queen's run that `bzz ready` reads), add the live line to the milestones of the caller and return the result with the end of the
    output under "output". Checks run one at a time per machine: waiting for another check counts for neither limit. The settings compileStallMinutes (10) and
    compileMaxMinutes (120) end a check red; the commit is the one the worktree stood on before the run."""
    poll = POLL_SECONDS if poll is None else poll
    command = str(env.settings.get("compileCheck") or "").strip()
    cwd = str(cwd or queen.get("cwd") or "")
    commit = head_commit(cwd)
    stall = float(env.settings.get("compileStallMinutes", 10)) * 60.0
    longest = float(env.settings.get("compileMaxMinutes", 120)) * 60.0
    lock = None
    try:
        lock = _acquire_compile_lock(poll)
        started = time.time()   # after the wait for the lock
        try:
            code, output = _watch_compile(command, cwd, stall, longest, poll)
        except OSError as exc:
            code, output = 127, "the compile check could not start: %s" % exc
    finally:
        if lock:
            try:
                lock.unlink()
            except OSError:
                pass
    result = {"ok": code == 0, "exit": code, "commit": commit, "time": db.now(), "seconds": round(time.time() - started, 1), "command": command}
    if code == 125:
        result["stalled"] = True
    runtime.kv_set(env.conn, env.name, key or COMPILE_KEY + queen["name"], json.dumps(result))
    last = next((" ".join(line.split()) for line in reversed(output.splitlines()) if line.strip()), "")
    live = "%s\t%s Compile test - Exit %s%s\n" % ("Green" if result["ok"] else "Red", localtime.time(datetime.now()), code, (": " + last[:140]) if last else "")
    try:
        if not milestones:
            raise OSError
        folder = env.run_dir(queen["name"])
        folder.mkdir(parents=True, exist_ok=True)
        with open(str(folder / "milestones.txt"), "ab") as handle:
            handle.write(live.encode("utf-8"))
    except OSError:
        pass
    return {**result, "output": "\n".join(output.strip().splitlines()[-40:])[-4000:]}


# Whether the Queen's run has a passed compile check for her current commit.
def compile_check_missing(env, queen, plan):
    """The reason `bzz ready` waits for the compile check, or '': the step is on, a command is configured and the Queen has no green `bzz compile-check` result for the commit her worktree stands on."""
    if not switches(env.settings)["compileCheck"] or not str(env.settings.get("compileCheck") or "").strip():
        return ""
    again = "run bzz compile-check --project %s --plan %s, then call bzz ready again" % (env.name, plan)
    result = compile_result(env.conn, env.name, queen["name"])
    if not result:
        return "the compile check was not run in this Queen run; " + again
    if not result.get("ok"):
        return "the last compile check of this Queen run failed; fix it, " + again
    head = head_commit(queen.get("cwd"))
    if not head:
        return "the commit of the worktree cannot be read, so the compile check cannot be matched to it"
    if result.get("commit") != head:
        return "the green compile check is for commit %s but the worktree is at %s; %s" % (str(result.get("commit"))[:7], head[:7], again)
    return ""


# The integration part of the Queen's start text.
def start_text(env, plan, provider_name):
    """The numbered integration order with only the switched-on steps, followed by what the switches and capabilities mean for this Queen."""
    on = switches(env.settings)
    states = step_states(env, provider_name)
    acquire = ("Use bzz integration acquire --project %s --plan %s. Use the returned buildJobs EnvVar and Jobs for the integration build and the plan build, and the returned Parallel as the "
               "--parallel value of the build command (also in the variable named by ParallelVar); the build starts only while the free RAM is at least MinFreeGB. These values win over a "
               "--parallel value or RAM minimum in a project rule: do not ask a question about that difference. If it reports waiting for integration, write a handoff and end until restarted. "
               "After acquiring the place, abort any unfinished merge in the project integration checkout before integrating again." % (env.name, plan))
    order = [("Run the compile check with bzz compile-check --project %s --plan %s: it runs the command compileCheck of the project configuration in your worktree and stores the result for your current commit. "
              "Do not run that command yourself; bzz ready accepts only this result and only for the commit your worktree is at, so run it again after every new commit. Then register your own branch with bzz ready."
              % (env.name, plan) if on["compileCheck"] else "Register your own branch with bzz ready."), acquire]
    if states["Deploy"] == "queen":
        order.append("If the release worktree of the project is missing, create it again and build it according to the integration rules of the project; only the Queen who holds the integration place may do this.")
    order.append("Integrate your own branch, then release the integration place with bzz integration release --project %s --plan %s." % (env.name, plan))
    text = "Then follow this order. " + " ".join("%d. %s" % (i + 1, step) for i, step in enumerate(order))
    text += " If only open owner questions remain, write a handoff and end your final report with WAITING_FOR_OWNER."
    if not on["compileCheck"]:
        text += " The compile check is switched off for this Project: do not run it."
    text += " " + _step_sentence("Build", BUILD, states["Build"], "before bzz ready", "bzz ready is refused")
    deploy_held = "the end of the plan is held back" if states["Deploy"] == "owner" else "bzz integration release and the end of the plan are held back"   # the place is not held for an owner answer
    text += " " + _step_sentence("Deploy", DEPLOY, states["Deploy"], "after you integrated and before you release the integration place", deploy_held)
    if not on["build"]:
        text += " A package marked verify build is closed like a code package (compile check and evidence); never report build as a wait."
    text += " These switches win over project rules and over the configured integration steps below."
    return text


# One sentence about a Build or Deploy step.
def _step_sentence(capability, package, state, when, held):
    """What the Queen does for a step in the given state."""
    if state == "off":
        return "The %s step is switched off for this Project: do not perform it." % capability
    if state == "queen":
        return "The %s step is switched on and it is yours: do it yourself %s, according to the integration rules of the project." % (capability, when)
    if state == "package":
        return ("The %s step is switched on but your provider lacks %s: it is package %s of this plan, for a provider that has %s. Start an Agent for it with bzz agent start %s; until it is done %s."
                % (capability, capability, package, capability, when, held))
    return ("The %s step is switched on but no configured provider has %s: the owner has been asked (item %s); until a provider has it, the step is switched off or his answer lets you set package %s done, %s. "
            "Go on with everything else, then write a handoff and end your final report with WAITING_FOR_OWNER." % (capability, capability, package, package, held))


# The project integration configuration a Queen gets.
def configuration(env, plan):
    """Branch, remote, worktree location and the settings of the switched-on steps; a configured integrator prompt is expanded with the same values."""
    s = env.settings
    on = switches(s)
    config = {"integrationBranch": str(s.get("integrationBranch") or "main"), "remoteName": str(s.get("remoteName") or "origin"),
              "worktreeLocation": str(s.get("worktreeLocation") or "beside the repository")}
    if on["compileCheck"] and s.get("compileCheck"):
        config["compileCheck"] = s["compileCheck"]
    if on["build"] or on["deploy"]:
        config.update({key: s[key] for key in ("integratorInstructions", "buildFlowDoc") if s.get(key)})
    return config


# The owner topics in the Queen's start text.
def owner_topics_text(env):
    """The sentence with the topics that always go to the owner; empty without topics."""
    topics = [str(t).strip() for t in (env.settings.get("ownerTopics") or []) if str(t).strip()]
    if not topics:
        return ""
    return ("Questions on these topics of this Project always go to the owner with bzz ask, even a technical question you could decide yourself; this wins over your own triage: "
            + json.dumps(topics, ensure_ascii=False))
