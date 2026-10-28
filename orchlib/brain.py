"""Brain: the one language-model call of a round, made only when an event needs judgement.

Ports the brain part of round.ps1: builds the prompt from the tool rules, the project rules (AGENTS.md of the workspace) and the events,
runs the Claude Code CLI headless (stream-json), reads the answer (a JSON object with an "actions" list) and the token numbers, and
records the call as a usage row of kind `brain`.
"""
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import runtime
from . import scheduler as sch

TOOLS_DIR = Path(__file__).resolve().parent.parent
NO_ANSWER = {"answer": None, "fail": None, "context": None, "tokens_in": None, "tokens_out": None, "model": "", "seconds": 0, "envelope": None}


def instructions_text(env, tools_dir=None) -> str:
    """Brain instructions: the tool rules (ORCHESTRATOR.md, else docs/AGENT-RULES.md) plus the project rules file AGENTS.md of the workspace."""
    base = Path(tools_dir or TOOLS_DIR)
    custom = (env.settings.get("brain") or {}).get("instructions")
    first = next((p for p in ([Path(custom)] if custom else []) + [base / "ORCHESTRATOR.md", base / "docs" / "AGENT-RULES.md"] if p.is_file()), None)
    text = first.read_text(encoding="utf-8-sig") if first else ""
    rules = env.workspace / "AGENTS.md"
    if rules.is_file():
        text += "\n\n" + rules.read_text(encoding="utf-8-sig")
    return text


def build_prompt(env, delta: dict, tools_dir=None) -> str:
    """Full brain prompt: instructions, then the events as JSON, then the request for the JSON object."""
    return (instructions_text(env, tools_dir) + "\n\n# EVENTS (JSON)\n\n" + json.dumps(delta, indent=2, ensure_ascii=False)
            + "\n\nAnswer now with the JSON object only.")


def brain_command(env) -> list:
    """The brain command line (without the prompt): executable and arguments with {brainModel}, {brainEffort} and {repo} replaced."""
    b = env.settings.get("brain") or {}
    cmd = b.get("command", "claude")
    exe = list(cmd) if isinstance(cmd, list) else [shutil.which(str(cmd)) or str(cmd)]
    repl = {"{brainModel}": str(b.get("model", "sonnet")), "{brainEffort}": str(b.get("effort", "medium")), "{repo}": str(env.workspace)}
    args = ["-p"] if not isinstance(cmd, list) else []
    for a in b.get("args", []):
        a = str(a)
        for k, v in repl.items():
            a = a.replace(k, v)
        args.append(a)
    return exe + args


def parse_stream(stdout: str, conn=None) -> dict:
    """Read the stream-json lines: the result event, the last assistant usage and the rate-limit windows (saved when a connection is given)."""
    last_use = envelope = None
    for ln in stdout.split("\n"):
        if not ln.strip():
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if ev.get("type") == "rate_limit_event":
            if conn is not None:
                sch.save_claude_usage(conn, ev)
        elif ev.get("type") == "assistant" and (ev.get("message") or {}).get("usage"):
            last_use = ev["message"]["usage"]
        elif ev.get("type") == "result":
            envelope = ev
    return {"envelope": envelope, "last_use": last_use}


def _sum_input(u: dict) -> int:
    """Input tokens of a usage block including the cache parts."""
    return int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0))


def parse_answer(text: str):
    """Cut the JSON object out of the answer text; returns (answer dict or None, error text or None)."""
    a0, a1 = text.find("{"), text.rfind("}")
    if a0 < 0 or a1 <= a0:
        return None, "brain answer has no JSON"
    try:
        answer = json.loads(text[a0:a1 + 1])
    except ValueError as exc:
        return None, "brain JSON invalid: %s" % exc
    if not isinstance(answer, dict) or answer.get("actions") is None:
        return None, 'brain answer has no "actions" list'
    return answer, None


def ask(env, delta: dict, new_plan: bool, tools_dir=None, runner=None) -> dict:
    """Ask the brain once. Returns {answer, fail, context, tokens_in, tokens_out, model, seconds, envelope}.

    Without a new-plan event the brain gets no tools (rule 12: the events already carry all data, one turn). `runner(argv, prompt, cwd, timeout)`
    replaces the subprocess call in tests and returns (exit code, stdout, stderr, timed_out). Token numbers come only from the stream; missing
    fields stay None.
    """
    t0 = time.time()
    argv = brain_command(env)
    if "--tools" in argv and not new_plan:
        argv[argv.index("--tools") + 1] = ""
    prompt = build_prompt(env, delta, tools_dir)
    timeout = int((env.settings.get("brain") or {}).get("timeoutSeconds", 1500))
    res = dict(NO_ANSWER)
    run = runner or _run
    code, out, err, timed_out = run(argv, prompt, str(env.workspace), timeout)
    parsed = parse_stream(out, env.conn) if not (timed_out or code != 0) else {"envelope": None, "last_use": None}
    env_ev, last_use = parsed["envelope"], parsed["last_use"]
    if env_ev and env_ev.get("usage"):
        res["tokens_in"], res["tokens_out"] = _sum_input(env_ev["usage"]), int(env_ev["usage"].get("output_tokens") or 0)
    if last_use:
        res["context"] = _sum_input(last_use)
    elif res["tokens_in"] is not None:
        res["context"] = res["tokens_in"]
    if env_ev and env_ev.get("modelUsage"):
        res["model"] = sch.model_label(next(iter(env_ev["modelUsage"])))
    res["envelope"] = env_ev
    if timed_out or code != 0:
        res["fail"] = "brain failed (exit %s, timeout %s): %s" % (code, timed_out, err[:300])
    elif not env_ev:
        res["fail"] = "brain run had no result event"
    else:
        res["answer"], res["fail"] = parse_answer(str(env_ev.get("result") or ""))
    res["seconds"] = int(time.time() - t0)
    return res


def _run(argv, prompt, cwd, timeout):
    """Run the brain program with the prompt on stdin; returns (exit code, stdout, stderr, timed_out)."""
    try:
        r = subprocess.run(argv, input=prompt.encode("utf-8"), cwd=cwd, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired as exc:
        return -1, "", (exc.stderr or b"").decode("utf-8", "replace"), True
    except OSError as exc:
        return 127, "", str(exc), False


def record_call(env, trigger: str, result: dict, actions: str, is_error: bool = False) -> None:
    """Store one brain call as a usage row (kind brain): trigger, model, context, tokens, duration, actions, error flag."""
    runtime.usage_add(env.conn, kind="brain", project=env.name, trigger=trigger,
                      model=result.get("model") or sch.model_label((env.settings.get("brain") or {}).get("model", "sonnet")),
                      context=result.get("context"), tokens_in=result.get("tokens_in"), tokens_out=result.get("tokens_out"),
                      duration_s=result.get("seconds"), actions=actions, error=1 if is_error else 0)


def totals(conn, project: str = None) -> dict:
    """Summary of the brain usage: calls, input and output of today, the last row and the last row with tokens (all projects when none is named)."""
    rows = runtime.usage_list(conn, kind="brain", project=project)
    today = datetime.now().date().isoformat()
    day = [r for r in rows if str(r["ts"]).startswith(today)]
    with_tokens = [r for r in rows if r["tokens_in"] is not None]
    return {"Calls": len(day), "Input": sum(int(r["tokens_in"] or 0) for r in day), "Output": sum(int(r["tokens_out"] or 0) for r in day),
            "Last": rows[-1] if rows else None, "LastCall": with_tokens[-1] if with_tokens else None}
