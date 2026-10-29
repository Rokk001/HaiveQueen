"""Runs ONE agent (Claude Code or Codex, headless) in its own pane (port of agent-run.ps1, pane-wrap.ps1 and the agent part of build-sampler.ps1).

The agent is tracked in the agents table; the pane shows a fixed header (haivequeen.header) and below it only milestones with a
time stamp; the full raw output stays in <runs dir>/<agent>/ (events.jsonl, stderr.txt, milestones.txt, final.txt, rules.md).

Usage:
  python -m haivequeen.agent_runner --project P --name N --provider claude --cwd DIR --prompt-file FILE
         [--plan PATH] [--package ID] [--phase work] [--runs-dir DIR] [--model M] [--effort E]
  python -m haivequeen.agent_runner --wrap ...same arguments...   (pane wrapper: runs the agent as a child, keeps the pane open
                                                                 for the close delay, then exits 0)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
from datetime import datetime
from pathlib import Path

from . import buildjobs, config, db, header, runtime
from . import platform as osplat

TOOL_ROOT = Path(__file__).resolve().parent.parent
CLASSES = ("light", "medium", "heavy")
CONTEXT_LIMIT = 300000   # context size at which the HANDOFF flag file is written (rule 13)
MS_COLOURS = ("White", "Green", "Red", "Cyan", "Yellow", "Gray")
DEFAULT_STALL_SECONDS = 3600   # an agent that prints nothing for this long is marked stalled, never ended (config.toml agentStallSeconds, 0 = off)


# ---------------------------------------------------------------- small text helpers

def short(text, limit=180):
    """Return a text as one line, cut to `limit` characters (a cut text ends with "...")."""
    text = " ".join(str(text or "").split())
    return text[:limit] + "..." if len(text) > limit else text


def model_label(model):
    """Return a readable model name: claude-sonnet-5-5 -> Sonnet 5.5, gpt-6.1-sol -> GPT-6.1-Sol."""
    m = re.match(r"^claude-([a-z]+)-(\d+)-(\d+)", model or "")
    if m:
        return "%s %s.%s" % (m.group(1).capitalize(), m.group(2), m.group(3))
    parts = (model or "").split("-")
    out = []
    for i, x in enumerate(parts):
        if i == 0:
            out.append(x.upper())
        elif x[:1].isdigit():
            out.append(x)
        else:
            out.append(x.capitalize())
    return "-".join(out)


def model_short(provider_type, model, effort):
    """Return the short model text of the first header line: "Son 5.5 med." / "Sol 6.1 med."."""
    eff = "med." if effort == "medium" else effort
    if provider_type == "codex":
        m = re.match(r"^gpt-(\d+(?:\.\d+)?)-([a-z]+)", model or "")
        if m:
            return "%s %s %s" % (m.group(2).capitalize(), m.group(1), eff)
        return "%s %s" % (model, eff)
    name, ver = model, ""
    for key, label in (("sonnet", "Son"), ("opus", "Opus"), ("haiku", "Hai")):
        if key in (model or ""):
            name, ver = label, "5.5"
    m = re.search(r"(\d+)[-.](\d+)", model or "")
    if m:
        ver = "%s.%s" % (m.group(1), m.group(2))
    return ("%s %s" % (name, ver)).strip() + " " + eff


def class_index(cls):
    """Return the index of an effort class (light < medium < heavy); empty or unknown = medium."""
    c = str(cls or "").strip().lower()
    return CLASSES.index(c) if c in CLASSES else 1


# ---------------------------------------------------------------- providers (machine config)

def get_providers(cfg):
    """Return the provider entries of config.toml as dicts (name, protocol, command, args, classes, endpoint)."""
    out = []
    for p in cfg.get("providers") or []:
        name = str(p.get("name") or "")
        if not name:
            raise ValueError("a provider entry has no name")
        proto = str(p.get("protocol") or name)
        if proto not in ("claude", "codex"):
            raise ValueError("provider %s: protocol must be claude or codex, not '%s'" % (name, proto))
        out.append({"name": name, "protocol": proto, "command": str(p.get("command") or proto),
                    "args": [str(x) for x in (p.get("args") or [])], "classes": p.get("classes") or {},
                    "endpoint": p.get("endpoint") or None})
    return out


def get_provider(cfg, name):
    """Return the provider entry with this name, or None."""
    return next((p for p in get_providers(cfg) if p["name"] == name), None)


def endpoint_args(prov):
    """Return the extra arguments of a provider with an endpoint (a model_provider of the codex CLI)."""
    ep = prov.get("endpoint")
    if not ep or not ep.get("baseUrl"):
        return []
    pid = str(ep.get("id") or prov["name"])
    nm = str(ep.get("name") or prov["name"])
    return ["-c", "model_provider=" + pid, "-c", "model_providers.%s.name=%s" % (pid, nm),
            "-c", "model_providers.%s.base_url=%s" % (pid, ep["baseUrl"]),
            "-c", "model_providers.%s.wire_api=responses" % pid]


def class_model(prov, cls):
    """Return (model, effort) of a provider for a class; a class the entry does not list falls back to the nearest lower, else higher one."""
    i = class_index(cls)
    classes = (prov or {}).get("classes") or {}
    for k in list(range(i, -1, -1)) + list(range(i + 1, 3)):
        m = classes.get(CLASSES[k])
        if m and m.get("model"):
            return str(m["model"]), str(m.get("effort") or "")
    return "", ""


# ---------------------------------------------------------------- command classification

def classify_cmd(cmd, queue_re=None):
    """Recognise a milestone command (commit, push, compile test, check, ready line, file write); returns {kind, label} or None.

    A call of `bzz ready` is the ready line; queue_re (optional) additionally marks writes into a legacy queue file.
    """
    cmd = str(cmd or "")
    if re.search(r"git\s+commit", cmd):
        m = re.search(r'-m\s+"([^"\r\n$]{3,})"', cmd) or re.search(r"-m\s+'([^'\r\n]{3,})'", cmd)
        return {"kind": "commit", "label": ("Commit: " + short(m.group(1), 100)) if m else "Commit"}
    if re.search(r"git\s+push", cmd):
        return {"kind": "push", "label": "Push"}
    if "compile-check-changed" in cmd:
        return {"kind": "check", "label": "Compile test"}
    m = re.search(r"(check[\w\-]*\.(py|ps1))", cmd)
    if m:
        return {"kind": "check", "label": "Check " + m.group(1)}
    if re.search(r"(?:bzz|cli.py)[\"']?\s+ready(\s|$)", cmd):
        return {"kind": "ready", "label": "ready line registered"}
    if queue_re and re.search(queue_re, cmd) and re.search(r">>|Add-Content|Out-File|Set-Content", cmd):
        return {"kind": "ready", "label": "ready line registered"}
    if re.search(r"(Set-Content|Add-Content|Out-File|WriteAllText|sed\s+-i|apply_patch)", cmd):
        m = re.search(r"""(?:-(?:Path|LiteralPath|FilePath)\s+|WriteAllText\(|sed\s+-i\S*\s+(?:'[^']*'\s+|"[^"]*"\s+)?)['"]?([^'"\s,)]+\.[A-Za-z0-9]+)""", cmd)
        if m:
            return {"kind": "edit", "label": "changed: " + m.group(1).replace("\\\\", "\\")}
    return None


# ---------------------------------------------------------------- Codex session log

def codex_sessions_dir():
    """Return the folder of the Codex session logs (BZZ_CODEX_SESSIONS overrides it, used by tests)."""
    env = os.environ.get("BZZ_CODEX_SESSIONS")
    if env:
        return Path(env)
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"


def get_codex_tokens(thread_id, sessions=None):
    """Return live token numbers {ctx, tin, tout} of a running Codex agent from its session log, or None when not available yet."""
    if not thread_id:
        return None
    try:
        root = Path(sessions) if sessions else codex_sessions_dir()
        files = list(root.rglob("*%s.jsonl" % thread_id))
        if not files:
            return None
        with open(str(files[0]), "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 300 * 1024))
            data = handle.read().decode("utf-8", errors="replace")
        lines = [ln for ln in data.split("\n") if '"type":"token_count"' in ln and "last_token_usage" in ln]
        if not lines:
            return None
        info = json.loads(lines[-1])["payload"]["info"]
        return {"ctx": int(info["last_token_usage"]["input_tokens"]), "tin": int(info["total_token_usage"]["input_tokens"]),
                "tout": int(info["total_token_usage"]["output_tokens"])}
    except Exception:
        return None


# ---------------------------------------------------------------- build jobs

def sample_once(path, process_names):
    """Append one sample line "ISO time;free MB;compiler process count" to the sample file."""
    line = "%s;%d;%d\n" % (datetime.now().isoformat(), osplat.free_ram_mb(), osplat.count_processes(process_names) if process_names else 0)
    with open(str(path), "a", encoding="utf-8") as handle:
        handle.write(line)


def sampler_loop(path, process_names, stop, seconds):
    """Sample free RAM and compiler processes every `seconds` seconds until `stop` is set (BZZ_SAMPLE_SEC overrides, tests only)."""
    while True:
        try:
            sample_once(path, process_names)
        except Exception:
            pass
        if stop.wait(seconds):
            break


def stall_watch(last_output, limit, on_stall, on_resume, stop):
    """Report an agent that printed nothing for `limit` seconds; `last_output[0]` is the monotonic time of its last line.

    Calls `on_stall(silent seconds)` once per silent period and `on_resume()` when the agent prints again. It never ends
    anything: a running agent is only marked. Stops when `stop` is set.
    """
    poll = min(5.0, max(0.2, limit / 4.0))
    flagged = False
    while not stop.wait(poll):
        silent = time.monotonic() - last_output[0]
        if silent > limit and not flagged:
            flagged = True
            on_stall(int(silent))
        elif silent <= limit and flagged:
            flagged = False
            on_resume()


def set_stalled(a, value, text):
    """Write the stalled mark (a time, or "" to clear it) into the agent's row and log one event; uses its own connection because it runs in the watch thread."""
    try:
        conn = db.connect(config.db_path())
        try:
            runtime.upsert_agent(conn, a.project, a.name, {"stalledAt": value})
            runtime.append_event(conn, a.project, text, kind="warning", agent=a.name, mirror="rounds")
        finally:
            conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------- rule file of a phase

def markdown_block(lines, prefix):
    """Return the text of the first markdown block whose heading line starts with `prefix`; headings inside code fences are ignored."""
    level = len(re.match(r"^(#+)", prefix).group(1)) if re.match(r"^(#+)", prefix) else 1
    start, fence = -1, False
    for i, ln in enumerate(lines):
        if re.match(r"^\s*```", ln):
            fence = not fence
            continue
        if not fence and ln.startswith(prefix):
            start = i
            break
    if start < 0:
        return ""
    end, fence = len(lines), False
    for j in range(start + 1, len(lines)):
        if re.match(r"^\s*```", lines[j]):
            fence = not fence
            continue
        m = re.match(r"^(#+) ", lines[j])
        if not fence and m and len(m.group(1)) <= level:
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()


def write_phase_rules(pcfg, workspace, phase, out_file):
    """Write the rule file of one agent phase and return the number of sections written.

    project.toml may name `ruleSources` (name -> file, relative to the workspace) and `phaseRules` (phase -> name -> list of heading
    prefixes); the listed sections are copied verbatim, and a listed section that does not exist raises an error, so a renamed
    heading never silently drops a rule. Without `phaseRules` the whole AGENTS.md of the workspace is the rule file.
    """
    ws = Path(workspace) if workspace else Path(".")
    rules = pcfg.get("phaseRules")
    parts, n = [], 0
    if rules:
        sel = rules.get(phase)
        if sel is None and phase == "triage":
            sel = rules.get("verify")   # a triage agent only reads and decides, so it follows the verify rules unless project.toml names its own
        if sel is None:
            raise ValueError("no phaseRules for phase '%s' in project.toml" % phase)
        sources = pcfg.get("ruleSources") or {}
        for src, prefixes in sel.items():
            path = ws / str(sources.get(src, src))
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            for pre in prefixes:
                block = markdown_block(lines, str(pre))
                if not block:
                    raise ValueError("rule section '%s' not found in %s" % (pre, path))
                parts.append(block)
                n += 1
    elif (ws / "AGENTS.md").is_file():
        parts.append((ws / "AGENTS.md").read_text(encoding="utf-8-sig").rstrip())
        n = 1
    Path(out_file).write_text("# Rules for this agent phase (%s)\n\n%s\n" % (phase, "\n\n".join(parts)), encoding="utf-8")
    return n


# ---------------------------------------------------------------- the run

def bzz_script_dirs():
    """Return the folders that may hold the `bzz` console script of the running Python (Scripts folder, sysconfig scripts folder, `which bzz`)."""
    dirs = [Path(sys.executable).parent / "Scripts", Path(sys.executable).parent]
    try:
        dirs.append(Path(sysconfig.get_path("scripts")))
    except (KeyError, TypeError):
        pass
    found = shutil.which("bzz")
    if found:
        dirs.append(Path(found).parent)
    return dirs


def with_bzz_on_path(env, dirs=None):
    """Put the folder of the `bzz` console script at the front of PATH in `env` (a dict, changed in place and returned).

    Only a folder that holds a bzz program is added, and only when it is not on PATH yet, so there are no duplicate entries.
    `dirs` replaces the folders to look at (tests).
    """
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    current = [p for p in (env.get(key) or "").split(os.pathsep) if p]
    known = set(os.path.normcase(os.path.normpath(p)) for p in current)
    for d in (bzz_script_dirs() if dirs is None else dirs):
        d = Path(d)
        if not any((d / n).is_file() for n in ("bzz", "bzz.exe", "bzz.cmd", "bzz.bat")):
            continue
        if os.path.normcase(os.path.normpath(str(d))) in known:
            return env
        env[key] = str(d) + os.pathsep + os.pathsep.join(current)
        return env
    return env


def child_env():
    """Return the environment for child processes of the runner: the tool folder is on PYTHONPATH so `-m haivequeen...` works, `bzz` is found on PATH."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TOOL_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return with_bzz_on_path(env)


def task_text(conn, project, plan, package, phase, plan_short):
    """Return the task for the header: work = package row of the plan (id and title); verify / release / integrate = phase plus package."""
    pkg_txt = "package " + package if package and package != "ALL" and not re.match(r"^(REL|INT-)", package) else "whole plan"
    if phase == "verify":
        return "Verify %s, completeness check" % pkg_txt
    if phase == "release":
        return "Release: merge plan branch, compile test, ready line (%s)" % plan_short
    if phase == "integrate":
        return "Integrator: merge ready lines, build, checks, push"
    try:
        key = str(plan).replace("\\", "/")
        for pk in runtime.list_packages(conn, project):
            pp = str(pk.get("plan_path") or "").replace("\\", "/")
            if pk["id"] == package and key and (pp == key or key.endswith("/" + pp) or pp.endswith("/" + key)):
                return "%s: %s" % (package, pk.get("title") or "")
    except Exception:
        pass
    return ""


def task_line(prompt_file):
    """Return the task in one line from the prompt file: the first line after the TASK heading (else its first line)."""
    try:
        lines = [ln.lstrip("﻿") for ln in Path(prompt_file).read_text(encoding="utf-8").splitlines()]
    except OSError:
        return ""
    cand = [ln for ln in (lines[lines.index("TASK") + 1:] if "TASK" in lines else lines) if ln.strip()]
    return short(cand[0], 300) if cand else ""


class AgentRun:
    """State and behaviour of one agent run; `handle_event` parses the event stream of either CLI."""

    def __init__(self, conn, project, name, provider, protocol, cwd, run_dir, plan="", package="", phase="work",
                 out=None, queue_re=None, sessions=None):
        """Remember the run parameters; `out` is where pane text goes (default stdout)."""
        self.conn, self.project, self.name = conn, project, name
        self.provider, self.type = provider, protocol
        self.cwd, self.run_dir = str(cwd), Path(run_dir)
        self.plan, self.package, self.phase = plan, package, phase
        self.out = out or sys.stdout
        self.queue_re, self.sessions = queue_re, sessions
        self.thread = ""
        self.tin, self.tout = (None, None) if protocol == "codex" else (0, 0)   # Codex reports its token totals only in the session log
        self.ctx = 0
        self.seen = set()
        self.last = ""
        self.final = ""
        self.ok = False
        self.model = ""
        self.pending = {}
        self.last_said = ""
        self.last_write = 0.0
        self.started = datetime.now()
        self.ms_file = self.run_dir / "milestones.txt"

    # --- pane output

    def width(self):
        """Return the pane width in columns."""
        return max(30, shutil.get_terminal_size((120, 40)).columns)

    def say(self, text, colour="White"):
        """Write one milestone line with the time (wrapped, not cut) and keep it in milestones.txt for the header process."""
        text = str(text)
        if len(text) > 1500:
            text = text[:1500] + "..."
        ts = datetime.now().strftime("%H:%M") + " "
        try:
            with open(str(self.ms_file), "ab") as handle:
                handle.write((colour + "\t" + ts + " ".join(text.split()) + "\n").encode("utf-8"))
        except OSError:
            pass
        first = True
        for line in header.wrap_text(text, self.width() - 6):
            self.out.write("%s%s%s%s[0m\n" % (header.sgr(colour), ts if first else "      ", line, header.ESC))
            first = False
        try:
            self.out.flush()
        except Exception:
            pass
        self.last_said = text

    def rel_path(self, p):
        """Return the path relative to the working directory when it lies inside it."""
        norm = os.path.normcase(p)
        base = os.path.normcase(self.cwd)
        for sep in ("\\", "/"):
            if norm.startswith(base + sep):
                return p[len(self.cwd) + 1:]
        return p

    def say_result(self, info, text, exit_code, is_err):
        """Write the result line of a milestone command (exit code, last output line)."""
        ex = exit_code
        if ex is None:
            m = re.search(r"EXIT=(\d+)", text)
            if m:
                ex = int(m.group(1))
        last_line = next((ln for ln in reversed(text.splitlines()) if ln.strip()), "")
        ok = (int(ex) == 0) if ex is not None else (not is_err)
        label = info["label"]
        if info["kind"] == "commit":
            m = re.search(r"\[[^\]]+ ([0-9a-f]{7,})\]", text)
            if m:
                label += " (" + m.group(1) + ")"
        msg = label + " - " + (("Exit %s" % ex) if ex is not None else ("OK" if ok else "Error")) + ((": " + short(last_line, 140)) if last_line else "")
        self.say(msg, "Green" if ok else "Red")

    # --- state in the database

    def push_state(self, force=False):
        """Write counters and last line into the agent's row (throttled to every 2 s unless forced)."""
        if not force and time.time() - self.last_write < 2:
            return
        self.last_write = time.time()
        if self.type == "codex" and self.thread:
            cx = get_codex_tokens(self.thread, self.sessions)
            if cx:
                self.ctx, self.tin, self.tout = cx["ctx"], cx["tin"], cx["tout"]
        size = shutil.get_terminal_size((120, 40))
        try:
            runtime.upsert_agent(self.conn, self.project, self.name, {
                "tokensIn": self.tin, "tokensOut": self.tout, "ctxTokens": self.ctx, "lastLine": self.last,
                "paneCols": size.columns, "paneRows": size.lines})
        except Exception:
            pass   # a failed write must not end the run

    def check_handoff(self):
        """Write the HANDOFF flag file when the current context reached the limit (the agent is never killed)."""
        flag = self.run_dir / "HANDOFF"
        if self.ctx and self.ctx >= CONTEXT_LIMIT and not flag.exists():
            try:
                flag.write_text("context %d reached %s\n" % (self.ctx, datetime.now().isoformat()), encoding="utf-8")
            except OSError:
                pass

    def set_agent_retry(self, props, attempts=3, delay=2.0):
        """Write the fields into the agent's row with up to `attempts` tries.

        When the last try fails, the row is set to failed with the reason (best effort), so a row never stays on
        starting / running because of a database problem. Returns True when the requested fields were written.
        """
        err = ""
        for i in range(1, attempts + 1):
            try:
                runtime.upsert_agent(self.conn, self.project, self.name, props)
                return True
            except Exception as exc:
                err = str(exc)
                if i < attempts:
                    time.sleep(delay)
        self.event("warning %s could not update its row after %d tries, set to failed: %s" % (self.name, attempts, err), "warning")
        try:
            runtime.upsert_agent(self.conn, self.project, self.name, {
                "status": "failed", "endedAt": db.now(), "lastLine": "agent row error after %d tries (wanted status '%s'): %s" % (attempts, props.get("status", ""), err)})
        except Exception:
            pass
        return False

    def event(self, text, kind):
        """Append an event to the history and the rounds log (a failure is ignored)."""
        try:
            runtime.append_event(self.conn, self.project, text, kind=kind, agent=self.name, mirror="rounds")
        except Exception:
            pass

    # --- event stream

    def handle_event(self, e):
        """Handle one parsed JSON event of either CLI."""
        if self.type == "claude":
            self._claude_event(e)
        else:
            self._codex_event(e)
        self.push_state(False)
        self.check_handoff()

    def _claude_event(self, e):
        """Handle one event of the Claude stream-json format."""
        et = e.get("type")
        msg = e.get("message") if isinstance(e.get("message"), dict) else None
        if et == "assistant" and msg:
            if msg.get("model"):
                self.model = model_label(str(msg["model"]))
            u = msg.get("usage")
            # One API message is streamed once per content block with the same id and usage: count each message id once. ctx = size of the latest context.
            if u and msg.get("id") and msg["id"] not in self.seen:
                self.seen.add(msg["id"])
                c1 = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                self.tin += c1
                self.tout += int(u.get("output_tokens") or 0)
                self.ctx = c1
            for c in msg.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text"):
                    self.last = short(c["text"])
                    self.say(str(c["text"]), "White")
                elif c.get("type") == "tool_use":
                    self.last = "tool: " + str(c.get("name"))
                    inp = c.get("input") or {}
                    if c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
                        if fp and self.queue_re and re.search(self.queue_re, fp):
                            self.say("ready line registered", "Green")
                        elif fp:
                            self.say("changed: " + self.rel_path(fp), "Cyan")
                    elif c.get("name") in ("Bash", "PowerShell"):
                        info = classify_cmd(str(inp.get("command") or ""), self.queue_re)
                        if info:
                            if info["kind"] == "edit":
                                self.say(info["label"], "Cyan")
                            else:
                                self.pending[str(c.get("id"))] = info
        elif et == "user" and msg:
            content = msg.get("content")
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and str(c.get("tool_use_id")) in self.pending:
                    info = self.pending.pop(str(c["tool_use_id"]))
                    cc = c.get("content")
                    txt = cc if isinstance(cc, str) else "\n".join(str(x.get("text", "")) for x in (cc or []) if isinstance(x, dict))
                    self.say_result(info, txt, None, bool(c.get("is_error")))
        elif et == "rate_limit_event":
            self.save_claude_usage(e)
        elif et == "result":
            u = e.get("usage")
            if u:
                self.tin = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                self.tout = int(u.get("output_tokens") or 0)
            self.final = str(e.get("result") or "")
            self.ok = not e.get("is_error")
            self.last = short(self.final)

    def _codex_event(self, e):
        """Handle one event of the Codex exec --json format."""
        et = e.get("type")
        item = e.get("item") if isinstance(e.get("item"), dict) else None
        if et == "thread.started" and e.get("thread_id"):
            self.thread = str(e["thread_id"])
        elif et == "item.completed" and item:
            it = item.get("type")
            if it == "agent_message":
                text = str(item.get("text") or "")
                if text.strip():   # an empty closing message (seen with the Codex model) must not wipe the last real answer
                    self.final = text
                    self.last = short(self.final)
                    self.say(self.final, "White")
            elif it == "command_execution":
                self.last = "cmd: " + short(item.get("command"))
                info = classify_cmd(str(item.get("command") or ""), self.queue_re)
                if info:
                    if info["kind"] == "edit":
                        self.say(info["label"], "Cyan")
                    else:
                        self.say_result(info, str(item.get("aggregated_output") or ""), item.get("exit_code"), False)
            elif it == "file_change":
                for ch in item.get("changes") or []:
                    if isinstance(ch, dict) and ch.get("path"):
                        self.say("changed: " + self.rel_path(str(ch["path"])), "Cyan")
        elif et == "turn.completed" and e.get("usage"):
            self.tin = int(e["usage"].get("input_tokens") or 0)
            self.tout = int(e["usage"].get("output_tokens") or 0)
            cx = get_codex_tokens(self.thread, self.sessions)
            if cx:
                self.ctx = cx["ctx"]
            self.ok = True
        elif et in ("turn.failed", "error"):
            self.last = "error: " + short(json.dumps(e, separators=(",", ":")))
            self.say(self.last, "Red")

    def save_claude_usage(self, e):
        """Remember the newest Claude usage windows (from a rate_limit_event) in the kv key usage-claude of the global scope."""
        info = e.get("rate_limit_info") or {}
        w = info.get("unifiedWindows")
        if not w:
            return
        try:
            runtime.kv_set(self.conn, "*", "usage-claude", json.dumps({"saved": datetime.now().isoformat(), "fiveHour": w.get("five_hour"), "sevenDay": w.get("seven_day")}))
        except Exception:
            pass


def report_prompt(prompt_file, project, agent_id):
    """Return the prompt handed to the CLI: read the prompt file, plus the report commands (bzz done / ready / ask / note). It has no double quote."""
    orch = "'%s' -I '%s'" % (osplat.python_executable(), TOOL_ROOT / "haivequeen" / "cli.py")
    text = ('Read the file "%s" completely and carry out exactly the task described there. Do nothing else.' % prompt_file)
    text += (" Report only with these commands, never by editing a queue file or state table by hand (the handoff text in the state file of the plan stays your own file): "
             "final report: %s done --agent %s --result <file with your final report>; "
             "branch ready for integration: %s ready --project %s --branch <branch> --tip <commit> --plan <plan path> --note <text>; "
             "question for the owner: %s ask --project %s --plan <plan path> --item <item> --question <question> --recommendation <recommendation>; "
             "note: %s note --project %s --text <text>.") % (orch, agent_id, orch, project, orch, project, orch, project)
    return text.replace('"', "")


def build_args(prov, vars_):
    """Return the argument list of the provider entry (plus the endpoint arguments) with {name} replaced by the variables."""
    def sub(arg):
        return re.sub(r"\{(\w+)\}", lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), arg)
    return [sub(a) for a in prov["args"] + endpoint_args(prov)]


def run_agent(a, out=None):
    """Run one agent as described by the parsed arguments; returns 0 (the status is in the agents table)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    out = out or sys.stdout
    cfg = config.load_config()
    conn = db.connect(config.db_path())
    prow = conn.execute("SELECT workspace FROM projects WHERE name=?", (a.project,)).fetchone()
    workspace = prow[0] if prow and prow[0] else ""
    pcfg = config.load_project(workspace) if workspace else dict(config.DEFAULT_PROJECT)
    runs = Path(a.runs_dir) if a.runs_dir else osplat.runs_dir()
    run_dir = runs / a.name
    run_dir.mkdir(parents=True, exist_ok=True)
    prov = get_provider(cfg, a.provider)
    if not prov:
        raise ValueError("provider '%s' is not in config.toml" % a.provider)
    protocol = prov["protocol"]
    prev = runtime.get_agent(conn, a.project, a.name)
    cls = (prev or {}).get("class") or "medium"
    model, effort = class_model(prov, cls)
    model = a.model or model or (prev or {}).get("model") or ""
    effort = a.effort or effort or (prev or {}).get("effort") or ""
    queue_name = pcfg.get("integrationStateFile")
    queue_re = re.escape(Path(str(queue_name)).stem) if queue_name else None
    run = AgentRun(conn, a.project, a.name, a.provider, protocol, a.cwd, run_dir, a.plan, a.package, a.phase, out, queue_re)
    run.model = model_label(model)
    plan_short = re.sub(r"-?PLAN$", "", Path(a.plan).stem) if a.plan else ""
    task = task_text(conn, a.project, a.plan, a.package, a.phase, plan_short) or task_line(a.prompt_file)
    events_file, err_file, final_file = run_dir / "events.jsonl", run_dir / "stderr.txt", run_dir / "final.txt"

    if prev and prev.get("pid") and int(prev["pid"]) != os.getpid() and osplat.process_alive(int(prev["pid"])):
        out.write("agent %s already runs (pid %s), this second pane ends\n" % (a.name, prev["pid"]))   # a relaunched pane must never start the task twice
        return 0
    size = shutil.get_terminal_size((120, 40))
    started = run.started
    if not run.set_agent_retry({"type": protocol, "provider": a.provider, "model": model, "effort": effort, "plan": a.plan,
                                "package": a.package, "phase": a.phase, "cwd": a.cwd, "status": "running",
                                "startedAt": started.isoformat(), "endedAt": None, "pid": os.getpid(), "tokensIn": run.tin,
                                "tokensOut": run.tout, "lastLine": "starting", "paneCols": size.columns, "paneRows": size.lines,
                                "modelShort": (prev or {}).get("modelShort") or model_short(protocol, model, effort),
                                "task": (prev or {}).get("task") or task}):
        out.write("agent %s not started: the agent row could not be written, row set to failed\n" % a.name)   # the model is never called
        return 0
    # The header is drawn by haivequeen.header (own process in this pane); this process only prints milestones below it.
    run.ms_file.write_text("", encoding="utf-8")
    header_proc = None
    if sys.stdout.isatty() and not os.environ.get("BZZ_NO_HEADER"):
        header_proc = subprocess.Popen([osplat.python_executable(), "-m", "haivequeen.header", "--project", a.project, "--name", a.name,
                                        "--parent-pid", str(os.getpid()), "--runs-dir", str(runs)], env=child_env())
        time.sleep(2)

    try:
        agent_row = runtime.get_agent(conn, a.project, a.name)
        prompt = report_prompt(a.prompt_file, a.project, agent_row["id"] if agent_row else "?")
        rules_file = run_dir / "rules.md"
        if protocol == "claude":
            write_phase_rules(pcfg, workspace, a.phase, rules_file)
        vars_ = {"model": model, "effort": effort, "repo": pcfg.get("codeRepo") or workspace or a.cwd, "workspace": workspace or a.cwd, "tools": str(TOOL_ROOT), "cwd": a.cwd,
                 "claudeTools": pcfg.get("claudeTools") or cfg.get("claudeTools") or "", "rules": str(rules_file)}
        arg_list = build_args(prov, vars_)
    except Exception as exc:   # a broken rule file or argument template must show up as a failed agent at once, not as a vanished runner one round later
        run.last = short("runner error: " + str(exc))
        run.say(run.last, "Red")
        run.set_agent_retry({"status": "failed", "endedAt": db.now(), "exitCode": 99, "lastLine": run.last})
        return 0

    # Build jobs: an agent that builds (integrate, release) gets the job count of this machine as environment variable and its RAM use is measured while it runs.
    bj_raw = cfg.get("buildJobs")
    bj = buildjobs.settings_from(None if isinstance(bj_raw, bool) else bj_raw) if a.phase in ("integrate", "release") else None
    bj_jobs, bj_start_mb, samples, stop, sampler = 0, 0.0, run_dir / "build-samples.txt", threading.Event(), None
    env = with_bzz_on_path(dict(os.environ))   # the agent and everything it starts finds `bzz`, also without a PATH entry of the pip Scripts folder
    env["BZZ_AGENT"] = a.name   # tells `bzz ready` which agent calls it (only the release phase may register a branch as ready)
    if bj:
        bj_start_mb = float(osplat.free_ram_mb())
        measured = buildjobs.stored_per_job(conn, a.project)
        calc = buildjobs.job_count(bj, osplat.logical_cores(), bj_start_mb, measured)
        bj_jobs, why = calc["Jobs"], calc["Why"]
        env[bj["EnvVar"]] = str(bj_jobs)
        try:
            samples.unlink()
        except OSError:
            pass
        sampler = threading.Thread(target=sampler_loop, daemon=True, args=(
            samples, [str(x) for x in pcfg.get("buildProcessNames") or []], stop, float(os.environ.get("BZZ_SAMPLE_SEC") or 10)))
        sampler.start()
        runtime.upsert_agent(conn, a.project, a.name, {"buildJobs": bj_jobs})
        run.event("build %s: starting with %d jobs (%s, free RAM %d MB)" % (a.name, bj_jobs, why, bj_start_mb), "build")

    code = 0
    stall_limit = float(cfg.get("agentStallSeconds", DEFAULT_STALL_SECONDS) or 0)

    def mark_stalled(secs):
        """Mark the running agent as stalled (row field, event, pane line); the process keeps running."""
        set_stalled(a, db.now(), "%s stalled: no output for %d s (still running, not ended)" % (a.name, secs))
        run.say("Stalled: no output for %d s. The agent is still running." % secs, "Yellow")

    def clear_stalled():
        """Remove the stalled mark when the agent prints again."""
        set_stalled(a, "", "%s prints again" % a.name)
    try:
        if os.environ.get("BZZ_FAKE_AGENT"):   # tests only: no model call, a few milestones and token numbers so the pane layout can be checked
            delay = float(os.environ.get("BZZ_FAKE_DELAY") or 3)
            for i in range(1, 9):
                run.say("Test milestone %d: this is a long line that should wrap in narrow panes so that the line wrapping becomes visible." % i, "White")
                run.tin += 4000
                run.tout += 120
                run.ctx = 4000 * i
                run.push_state(True)
                time.sleep(delay)
            run.ok, run.final, run.last = True, "Open requirements: 0", "test finished"
        else:
            cmd = shutil.which(prov["command"]) or prov["command"]
            argv = [cmd, "-p", prompt] + arg_list if protocol == "claude" else [cmd] + arg_list + [prompt]
            with open(str(err_file), "wb") as err, open(str(events_file), "ab") as raw:
                proc = subprocess.Popen(argv, cwd=a.cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err)
                last_output, watch_stop = [time.monotonic()], threading.Event()
                if stall_limit > 0:
                    threading.Thread(target=stall_watch, daemon=True, args=(last_output, stall_limit, mark_stalled, clear_stalled, watch_stop)).start()
                for bline in proc.stdout:
                    last_output[0] = time.monotonic()
                    line = bline.decode("utf-8", errors="replace").rstrip("\r\n")
                    raw.write((line + "\n").encode("utf-8"))
                    raw.flush()
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        run.handle_event(e)
                code = proc.wait()
                watch_stop.set()
    except Exception as exc:
        code = 99
        run.last = "runner error: " + str(exc)
        run.say(run.last, "Red")
    if bj_jobs > 0:
        stop.set()
        sampler.join(timeout=5)
        use = complete_build_measure(conn, a.project, a.name, bj_jobs, bj_start_mb, samples, started)
        runtime.upsert_agent(conn, a.project, a.name, {"buildUseMB": use})

    # Success is judged by the agent's own result event (claude: type result without error; codex: completed turn), not by the exit code: a failing plugin hook after the end must not fail the task.
    status = "done" if run.ok else "failed"
    if run.ok and code != 0:
        run.say("Note: exit code %s although the agent is done (see stderr.txt)" % code, "Yellow")
        run.event("warning %s exit code %s after a finished task (hook/stderr), kept as done" % (a.name, code), "warning")
    if not run.ok:
        err_txt = ""
        if err_file.is_file():
            err_txt = " ".join(err_file.read_text(encoding="utf-8", errors="replace").splitlines()[-3:])
        if not re.match(r"^(error|runner error)", run.last):
            run.last = "no result event, exit %s %s" % (code, err_txt)
        run.last = short(run.last)
    final_file.write_text(run.final, encoding="utf-8", newline="")
    run.set_agent_retry({"status": status, "endedAt": db.now(), "exitCode": code, "stalledAt": "", "tokensIn": run.tin, "tokensOut": run.tout, "lastLine": run.last, "ctxTokens": run.ctx})
    if run.final and short(run.final, 1500) != short(run.last_said, 1500):
        run.say("Final message: " + run.final, "Green" if status == "done" else "Red")
    run.say("End: %s (exit %s), total input %s / output %s. The section closes after the close delay." % (status, code, header.fmt_tok(run.tin), header.fmt_tok(run.tout)),
            "Green" if status == "done" else "Red")
    if header_proc is not None:
        try:
            header_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            header_proc.terminate()
    return 0


def complete_build_measure(conn, project, name, jobs, start_free_mb, sample_file, started):
    """End the measurement of one build: take the lowest free RAM from the samples, store the use per job and log one line. Returns the use per job in MB."""
    low = buildjobs.read_samples_min(sample_file, start_free_mb)
    use = buildjobs.use_mb(start_free_mb, low, jobs)
    dur = (datetime.now() - started).total_seconds()
    stored = buildjobs.save_per_job(conn, project, use, jobs, start_free_mb, low, dur)
    secs = int(dur)
    runtime.append_event(conn, project, "build %s: jobs=%d, duration=%02d:%02d:%02d, lowest free RAM=%d MB, use per job=%d MB (stored maximum %s MB)" % (
        name, jobs, secs // 3600, secs % 3600 // 60, secs % 60, low, use, int(stored) if stored is not None else use),
        kind="rounds", agent=name, mirror="rounds")
    return use


def pane_wrap(a, argv):
    """Pane wrapper: run the agent as a CHILD process, then keep the pane for the close delay and exit 0 (also when the child failed or was ended)."""
    try:
        cfg = config.load_config()
        delay = int(cfg.get("paneCloseDelaySeconds", 30))
        conn = db.connect(config.db_path())
    except Exception as exc:
        print("pane start error: %s" % exc)
        print("pane closes in 30 s")
        time.sleep(30)
        return 0
    try:
        subprocess.run([osplat.python_executable(), "-m", "haivequeen.agent_runner"] + argv, env=child_env())
    finally:
        print("pane closes in %d s" % delay)
        time.sleep(delay)
        try:
            runtime.upsert_agent(conn, a.project, a.name, {"paneClosed": True}, create=False)
        except Exception:
            pass
        try:
            from . import loop   # imported here: loop imports this module
            loop.hold_pane_lock_until_exit()   # the pane closes while no other pane command runs; the lock ends with this process
        except Exception:
            pass
    return 0


def parse_args(argv=None):
    """Parse the command line (see the module help)."""
    p = argparse.ArgumentParser(prog="haivequeen.agent_runner")
    p.add_argument("--project", required=True)
    p.add_argument("--name", required=True, help="agent id")
    p.add_argument("--provider", required=True, help="provider name from config.toml")
    p.add_argument("--plan", default="")
    p.add_argument("--package", default="")
    p.add_argument("--phase", default="work")
    p.add_argument("--cwd", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--runs-dir", default=None, help="folder of the run folders (default: <data>/runs)")
    p.add_argument("--model", default=None)
    p.add_argument("--effort", default=None)
    p.add_argument("--wrap", action="store_true", help="pane wrapper mode")
    p.add_argument("--protocol", default=None, help="passed by the loop; the protocol is taken from the provider entry")
    p.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="environment value to set before the run (test and folder overrides)")
    return p.parse_args(argv)


def main(argv=None):
    """Command line entry."""
    argv = list(sys.argv[1:] if argv is None else argv)
    a = parse_args(argv)
    for pair in a.env:
        key, _, value = pair.partition("=")
        if key:
            os.environ[key] = value
    if a.wrap:
        return pane_wrap(a, [x for x in argv if x != "--wrap"])
    return run_agent(a)


if __name__ == "__main__":
    sys.exit(main())
