"""The start context of a CLI (U-7) and its benchmark (U-8).

Start context = the context size of the first model turn of a Queen or Bee (everything the CLI sends at the start: its own system prompt and tools,
the role file, the project rules and the task). The runner stores it per start in `start_contexts`; the benchmark measures it on the click of the
button of a provider in the settings, per registered Apiary once with the HaiveQueen profile and once with the user's own configuration, and stores
it in `start_benchmarks` (a new measurement replaces the old one). Benchmark runs create no agent row and no usage row."""
import json
import os
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from . import config, db
from . import platform as osplat

TASK = "Reply with the single word OK. Do not use any tool and do not read any file."   # the same minimal task for every run: it does no work
KINDS = ("queen", "bee", "subbee")   # a Bee that runs as a subagent of its Queen is "subbee"
TIMEOUT_S = 300


# ---- the start of a Queen or Bee

def approx_tokens(text) -> int:
    """Approximate token count of a text (4 characters per token); HaiveQueen has no tokenizer, so its own parts are marked approximate."""
    return (len(str(text or "")) + 3) // 4


def record(conn, project, plan, agent, kind, provider, tokens, parts=None, started_at=None) -> None:
    """Store the start context of one Queen, Bee or subagent Bee; `parts` = {"role": n, "rules": {file: n}, "task": n} (approximate)."""
    conn.execute("INSERT INTO start_contexts(project,plan_path,agent,kind,provider,started_at,tokens,parts_json) VALUES (?,?,?,?,?,?,?,?)",
                 (project, plan or "", agent, kind, provider, started_at or db.now(), int(tokens), json.dumps(parts) if parts else None))


def approx_parts(role_text, rule_files, task_text) -> dict:
    """The parts of HaiveQueen in a start context as approximate counts: the role file, every project rules file and the task.
    `rule_files` = [(label, text)]; the task is the message the CLI is started with (not the prompt file it reads afterwards: that is no part of the first turn)."""
    return {"role": approx_tokens(role_text), "rules": {label: approx_tokens(text) for label, text in rule_files}, "task": approx_tokens(task_text)}


# ---- the benchmark

def store_benchmark(conn, provider, project, mode, tokens, when=None) -> None:
    """Store one measured value; it replaces the older value of the same provider, Apiary and mode."""
    conn.execute("INSERT INTO start_benchmarks(provider,project,mode,tokens,measured_at) VALUES (?,?,?,?,?) "
                 "ON CONFLICT(provider,project,mode) DO UPDATE SET tokens=excluded.tokens, measured_at=excluded.measured_at",
                 (provider, project, mode, int(tokens), when or db.now()))


def benchmarks(conn, provider=None) -> list:
    """The stored benchmark values as dicts (provider, project, mode, tokens, measured_at), optionally of one provider."""
    sql, args = "SELECT * FROM start_benchmarks", ()
    if provider:
        sql, args = sql + " WHERE provider=?", (provider,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY provider, project, mode", args)]


def _tok(n) -> str:
    """Compact token count (18k)."""
    return "%dk" % int(n / 1e3 + 0.5) if n >= 1000 else str(int(n))


def pairs(conn) -> list:
    """The complete measurements: one per provider and Apiary that has both values, as dicts (provider, project, hq, own, saving in percent as a float, measured_at = the newer
    of the two times). A pair with only one value or an own value of 0 is no measurement."""
    got = {}
    for b in benchmarks(conn):
        got.setdefault((b["provider"], b["project"]), {})[b["mode"]] = b
    out = []
    for (provider, project), m in got.items():
        if "hq" in m and "own" in m and m["own"]["tokens"] > 0:
            out.append({"provider": provider, "project": project, "hq": m["hq"]["tokens"], "own": m["own"]["tokens"],
                        "saving": 100.0 * (m["hq"]["tokens"] - m["own"]["tokens"]) / m["own"]["tokens"], "measured_at": max(m["hq"]["measured_at"], m["own"]["measured_at"])})
    return out


def comparison(hq, own) -> str:
    """'HaiveQueen 18k, own profile 61k, -70 %' for the two values, or 'no benchmark yet' when one of them is missing."""
    if not hq or not own:
        return "no benchmark yet"
    return "HaiveQueen %s, own profile %s, %+d %%" % (_tok(hq), _tok(own), round(100 * (hq - own) / own))


def benchmark_text(conn, provider, project) -> str:
    """The benchmark comparison of one provider and Apiary, or 'no benchmark yet'."""
    got = {r["mode"]: r["tokens"] for r in conn.execute("SELECT mode,tokens FROM start_benchmarks WHERE provider=? AND project=?", (provider, project))}
    return comparison(got.get("hq"), got.get("own"))


def code_folder(conn, project) -> str:
    """The code folder of a registered Apiary: its codeRepo, else its workspace; '' when it has neither."""
    row = conn.execute("SELECT workspace FROM projects WHERE name=?", (project,)).fetchone()
    ws = row[0] if row and row[0] else ""
    if not ws:
        return ""
    try:
        return str(config.load_project(ws).get("codeRepo") or ws)
    except Exception:
        return ws


def benchmark(conn, cfg, provider, measure=None, progress=None) -> dict:
    """Measure the start context of `provider` in every registered Apiary, once with the HaiveQueen profile and once without it.

    Returns {"excluded": bool, "errors": [text], "results": [{"project", "mode", "tokens"}]}. An excluded provider (machine-wide excludedProviders) is not measured.
    A failed run is reported in `errors` and stores nothing (an older value of it stays); the other run of the pair is stored. `measure(prov, cwd, hq, task)` is
    the measurement (default measure_cli; tests pass a fake). `progress(text)` is called before each run with '2/4: ODP with the HaiveQueen profile ...'."""
    out = {"excluded": False, "errors": [], "results": []}
    if provider in [str(x) for x in cfg.get("excludedProviders") or []]:
        out["excluded"] = True
        return out
    from . import agent_runner
    prov = agent_runner.get_provider(cfg, provider)
    if not prov:
        out["errors"].append("provider %s is not in config.toml" % provider)
        return out
    measure = measure or measure_cli
    names = [r[0] for r in conn.execute("SELECT name FROM projects ORDER BY name").fetchall()]
    total, count = 2 * len(names), 0
    for project in names:
        cwd = code_folder(conn, project)
        if not cwd or not Path(cwd).is_dir():
            out["errors"].append("%s: no code folder" % project)
            count += 2
            continue
        for mode, hq in (("hq", True), ("own", False)):
            count += 1
            label = "%s %s" % (project, "with the HaiveQueen profile" if hq else "without it")
            if progress:
                try:
                    progress("%d/%d: %s ..." % (count, total, label))
                except Exception:
                    pass
            try:
                tokens = int(measure(prov, cwd, hq, TASK))
            except Exception as exc:   # one failed run must not stop the others
                out["errors"].append("%s: %s" % (label, exc))
                continue
            store_benchmark(conn, provider, project, mode, tokens)
            out["results"].append({"project": project, "mode": mode, "tokens": tokens})
    return out


def own_argv(prov, task) -> list:
    """The command line of a run in the user's own configuration: the CLI with its plain non-interactive JSON mode and the task, nothing of the HaiveQueen profile."""
    cmd = prov["command"]
    fmt = prov.get("format") or prov["protocol"]
    if fmt == "claude":
        return [cmd, "-p", task, "--output-format", "stream-json", "--verbose"]
    if fmt == "codex":
        return [cmd, "exec", "--json", task]
    return [cmd, "run", "--format", "json", task]


def kill_tree(proc) -> None:
    """Kill the process of a run and its children (on Windows a .cmd shim runs the CLI as a child), then wait for it; never raises."""
    try:
        if os.name == "nt" and isinstance(getattr(proc, "pid", None), int):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=20)
    except Exception:
        pass


def remove_folder(path) -> None:
    """Remove a temp folder; a file that is still held is retried a few times, then left to the system temp cleanup. Never raises: the temp folder is no part of the result."""
    import shutil
    import time
    for _ in range(5):
        try:
            shutil.rmtree(path)
            return
        except Exception:
            if not Path(path).exists():
                return
            time.sleep(0.3)
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def measure_cli(prov, cwd, hq, task) -> int:
    """Run the CLI of `prov` once in `cwd` with `task` and return the context size of its first model turn; raises with the reason when there is none.

    hq=True runs it as a Bee is run (HaiveQueen profile folder and arguments), hq=False in the user's own configuration. The process tree is ended and waited
    for before the temp folder is removed; a temp folder that cannot be removed never changes the result."""
    tmp = tempfile.mkdtemp(prefix="hq-bench-")
    try:
        return _measure(prov, cwd, hq, task, tmp)
    finally:
        remove_folder(tmp)


def _measure(prov, cwd, hq, task, tmp) -> int:
    """The measurement of measure_cli in the temp folder `tmp`."""
    import shutil
    from . import agent_runner as ar, profile
    run = ar.AgentRun(None, "", "benchmark", prov["name"], prov["protocol"], cwd, tmp, phase="benchmark", out=open(os.devnull, "w"))
    run.format = prov["format"]
    cmd = shutil.which(prov["command"]) or prov["command"]
    if hq:
        if not profile.prepare(prov):
            raise RuntimeError("the CLI cannot be kept to the HaiveQueen profile")
        cfg = config.load_config()
        model, effort = ar.class_model(prov, "medium")
        rules = Path(tmp) / "rules.md"
        rules.write_text("", encoding="utf-8")
        vars_ = {"model": model, "effort": effort, "repo": cwd, "workspace": cwd, "tools": str(ar.TOOL_ROOT), "cwd": cwd,
                 "claudeTools": cfg.get("claudeTools", ""), "rules": str(rules)}
        args = ar.build_args(prov, vars_)
        if prov["protocol"] == "claude":
            if "--setting-sources" in args:
                args[args.index("--setting-sources") + 1] = ""
            else:
                args += ["--setting-sources", ""]
        argv = [cmd, "-p", task] + args if prov["protocol"] == "claude" else [cmd] + args + [task]
        env = profile.environment(prov)
    else:
        argv, env = own_argv(prov, task), dict(os.environ)
        argv[0] = cmd
    err_path = Path(tmp) / "stderr.txt"
    code = None
    with open(str(err_path), "wb") as err:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
        timer = threading.Timer(TIMEOUT_S, lambda: kill_tree(proc))
        timer.daemon = True
        timer.start()
        try:
            for bline in proc.stdout:
                try:
                    e = json.loads(bline.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(e, dict):
                    run.handle_event(e)
                if run.start_ctx:
                    break   # the first turn is all that is measured; the task is not carried on
        finally:
            timer.cancel()
            kill_tree(proc)   # the whole tree is gone and waited for before the folder is removed
            code = getattr(proc, "returncode", None)
    if run.start_ctx:
        return int(run.start_ctx)
    try:
        tail = " ".join(err_path.read_text(encoding="utf-8", errors="replace").split()[-40:]) if err_path.is_file() else ""
    except OSError:
        tail = ""
    why = "; ".join(run.api_errors[-1:]) or tail or "no model turn (exit %s)" % code
    raise RuntimeError(why[:300])


def started_today(now=None) -> str:
    """ISO time of the start of today (local)."""
    return (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
