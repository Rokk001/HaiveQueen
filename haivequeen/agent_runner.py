"""Runs ONE agent (Claude Code or Codex, headless) as a hidden background process (port of agent-run.ps1 and the agent part of build-sampler.ps1).

The agent is tracked in the agents table; the full raw output stays in <runs dir>/<agent>/ (events.jsonl, stderr.txt, milestones.txt, final.txt, rules.md).
The LIVE TEXT of the agent (what the graphical dashboard shows in its live view) is <runs dir>/<agent>/milestones.txt: one line per milestone,
written by AgentRun.say as "<colour><TAB><HH:MM> <text>" (colour is one of MS_COLOURS). The same milestones, plain, also go to the stdout of the
runner, which the loop redirects to runner.log in that folder.

Usage:
  python -m haivequeen.agent_runner --project P --name N --provider claude --cwd DIR --prompt-file FILE
         [--plan PATH] [--package ID] [--phase work] [--runs-dir DIR] [--model M] [--effort E]
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

from . import buildjobs, config, db, interrupt, localtime, overview, profile, roles, runtime, scheduler, startctx
from . import platform as osplat
from .i18n import tr

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
    """Return the short model text: "Son 5.5 med." / "Sol 6.1 med."."""
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
        fmt = profile.check_protocol(name, proto, p.get("outputFormat"))
        out.append({"name": name, "protocol": proto, "format": fmt, "command": str(p.get("command") or proto),
                    "args": [str(x) for x in (p.get("args") or [])], "classes": p.get("classes") or {},
                    "endpoint": p.get("endpoint") or None, **profile.custom_fields(p)})
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

# Find check scripts in command position, ignoring filenames used as data.
def executed_check_scripts(cmd, wrapped=False):
    """Return invoked check filenames, unwrapping one PowerShell command string."""
    tokens = re.findall(r'''"[^"]*"|'[^']*'|\#[^\r\n]*|&&|\|\||[;&|\n]|[^\s;&|]+''', cmd)
    segments, current = [], []
    for token in tokens + [';']:
        if token.startswith('#'):
            continue
        if token in (';', '&', '&&', '|', '||', '\n'):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token[1:-1] if token[:1] in ('"', "'") and token[-1:] == token[:1] else token)
    checks = []
    for words in segments:
        if words[0] == '.':
            words = words[1:]
        if not words:
            continue
        executable = re.split(r'[/\\]', words[0])[-1]
        index = 0
        if re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|py|pwsh|powershell)(?:\.exe)?', executable, re.I):
            index = 1
            while index < len(words) and words[index].startswith('-'):
                flag = words[index].lower()
                if words[index] in ('-h', '--help', '--help-env', '--help-xoptions', '--help-all', '-V', '--version'):
                    index = len(words)
                    break
                if flag in ('-c', '-m', '-command', '-encodedcommand'):
                    if not wrapped and re.fullmatch(r'(?:pwsh|powershell)(?:\.exe)?', executable, re.I) and flag in ('-c', '-command') and index + 1 < len(words):
                        tail = words[index + 1:]
                        command = tail[0] if len(tail) == 1 else ' '.join('"%s"' % word if re.search(r'\s', word) else word for word in tail)
                        checks.extend(executed_check_scripts(command, wrapped=True))
                    index = len(words)
                    break
                takes_value = words[index] in ('-X', '-W') or flag in ('--check-hash-based-pycs', '-executionpolicy', '-version', '-inputformat', '-outputformat')
                index += 2 if takes_value else 1
                if flag in ('--', '-file'):
                    break
        if index >= len(words):
            continue
        name = re.split(r'[/\\]', words[index])[-1]
        if re.fullmatch(r'(?:check[\w-]*\.(?:py|ps1)|compile-check-changed\.ps1)', name, re.I):
            checks.append(name)
    return checks


SHELLS = re.compile(r'(?:pwsh|powershell|bash|sh|zsh|dash|cmd)(?:\.exe)?$', re.I)
COMMAND_PREFIXES = re.compile(r'(?:exec|command|call|time)$', re.I)
SHELL_VALUE_FLAGS = ('-executionpolicy', '-ep', '-version', '-inputformat', '-outputformat', '-workingdirectory', '-wd', '-configurationname', '-settingsfile')


# Split a command line into its commands, each a list of words without their quotes.
def _shell_commands(cmd):
    """Commands of a command line in any common shell; the separators are ; & && | || and a line end, a comment ends at the line end."""
    commands, current = [], []
    for token in re.findall(r'''"[^"]*"|'[^']*'|\#[^\r\n]*|&&|\|\||[;&|\n]|[^\s;&|]+''', str(cmd or '')) + [';']:
        if token.startswith('#'):
            continue
        if token in (';', '&', '&&', '|', '||', '\n'):
            if current:
                commands.append(current)
                current = []
        else:
            current.append(token[1:-1] if len(token) > 1 and token[0] in '"\'' and token[-1] == token[0] else token)
    return commands


# Open shell wrappers and leave out what only prepares a command.
def _plain_commands(cmd, depth=0):
    """The commands that really run, each as a list of words: `pwsh -Command "x"`, `bash -lc 'x'` and `cmd /c x` count as x; `env`, `FOO=1` and the like are left out."""
    plain = []
    for words in _shell_commands(cmd):
        while words and (re.fullmatch(r'\w+=\S*', words[0]) or COMMAND_PREFIXES.fullmatch(words[0]) or re.split(r'[/\\]', words[0])[-1].lower() in ('env', 'env.exe')):
            words = words[1:]
        if not words:
            continue
        shell = SHELLS.fullmatch(re.split(r'[/\\]', words[0])[-1])
        flag = next((i for i, word in enumerate(words) if i and re.fullmatch(r'(?:-(?:command|[a-z]*c)|/[ck])', word, re.I)), None) if shell else None
        if flag is not None and flag + 1 < len(words) and depth < 3:
            tail = words[flag + 1:]
            plain.extend(_plain_commands(tail[0] if len(tail) == 1 else ' '.join('"%s"' % w if re.search(r'\s', w) else w for w in tail), depth + 1))
        else:
            plain.append(words)
            if shell:   # `pwsh -File x.ps1` and `bash x.sh` also count as running x
                index = 1
                while index < len(words) and words[index][:1] in ('-', '/') and not re.match(r'/[a-zA-Z]/|/\w+/', words[index]):
                    index += 2 if words[index].lower() in SHELL_VALUE_FLAGS else 1
                if index < len(words):
                    plain.append(words[index:])
    return plain


# Compare words of a command whatever way the shell wrote its program.
def _same_word(a, b, program=False):
    """True when two words are equal; a path counts in any slash form (also /c/x for C:/x); a program also without .exe and in any case."""
    def norm(word):
        """The comparable form of a word."""
        word = re.sub(r'^/([a-zA-Z])/', r'\1:/', word.replace('\\', '/'))
        if program:
            word = re.sub(r'\.exe$', '', word, flags=re.I).casefold()
        return word
    return norm(a) == norm(b)


# The compile check command that applies to a run.
def configured_compile_check(machine, project):
    """The setting compileCheck as the Queen and bzz ready read it: the project over the machine settings."""
    return scheduler.build_settings(machine, project).get("compileCheck") or ""


# Whether a command line runs the configured command.
def runs_configured_command(cmd, configured):
    """True when `cmd` runs the configured command in command position, in any shell, with or without further arguments; all its parts in their order when it has several."""
    wanted = _plain_commands(configured)
    if not wanted:
        return False
    position = 0
    for words in _plain_commands(cmd):
        want = wanted[position]
        if len(words) >= len(want) and all(_same_word(w, x, i == 0) for i, (w, x) in enumerate(zip(words, want))):
            position += 1
            if position == len(wanted):
                return True
    return False


# Classify requested operations while treating read and search arguments as data.
def classify_cmd(cmd, queue_re=None, compile_check=None):
    """Recognise a milestone command (commit, push, compile test, check, ready line, file write); returns {kind, label} or None.

    A call of `bzz ready` is the ready line; queue_re (optional) additionally marks writes into a legacy queue file.
    """
    cmd = str(cmd or "")
    if compile_check and runs_configured_command(cmd, compile_check):   # first: `check && git commit` is evidence of the check; text in a search, echo or message is not
        return {"kind": "check", "label": "Compile test"}
    if re.search(r"git\s+commit", cmd):
        m = re.search(r'-m\s+"([^"\r\n$]{3,})"', cmd) or re.search(r"-m\s+'([^'\r\n]{3,})'", cmd)
        return {"kind": "commit", "label": ("Commit: " + short(m.group(1), 100)) if m else "Commit"}
    if re.search(r"git\s+push", cmd):
        return {"kind": "push", "label": "Push"}
    checks = executed_check_scripts(cmd)
    if checks:
        return {"kind": "check", "label": "Check " + checks[0]}
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
    return profile.sessions_dir("codex")


def get_codex_tokens(thread_id, sessions=None, first=False):
    """Return live token numbers {ctx, tin, tout, cached} of a running Codex agent from its session log, or None when not available yet.
    With `first` the context is that of the first model turn of the run (read from the start of the log) instead of the latest."""
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
            handle.seek(0 if first else max(0, size - 300 * 1024))
            data = handle.read(300 * 1024 if first else -1).decode("utf-8", errors="replace")
        lines = [ln for ln in data.split("\n") if '"type":"token_count"' in ln and "last_token_usage" in ln]
        if not lines:
            return None
        info = json.loads(lines[0 if first else -1])["payload"]["info"]
        cached = info["total_token_usage"].get("cached_input_tokens")
        out = {"ctx": int(info["last_token_usage"]["input_tokens"]), "tin": int(info["total_token_usage"]["input_tokens"]),
               "tout": int(info["total_token_usage"]["output_tokens"])}
        if cached is not None:
            out["cached"] = int(cached)
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- build jobs

def sample_once(path, process_names, root_pid=None):
    """Append one sample line "ISO time;free MB;build process count;build process RAM MB" to the sample file; only the build processes
    below `root_pid` (the process tree of the building agent) are counted, never other processes of the same name."""
    with open(str(path), "a", encoding="utf-8") as handle:
        handle.write(buildjobs.sample_line(process_names, root_pid) + "\n")


def sampler_loop(path, process_names, stop, seconds, root_pid=None):
    """Sample free RAM and the build processes of `root_pid` every `seconds` seconds until `stop` is set (BZZ_SAMPLE_SEC overrides, tests only)."""
    while True:
        try:
            sample_once(path, process_names, root_pid)
        except Exception:
            pass
        if stop.wait(seconds):
            break



# Measure Queen builds only while integration or a compiler process is active.
def queen_build_monitor(project, plan, name, jobs, sample_file, process_names, stop, interval, parallel=1, root_pid=None, count=buildjobs.DEFAULT_MEASUREMENTS):
    """Measure Queen builds only while integration or a compiler process is active. The job count of `bzz integration acquire` (stored in the
    integration slot) replaces the one of the Queen start, and the measurement divides by it."""
    conn = db.connect(config.db_path())
    started = None
    start_free = None
    try:
        while True:
            slot = conn.execute("SELECT acquired,build_jobs FROM hive_integration WHERE project=? AND plan_path=?", (project, plan)).fetchone()
            active = bool(slot and slot[0]) or bool(process_names and osplat.count_processes(process_names))
            if active:
                if slot and slot[1]:
                    jobs = int(slot[1])
                if started is None:
                    started = datetime.now()
                    start_free = float(osplat.free_ram_mb())
                    Path(sample_file).write_text("", encoding="utf-8")
                    runtime.upsert_agent(conn, project, name, {"buildJobs": jobs})
                sample_once(sample_file, process_names, root_pid)
            elif started is not None:
                use = complete_build_measure(conn, project, name, jobs, start_free, sample_file, started, parallel, count)
                runtime.upsert_agent(conn, project, name, {"buildUseMB": use, "buildJobs": None})
                started = None
            if stop.wait(min(float(interval), 1.0)):
                break
        if started is not None:
            use = complete_build_measure(conn, project, name, jobs, start_free, sample_file, started, parallel, count)
            runtime.upsert_agent(conn, project, name, {"buildUseMB": use, "buildJobs": None})
    finally:
        conn.close()

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
        if sel is None and phase == "queen":
            sel = rules.get("work")
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


def task_text(conn, project, plan, package, phase, plan_short):
    """Return the task text: work = package row of the plan (id and title); verify / release / integrate = phase plus package."""
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


# Track and report one agent run.
class AgentRun:
    """State and behaviour of one agent run; `handle_event` parses the event stream of either CLI."""

    # Initialize one tracked agent run.
    def __init__(self, conn, project, name, provider, protocol, cwd, run_dir, plan="", package="", phase="work",
                 out=None, queue_re=None, sessions=None):
        """Remember the run parameters; `out` is where the plain milestone text goes (default stdout)."""
        self.conn, self.project, self.name = conn, project, name
        self.provider, self.type = provider, protocol
        self.format = profile.FORMATS.get(protocol, protocol)   # the output format the events are read in; run_agent sets the one of the provider entry
        self.cwd, self.run_dir = str(cwd), Path(run_dir)
        self.plan, self.package, self.phase = plan, package, phase
        self.out = out or sys.stdout
        self.queue_re, self.sessions = queue_re, sessions
        self.compile_check = None
        self.thread = ""
        self.tin, self.tout = (None, None) if protocol == "codex" else (0, 0)   # Codex reports its token totals only in the session log
        self.ctx = 0
        self.sub_ctx = {}   # latest context size of every subagent Bee of this Queen: agent id -> {desc, ctx}
        self.task_desc = {}   # description of each Task call of the Queen by tool use id: it names the subagent Bee
        self.sub_started = set()   # subagents whose start context is stored
        self.start_ctx = 0   # the context of the first model turn of this run (the start context)
        self.start_parts = {}   # approximate parts of HaiveQueen in the start context: role file, project rules files, task
        self.cache_read, self.cache_write = (None, None) if protocol == "codex" else (0, 0)   # cached and newly cached input tokens; None = the CLI reports none
        self.context_limit = CONTEXT_LIMIT
        self.seen = set()
        self.last = ""
        self.final = ""
        self.ok = False
        self.api_errors = []   # error texts of the agent's error events (Claude result with is_error, Codex turn.failed / error), for the interruption check
        self.model = ""
        self.pending = {}
        self.last_said = ""
        self.last_write = 0.0
        self.started = datetime.now()
        self.ms_file = self.run_dir / "milestones.txt"

    # --- live output

    def say(self, text, colour="White"):
        """Write one milestone line with the time: plain to `out` and as "<colour><TAB><time> <text>" into milestones.txt (the live text file the dashboard reads)."""
        text = str(text)
        if len(text) > 1500:
            text = text[:1500] + "..."
        ts = localtime.time(datetime.now()) + " "
        try:
            with open(str(self.ms_file), "ab") as handle:
                handle.write((colour + "\t" + ts + " ".join(text.split()) + "\n").encode("utf-8"))
        except OSError:
            pass
        try:
            self.out.write(ts + " ".join(text.split()) + "\n")
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
                if cx.get("cached") is not None:
                    self.cache_read = cx["cached"]
                if not self.start_ctx:
                    first = get_codex_tokens(self.thread, self.sessions, first=True)
                    self.note_start(first["ctx"] if first else cx["ctx"])
        try:
            runtime.upsert_agent(self.conn, self.project, self.name, {
                "tokensIn": self.tin, "tokensOut": self.tout, "ctxTokens": self.ctx, "lastLine": self.last,
                **({"startCtx": self.start_ctx} if self.start_ctx else {}),
                **({"cacheRead": self.cache_read, "cacheWrite": self.cache_write} if self.cache_read is not None else {}),
                **({"subCtx": self.sub_ctx} if self.sub_ctx else {})})
        except Exception:
            pass   # a failed write must not end the run

    # Remember the first model turn of this run.
    def note_start(self, tokens, kind=None, agent=None):
        """Store the start context (the context of the first model turn) of this run, or of a subagent Bee of it when `kind` is "subbee"; Queens and
        Bees are stored, other phases and runs without a database (the benchmark) are not."""
        kind = kind or self.phase
        if not tokens:
            return
        if kind != "subbee":
            if self.start_ctx:
                return
            self.start_ctx = int(tokens)
        if self.conn is None or kind not in startctx.KINDS:
            return
        try:
            startctx.record(self.conn, self.project, self.plan, agent or self.name, kind, self.provider, tokens, self.start_parts if kind != "subbee" else None)
        except Exception:
            pass   # a failed write must not end the run

    # Write the HANDOFF flag file when the current context reached the limit (the agent is never killed).
    def check_handoff(self):
        """Write the HANDOFF flag file when the current context reached the limit (the agent is never killed)."""
        flag = self.run_dir / "HANDOFF"
        if self.ctx and self.ctx >= self.context_limit and not flag.exists():
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
        """Handle one parsed JSON event of any CLI, read in the output format of the provider (claude, codex or opencode)."""
        if self.format == "claude":
            self._claude_event(e)
        elif self.format == "opencode":
            self._opencode_event(e)
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
                self.cache_read += int(u.get("cache_read_input_tokens") or 0)
                self.cache_write += int(u.get("cache_creation_input_tokens") or 0)
                if e.get("parent_tool_use_id"):
                    # A subagent's message (told by its parent tool use id; the Task call names it): its context is the Bee's, never the Queen's.
                    key = str(e.get("agent_id") or e["parent_tool_use_id"])
                    entry = self.sub_ctx.setdefault(key, {"desc": "", "ctx": 0, "tin": 0, "tout": 0})
                    entry.update(desc=str(e.get("task_description") or self.task_desc.get(str(e["parent_tool_use_id"])) or entry["desc"]), ctx=c1)
                    entry["tin"] += c1
                    entry["tout"] += int(u.get("output_tokens") or 0)
                    if key not in self.sub_started:
                        self.sub_started.add(key)
                        self.note_start(c1, "subbee", entry["desc"])
                else:
                    self.ctx = c1
                    self.note_start(c1)
            for c in msg.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text"):
                    self.last = short(c["text"])
                    if self.phase == "queen" and str(c["text"]).strip():
                        self.final = str(c["text"])
                    self.say(str(c["text"]), "White")
                elif c.get("type") == "tool_use":
                    self.last = "tool: " + str(c.get("name"))
                    inp = c.get("input") or {}
                    if c.get("name") in ("Task", "Agent") and c.get("id") and not e.get("parent_tool_use_id"):
                        self.task_desc[str(c["id"])] = short(str(inp.get("description") or inp.get("prompt") or ""), 200)
                    if c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
                        if fp and self.queue_re and re.search(self.queue_re, fp):
                            self.say("ready line registered", "Green")
                        elif fp:
                            self.say("changed: " + self.rel_path(fp), "Cyan")
                    elif c.get("name") in ("Bash", "PowerShell"):
                        info = classify_cmd(str(inp.get("command") or ""), self.queue_re, self.compile_check)
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
            result = str(e.get("result") or "")
            if self.phase != "queen" or result.strip():
                self.final = result
            self.ok = not e.get("is_error")
            if e.get("is_error"):
                self.api_errors.append(self.final)
            self.last = short(self.final)

    def _opencode_event(self, e):
        """Handle one event of the OpenCode `run --format json` format (also written by Kilo Code): `text`, `tool_use`, `step_finish` with the token counts, `error`.
        There is no result event: the run counts as done when a step finished and no error event came."""
        et = e.get("type")
        part = e.get("part") if isinstance(e.get("part"), dict) else {}
        if et == "text":
            text = str(part.get("text") or "")
            if text.strip():   # an empty closing message must not wipe the last real answer
                self.final = text
                self.last = short(text)
                self.say(text, "White")
        elif et == "tool_use":
            self.last = "tool: " + str(part.get("tool"))
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            command = (state.get("input") or {}).get("command") if isinstance(state.get("input"), dict) else None
            if command and state.get("status") in ("completed", "error"):
                info = classify_cmd(str(command), self.queue_re, self.compile_check)
                if info and info["kind"] == "edit":
                    self.say(info["label"], "Cyan")
                elif info:
                    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
                    self.say_result(info, str(state.get("output") or state.get("error") or ""), metadata.get("exit"), state.get("status") == "error")
        elif et == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            c1 = int(tokens.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
            self.tin += c1
            self.tout += int(tokens.get("output") or 0)
            self.cache_read += int(cache.get("read") or 0)
            self.cache_write += int(cache.get("write") or 0)
            self.ctx = c1   # the size of the latest context
            self.note_start(c1)
            self.ok = not self.api_errors
        elif et == "error":
            self.ok = False
            self.last = "error: " + short(json.dumps(e.get("error"), separators=(",", ":")))
            self.api_errors.append(json.dumps(e.get("error"), separators=(",", ":")))

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
                info = classify_cmd(str(item.get("command") or ""), self.queue_re, self.compile_check)
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
            if e["usage"].get("cached_input_tokens") is not None:
                self.cache_read = int(e["usage"]["cached_input_tokens"])
            cx = get_codex_tokens(self.thread, self.sessions)
            if cx:
                self.ctx = cx["ctx"]
            if not self.start_ctx:
                first = get_codex_tokens(self.thread, self.sessions, first=True)
                self.note_start(first["ctx"] if first else self.tin)
            self.ok = True
        elif et in ("turn.failed", "error"):
            self.last = "error: " + short(json.dumps(e, separators=(",", ":")))
            self.api_errors.append(json.dumps(e, separators=(",", ":")))
            self.say(self.last, "Red")

    def save_claude_usage(self, e):
        """Remember the newest Claude usage windows (from a rate_limit_event) in the kv key usage-claude of the global scope."""
        info = e.get("rate_limit_info") or {}
        w = info.get("unifiedWindows")
        if not w:
            return
        try:
            runtime.kv_set(self.conn, "*", "usage-claude:" + str(self.provider), json.dumps({"saved": datetime.now().isoformat(), "fiveHour": w.get("five_hour"), "sevenDay": w.get("seven_day")}))
        except Exception:
            pass


# Provide the agent with the supported reporting and integration commands.
def report_prompt(prompt_file, project, agent_id, phase=None):
    """Return the CLI prompt with phase-specific reporting commands and no double quotes."""
    orch = "'%s' -I '%s'" % (osplat.python_executable(), TOOL_ROOT / "haivequeen" / "cli.py")
    text = ('Read the file "%s" completely and carry out exactly the task described there. Do nothing else.' % prompt_file)
    if phase == "bee":
        text += " Final report: %s done --agent %s --result <file>; step report: %s note --project %s --text <text>." % (orch, agent_id, orch, project)
        return text.replace('"', "")
    text += (" Report only with these commands, never by editing a queue file or state table by hand (the handoff text in the state file of the plan stays your own file): "
             "final report: %s done --agent %s --result <file with your final report>; "
             "branch ready for integration: %s ready --project %s --branch <branch> --tip <commit> --plan <plan path> --note <text>; "
             "question for the owner: %s ask --project %s --plan <plan path> --item <item> --question <question> --recommendation <recommendation>; "
             "integration place: %s integration acquire|release --project %s --plan <plan path>; "
             "note: %s note --project %s --text <text>.") % (orch, agent_id, orch, project, orch, project, orch, project, orch, project)
    return text.replace('"', "")


def build_args(prov, vars_):
    """Return the argument list of the provider entry (plus the endpoint arguments) with {name} replaced by the variables."""
    def sub(arg):
        return re.sub(r"\{(\w+)\}", lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ else m.group(0), arg)
    return [sub(a) for a in prov["args"] + endpoint_args(prov)]


# Estimate the parts of HaiveQueen in the start context of a Queen or Bee.
def approx_start_parts(a, pcfg, cfg, workspace, protocol, message=""):
    """{"role": n, "rules": {file: n}, "task": n} as approximate token counts (4 characters per token) of the role file, every project rules file and the task;
    empty when a file cannot be read (the start context is stored without its split then)."""
    try:
        from . import roles
        settings = scheduler.build_settings(cfg, pcfg)
        role = roles.role_text("queen-start" if a.phase == "queen" else "bee-start", {"language": roles.language(settings)})
        rules = profile.project_rules(pcfg, pcfg.get("codeRepo") or workspace or a.cwd, protocol, a.cwd)
        return startctx.approx_parts(role, rules["inline"], message)   # the message the CLI starts with; the prompt file it reads afterwards is not in the first turn
    except Exception:
        return {}


# Find the one branch a Queen may register as ready or change in the queue.
def queen_branch(conn, cfg, project, plan, cwd, integration_branch):
    """The branch the plan (or its state file) names, else the branch checked out in the Queen's worktree;
    None when neither is known or it is the integration branch."""
    branch = None
    try:
        from . import bees
        env = next((e for e in scheduler.load_projects(conn, cfg) if e.name == project), None)
        named = bees.plan_worktree(env, plan) if env is not None else None
        branch = named[0] if named else None
    except Exception:
        branch = None
    if not branch and cwd:
        try:
            done = subprocess.run(["git", "-C", str(cwd), "symbolic-ref", "--quiet", "--short", "HEAD"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            branch = done.stdout.strip() if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            branch = None
    if not branch or branch == str(integration_branch):
        return None
    return branch


# Look the Queen's plan branch up again for the own-Hive hook (the branch may be created after the start).
def queen_branch_now(project, plan, name, integration_branch):
    """The branch queen_branch finds now, with the worktree the Queen recorded as her cwd; None when it is
    unknown, the integration branch, or a queue entry or an active agent of another plan in the same worktree
    gives it to another plan."""
    same_plan = lambda value: not value or Path(str(value)).as_posix() == Path(str(plan)).as_posix()
    same_dir = lambda left, right: bool(left and right) and os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))
    cfg = config.load_config()
    conn = db.connect(config.db_path())
    try:
        row = runtime.get_agent(conn, project, name)
        if not row or row.get("archived") or not same_plan(row.get("plan_path") or row.get("plan")):
            return None
        branch = queen_branch(conn, cfg, project, plan, row.get("cwd"), integration_branch or "main")
        if not branch:
            return None
        if any(not same_plan(entry[0]) for entry in conn.execute("SELECT plan_path FROM queue WHERE project=? AND branch=?", (project, branch))):
            return None
        for other in conn.execute("SELECT plan_path, cwd FROM agents WHERE project=? AND archived=0 AND name<>?", (project, name)):
            if same_dir(other[1], row.get("cwd")) and not same_plan(other[0]):
                return None
        return branch
    finally:
        conn.close()


# Run one agent, rejecting empty Claude tool arguments before starting its CLI.
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
    run.compile_check = configured_compile_check(cfg, pcfg)
    run.format = prov["format"]
    run.context_limit = scheduler.context_limit({"contextLimitTokens": CONTEXT_LIMIT, **scheduler.build_settings(cfg, pcfg)}, [{**prov, "name": a.provider}], a.provider, cls)
    run.model = model_label(model)
    plan_short = re.sub(r"-?PLAN$", "", Path(a.plan).stem) if a.plan else ""
    task = task_text(conn, a.project, a.plan, a.package, a.phase, plan_short) or task_line(a.prompt_file)
    events_file, err_file, final_file = run_dir / "events.jsonl", run_dir / "stderr.txt", run_dir / "final.txt"

    from . import agent_process
    if prev and prev.get("pid") and int(prev["pid"]) != os.getpid() and agent_process.is_live(prev):
        out.write("agent %s already runs (pid %s), this second runner ends\n" % (a.name, prev["pid"]))   # a relaunched runner must never start the task twice
        return 0
    started = run.started
    if not run.set_agent_retry({"type": protocol, "provider": a.provider, "model": model, "effort": effort, "plan": a.plan,
                                "package": a.package, "phase": a.phase, "cwd": a.cwd, "status": "running",
                                "startedAt": started.isoformat(), "endedAt": None, "pid": os.getpid(), "processIdentity": osplat.process_identity(os.getpid()), "tokensIn": run.tin,
                                "tokensOut": run.tout, "lastLine": "starting",
                                "modelShort": (prev or {}).get("modelShort") or model_short(protocol, model, effort),
                                "task": (prev or {}).get("task") or task}):
        out.write("agent %s not started: the agent row could not be written, row set to failed\n" % a.name)   # the model is never called
        return 0
    if a.phase == "queen":
        from . import hives
        plan_path = Path(a.plan) if a.plan else None
        state_paths = []
        if plan_path and not plan_path.is_absolute():
            names = (plan_path.stem + '-STATE.md', re.sub(r'-PLAN$', '', plan_path.stem) + '-STATE.md')
            state_paths = [Path(base) / plan_path.parent / name for base in (a.cwd, workspace) if base for name in names]
        hives.save_worktree_state(a.cwd, run_dir, state_paths)
    run.ms_file.write_text("", encoding="utf-8")   # a new live text file for this run

    try:
        agent_row = runtime.get_agent(conn, a.project, a.name)
        prompt = report_prompt(a.prompt_file, a.project, agent_row["id"] if agent_row else "?", a.phase)
        rules_file = run_dir / "rules.md"
        if protocol == "claude":
            if a.phase in ("bee", "queen"):   # complete project rules (checked-in instruction files, upstream ones when set), no selection
                rules_file.write_text(profile.rules_text(profile.project_rules(pcfg, pcfg.get("codeRepo") or workspace or a.cwd, protocol, a.cwd)), encoding="utf-8")
            else:
                write_phase_rules(pcfg, workspace, a.phase, rules_file)
        if a.phase in ("bee", "queen"):
            run.start_parts = approx_start_parts(a, pcfg, cfg, workspace, protocol, prompt)
        vars_ = {"model": model, "effort": effort, "repo": pcfg.get("codeRepo") or workspace or a.cwd, "workspace": workspace or a.cwd, "tools": str(TOOL_ROOT), "cwd": a.cwd,
                 "claudeTools": pcfg.get("claudeTools", cfg.get("claudeTools", "")), "rules": str(rules_file)}
        if protocol == "claude" and not str(vars_["claudeTools"] or "").strip():
            raise ValueError("claudeTools missing")
        if not profile.prepare(prov):
            if not runtime.list_questions(conn, a.project, status="open", plan=a.plan, item=a.package or "profile"):
                language = roles.language(scheduler.build_settings(cfg, pcfg))
                db.add_question(conn, a.project, a.plan, a.package or "profile",
                                tr("Provider %s (protocol %s) cannot be kept to the HaiveQueen profile, so it would read private files. This package is stopped. Which provider may run it?", a.provider, protocol, language=language),
                                tr("Give the package to a provider that can be kept to the profile.", language=language))
            raise ValueError("provider %s cannot be kept to the HaiveQueen profile" % a.provider)
        arg_list = build_args(prov, vars_)
        if protocol == "claude" and a.phase == "queen" and a.plan:
            settings = scheduler.build_settings(cfg, pcfg)
            grants = profile.claude_integration_permissions(a.project, a.plan, settings.get("remoteName") or "origin",
                                                             settings.get("integrationBranch") or "main", osplat.python_executable(),
                                                             TOOL_ROOT / "haivequeen" / "cli.py")
            permissions_file = run_dir / "claude-integration-settings.json"
            provider_settings = {}
            settings_args = [i for i, arg in enumerate(arg_list) if arg == "--settings" or arg.startswith("--settings=")]
            if len(settings_args) > 1:
                raise ValueError("multiple Claude --settings arguments cannot be merged")
            if settings_args:
                i = settings_args[0]
                if arg_list[i] == "--settings":
                    if i + 1 >= len(arg_list):
                        raise ValueError("Claude --settings requires a value")
                    raw = arg_list[i + 1]
                    del arg_list[i:i + 2]
                else:
                    raw = arg_list.pop(i).split("=", 1)[1]
                if raw.lstrip().startswith("{"):
                    provider_settings = json.loads(raw)
                else:
                    path = Path(raw)
                    if not path.is_absolute():
                        path = Path(a.cwd) / path
                    provider_settings = json.loads(path.read_text(encoding="utf-8"))
            policy_file = run_dir / "claude-hive-policy.json"
            integration_branch = settings.get("integrationBranch") or "main"
            policy = profile.claude_hook_policy(a.project, a.plan, agent_row["id"] if agent_row else None,
                                                settings.get("remoteName") or "origin", integration_branch,
                                                osplat.python_executable(), TOOL_ROOT / "haivequeen" / "cli.py",
                                                queen_branch(conn, cfg, a.project, a.plan, a.cwd, integration_branch), a.name)
            policy_file.write_text(json.dumps(policy, indent=1) + "\n", encoding="utf-8")
            hook = profile.claude_hook_command(osplat.python_executable(), TOOL_ROOT / "haivequeen" / "claude_hook.py", policy_file)
            merged = profile.claude_merge_run_settings(provider_settings, grants, hook)
            permissions_file.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
            arg_list.extend(["--settings", str(permissions_file)])
        if a.phase == "bee" and protocol == "claude":
            if "--setting-sources" in arg_list:
                i = arg_list.index("--setting-sources")
                arg_list[i + 1] = ""
            else:
                arg_list.extend(["--setting-sources", ""])
        if protocol == "claude" and "--tools" in arg_list:
            tools_index = arg_list.index("--tools") + 1
            if tools_index == len(arg_list) or not arg_list[tools_index].strip():
                raise ValueError("claudeTools missing")
    except Exception as exc:   # a broken rule file or argument template must show up as a failed agent at once, not as a vanished runner one round later
        run.last = short("runner error: " + str(exc))
        run.say(run.last, "Red")
        run.set_agent_retry({"status": "failed", "endedAt": db.now(), "exitCode": 99, "lastLine": run.last})
        return 0

    # Build jobs: an agent that builds (integrate, release) gets the job count of this machine as environment variable and its RAM use is measured while it runs.
    bj_raw = cfg.get("buildJobs")
    bj = buildjobs.settings_from(None if isinstance(bj_raw, bool) else bj_raw) if a.phase in ("integrate", "release", "queen", "bee") else None   # every agent that builds, Bees included
    bj_jobs, bj_start_mb, samples, stop, sampler = 0, 0.0, run_dir / "build-samples.txt", threading.Event(), None
    env = with_bzz_on_path(profile.environment(prov))   # the agent and everything it starts finds `bzz`, also without a PATH entry of the pip Scripts folder
    env["BZZ_AGENT"] = a.name   # tells `bzz ready` which agent calls it (only the release phase may register a branch as ready)
    if bj:
        bj_start_mb = float(osplat.free_ram_mb())
        measured = buildjobs.stored_per_job(conn, a.project, count=bj["Measurements"])
        calc = buildjobs.job_count(bj, osplat.logical_cores(), bj_start_mb, measured, concurrent=len(buildjobs.running_builds(conn, a.name)))
        bj_jobs, why = calc["Jobs"], calc["Why"]
        env.update(buildjobs.build_environment(bj, bj_jobs))   # the job count (configured name), the parallel value and the minimum free RAM
        try:
            samples.unlink()
        except OSError:
            pass
        process_names = [str(x) for x in pcfg.get("buildProcessNames") or []]
        if a.phase == "queen":
            sampler = threading.Thread(target=queen_build_monitor, daemon=True, args=(
                a.project, a.plan, a.name, bj_jobs, samples, process_names, stop, float(os.environ.get("BZZ_SAMPLE_SEC") or 10),
                bj["Parallel"], os.getpid(), bj["Measurements"]))
        elif a.phase == "bee":
            sampler = None   # a Bee only gets the variables: no sampler and no job count in its row (it is no build agent of its own)
        else:
            sampler = threading.Thread(target=sampler_loop, daemon=True, args=(
                samples, process_names, stop, float(os.environ.get("BZZ_SAMPLE_SEC") or 10), os.getpid()))
            runtime.upsert_agent(conn, a.project, a.name, {"buildJobs": bj_jobs})
            run.event("build %s: starting with %d jobs (%s, free RAM %d MB)" % (a.name, bj_jobs, why, bj_start_mb), "build")
        if sampler:
            sampler.start()

    code = 0
    clock_jumps = []   # filled when the wall clock jumps by more than interrupt.JUMP_SECONDS during the run (laptop sleep)
    jump_limit = float(scheduler.build_settings(cfg, pcfg).get("clockJumpSeconds", interrupt.JUMP_SECONDS))
    threading.Thread(target=clock_watch, daemon=True, args=(clock_jumps,), kwargs={"limit": jump_limit}).start()
    if os.environ.get("BZZ_FAKE_CLOCK_JUMP"):   # tests only: simulate a clock jump of this many seconds during the run
        clock_jumps.append(float(os.environ["BZZ_FAKE_CLOCK_JUMP"]))
    stall_limit = float(cfg.get("agentStallSeconds", DEFAULT_STALL_SECONDS) or 0)

    def mark_stalled(secs):
        """Mark the running agent as stalled (row field, event, live text line); the process keeps running."""
        set_stalled(a, db.now(), "%s stalled: no output for %d s (still running, not ended)" % (a.name, secs))
        run.say("Stalled: no output for %d s. The agent is still running." % secs, "Yellow")

    def clear_stalled():
        """Remove the stalled mark when the agent prints again."""
        set_stalled(a, "", "%s prints again" % a.name)
    try:
        if os.environ.get("BZZ_FAKE_AGENT"):   # tests only: no model call, a few milestones and token numbers so the live text can be checked
            delay = float(os.environ.get("BZZ_FAKE_DELAY") or 3)
            for i in range(1, 9):
                run.say("Test milestone %d: this is a long line that should wrap in a narrow window so that the line wrapping becomes visible." % i, "White")
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
    if bj_jobs > 0 and sampler:
        stop.set()
        sampler.join(timeout=5)
        if a.phase != "queen":
            use = complete_build_measure(conn, a.project, a.name, bj_jobs, bj_start_mb, samples, started, bj["Parallel"], bj["Measurements"])
            if use is not None:
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
    if a.phase == "queen":
        from . import hives
        run.final = hives.queen_final_text(run_dir, started.timestamp(), run.final)
    final_file.write_text(run.final, encoding="utf-8", newline="")
    # An end caused by sleep, network, API error or a vanished process is marked interrupted: it is no failure and is tried again after a wait.
    err_text = err_file.read_text(encoding="utf-8", errors="replace") if err_file.is_file() else ""
    kind = interrupt.classify_end(status == "failed", run.api_errors, err_text, bool(clock_jumps), run.last)
    run.set_agent_retry({"status": status, "endedAt": db.now(), "exitCode": code, "stalledAt": "", "tokensIn": run.tin, "tokensOut": run.tout, "lastLine": run.last, "ctxTokens": run.ctx, "subCtx": run.sub_ctx,
                         **({"startCtx": run.start_ctx} if run.start_ctx else {}), **({"cacheRead": run.cache_read, "cacheWrite": run.cache_write} if run.cache_read is not None else {}),
                         "interrupted": kind, "retryAt": ""})
    if run.final and (a.phase == "queen" or short(run.final, 1500) != short(run.last_said, 1500)):
        run.say("Final message: " + run.final, "Green" if status == "done" else "Red")
    run.say("End: %s (exit %s), total input %s / output %s." % (status, code, overview.fmt_tok(run.tin), overview.fmt_tok(run.tout)),
            "Green" if status == "done" else "Red")
    return 0


def clock_watch(jumps: list, step: float = 5.0, now=time.time, sleep=time.sleep, rounds=None, limit=None) -> None:
    """Thread body: sleep in short steps and append to `jumps` when the wall clock advanced by more than `limit` seconds in one step.

    `limit` is the setting clockJumpSeconds (default interrupt.JUMP_SECONDS). `now`, `sleep` and `rounds` (number of steps, None = forever) are for
    tests that simulate the clock.
    """
    limit = interrupt.JUMP_SECONDS if limit is None else limit
    last = now()
    done = 0
    while rounds is None or done < rounds:
        sleep(step)
        cur = now()
        if abs(cur - last - step) > limit:
            jumps.append(cur - last)
        last = cur
        done += 1


def complete_build_measure(conn, project, name, jobs, start_free_mb, sample_file, started, parallel=1, count=buildjobs.DEFAULT_MEASUREMENTS):
    """End the measurement of one build: take the highest RAM sum of the build processes from the samples, store the use per job (that sum divided
    by the jobs and the parallel value) and log one line. A build in which no build process was seen stores nothing.
    Returns the use per job in MB, or None when nothing was stored."""
    smp = buildjobs.read_samples(sample_file)
    low = start_free_mb if smp["FreeMin"] is None else min(start_free_mb, smp["FreeMin"])
    dur = (datetime.now() - started).total_seconds()
    secs = int(dur)
    head = "build %s: jobs=%d, duration=%02d:%02d:%02d" % (name, jobs, secs // 3600, secs % 3600 // 60, secs % 60)
    if smp["Seen"] <= 0 or smp["PeakMB"] <= 0:
        runtime.append_event(conn, project, head + ", no build process seen, nothing stored", kind="rounds", agent=name, mirror="rounds")
        return None
    use = buildjobs.use_mb(smp["PeakMB"], jobs, parallel)
    stored = buildjobs.save_per_job(conn, project, use, jobs, start_free_mb, low, dur, peak_mb=smp["PeakMB"], parallel=parallel, count=count)
    runtime.append_event(conn, project, "%s, build process RAM peak=%d MB, use per job=%d MB (median %d MB)" % (head, smp["PeakMB"], use, stored),
                         kind="rounds", agent=name, mirror="rounds")
    return use


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
    try:
        return run_agent(a)
    except Exception as exc:   # a runner that dies before its first row write must leave its reason behind, not vanish silently
        import traceback
        text = traceback.format_exc()
        folder = (Path(a.runs_dir) if a.runs_dir else osplat.runs_dir()) / a.name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "runner-error.txt").write_text(text, encoding="utf-8")
            conn = db.connect(config.db_path())
            try:
                runtime.upsert_agent(conn, a.project, a.name,
                                     {"status": "failed", "endedAt": db.now(), "exitCode": 98, "lastLine": short("runner error: " + str(exc))})
            finally:
                conn.close()
        except Exception:
            pass
        sys.stderr.write(text)
        return 98


if __name__ == "__main__":
    sys.exit(main())
