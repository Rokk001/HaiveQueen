"""Claude Code PreToolUse hook that allows a Queen's own-Plan bzz commands and its integration push.

The hook is run as `python -I claude_hook.py <policy.json>` and therefore uses only the standard library.
It reads the hook event from stdin and prints an "allow" decision only for one simple command whose
arguments, parsed exactly like a narrow common subset of Bash and PowerShell, name the Queen's own
project, plan and agent, and for `ready` and `queue upsert` her own plan branch. Anything else produces no output, so Claude's normal permission rules decide.
A plan branch created after the start is looked up again when the hook runs (plan, state file or the worktree she recorded as her cwd).
"""
import json
import os
import re
import sys

SAFE_BARE = re.compile(r"[A-Za-z0-9_./:=+-]+")
SAFE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
UNSAFE_ANYWHERE = set("‘’‚‛“”„‟")   # PowerShell also treats these as quotes

ANY, PROJECT, PLAN, AGENT, NAME, INT, BRANCH = "any", "project", "plan", "agent", "name", "int", "branch"
QUEEN, CWD_FIELDS, CWD_PATH = "queen", "cwd-fields", "cwd-path"
QUEUE_STATUSES = ("ready", "in_run", "integrated", "blocked", "withdrawn")

# (command, action) -> (required options, optional options, flags without a value)
SCHEMA = {}


def _add(command, actions, required, optional=None, flags=()):
    """Register the accepted options for each action (None = no action word) of one command."""
    for action in actions:
        SCHEMA[(command, action)] = (dict(required), dict(optional or {}), set(flags))


_BEE_OPTIONAL = {o: ANY for o in ("--name", "--provider", "--task", "--steps", "--paths", "--rules", "--cwd",
                                  "--attempt", "--open-requirements", "--error", "--blocked-ref")}
_BEE_OPTIONAL.update({"--outcome": ("failed", "interrupted", "succeeded"),
                      "--blocked-by": ("question", "build", "owner", "package", "provider")})
_add("bee", ("start", "result", "wait"), {"--project": PROJECT, "--plan": PLAN, "--package": ANY, "--class": ANY},
     _BEE_OPTIONAL, ("--subagents", "--no-subagents"))
_add("package", (None,), {"--project": PROJECT, "--plan": PLAN, "--package": ANY,
                          "--state": ("working", "waiting", "implemented", "done", "rejected")}, {"--reason": ANY})
_add("plan", ("check",), {"--project": PROJECT, "--plan": PLAN})
_add("integration", ("acquire", "release"), {"--project": PROJECT, "--plan": PLAN}, {"--owner-pid": INT})
_add("compile-check", (None,), {"--project": PROJECT, "--plan": PLAN}, {"--worktree": ANY})
_add("ready", (None,), {"--project": PROJECT, "--branch": BRANCH, "--tip": NAME, "--plan": PLAN}, {"--note": ANY})
_add("ask", (None,), {"--project": PROJECT, "--plan": PLAN, "--item": ANY, "--question": ANY}, {"--recommendation": ANY})
_add("note", (None,), {"--project": PROJECT, "--text": ANY})
_add("done", (None,), {"--agent": AGENT, "--result": ANY})
_add("status", (None,), {"--project": PROJECT}, flags=("--json",))
_add("questions", (None,), {"--project": PROJECT}, {"--plan": PLAN, "--item": ANY, "--status": ANY}, ("--open",))
_add("views", (None,), {"--project": PROJECT})
_add("queue", (None, "list"), {"--project": PROJECT}, {"--branch": ANY})   # read only; without an action word it lists too
_add("queue", ("upsert",), {"--project": PROJECT, "--branch": BRANCH},
     {"--plan": PLAN, "--tip": NAME, "--status": QUEUE_STATUSES, "--note": ANY, "--run": ANY})
_add("decisions", ("list",), {"--project": PROJECT}, {"--plan": PLAN, "--item": ANY}, ("--all",))
_add("decisions", ("add",), {"--project": PROJECT, "--plan": PLAN, "--source": ANY, "--text": ANY}, {"--item": ANY})
_add("agents", ("list",), {"--project": PROJECT}, {"--scope": ("active", "archive", "all"), "--status": ANY})
_add("packages", ("list",), {"--project": PROJECT}, {"--plan": PLAN})
# The Queen records her worktree with exactly one of --cwd <path> (works in every shell) or --fields '{"cwd": ...}'
# (Windows PowerShell 5.1 strips the inner double quotes, so that form fails there without a change).
_add("agents", ("update-fields",), {"--project": PROJECT, "--name": QUEEN}, {"--cwd": CWD_PATH, "--fields": CWD_FIELDS})
ONE_OF = {("agents", "update-fields"): {"--cwd", "--fields"}}


def split_command(command, shell):
    """Split one simple command into words; None when Bash and PowerShell might read it differently or it is not simple.

    Accepted words: bare words of letters, digits and _ . / : = + -; a single-quoted string without quotes inside;
    a double-quoted string without $ ` \\ ! or quotes inside. A word is never empty or a mix of quoted and bare parts.
    PowerShell may start with the call operator &.
    """
    if not isinstance(command, str) or any(c in UNSAFE_ANYWHERE or (ord(c) < 32 and c != " ") or ord(c) == 127 for c in command):
        return None
    text = command.strip(" ")
    words, call, first_quoted = [], False, False
    if shell == "PowerShell" and text.startswith("& "):
        call, text = True, text[2:].lstrip(" ")
    i = 0
    while i < len(text):
        if text[i] == " ":
            i += 1
            continue
        if text[i] in "'\"":
            quote = text[i]
            end = text.find(quote, i + 1)
            if end < 0:
                return None
            word = text[i + 1:end]
            bad = "'" if quote == "'" else "'\"$`\\!"   # a double quote inside single quotes is checked per option
            if not word or any(c in bad for c in word):
                return None
            i = end + 1
            if i < len(text) and text[i] != " ":
                return None
            first_quoted = first_quoted or not words
        else:
            end = text.find(" ", i)
            end = len(text) if end < 0 else end
            word = text[i:end]
            if not SAFE_BARE.fullmatch(word):
                return None
            if shell == "PowerShell" and re.match(r"[-+.]?[0-9]", word) and not re.fullmatch(r"[1-9][0-9]{0,8}|0", word):
                return None   # PowerShell would pass a number literal (0x10, 1kb, 1e3, 007) in its converted form
            i = end
        words.append(word)
    if call and not first_quoted:
        return None   # the call operator only for a quoted program path
    return words


def _path_key(value):
    """Comparable form of a program path: forward slashes, Git Bash /c/ drive form, case-insensitive drive paths."""
    value = str(value).replace("\\", "/")
    m = re.match(r"^/([A-Za-z])/(.*)$", value)
    if m:
        value = m.group(1) + ":/" + m.group(2)
    return value.lower() if re.match(r"^[A-Za-z]:/", value) else value


def _plan_key(value):
    """Plan paths compare with forward slashes."""
    return str(value).replace("\\", "/")


def bzz_arguments(words, policy):
    """Return the bzz arguments after the program, or None when the program is neither bzz nor the policy's CLI."""
    if words and words[0] == "bzz":
        return words[1:]
    python, cli = policy.get("python"), policy.get("cli")
    if (len(words) >= 3 and python and cli and _path_key(words[0]) == _path_key(python) and words[1] == "-I"
            and _path_key(words[2]) == _path_key(cli)):
        return words[3:]
    return None


def _cwd_fields_ok(value):
    """True for a JSON object with only "cwd": an existing absolute directory without a .. part."""
    try:
        fields = json.loads(value)
    except ValueError:
        return False
    if not isinstance(fields, dict) or set(fields) != {"cwd"} or not isinstance(fields["cwd"], str):
        return False
    return _cwd_path_ok(fields["cwd"])


def _cwd_path_ok(cwd):
    """True for an existing absolute directory without a .. part and without a trailing backslash
    (Windows PowerShell 5.1 would let it escape the closing quote of an argument with a space)."""
    if cwd.endswith("\\"):
        return False
    if not re.fullmatch(r"(?:[A-Za-z]:[\\/]|/)[A-Za-z0-9_ ./:\\-]*", cwd) or ".." in re.split(r"[\\/]", cwd):
        return False
    return os.path.isdir(cwd)


def live_branch(policy):
    """The Queen's plan branch resolved now (it may have been created after the start), or None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(str(policy.get("cli") or ""))))
    if not policy.get("name") or not os.path.isfile(os.path.join(root, "haivequeen", "agent_runner.py")):
        return None
    sys.path.insert(0, root)
    from haivequeen import agent_runner
    return agent_runner.queen_branch_now(policy["project"], policy["plan"], policy["name"], policy.get("integrationBranch"))


def _value_ok(kind, value, policy, lookup=live_branch):
    """Check one option value against its constraint."""
    if kind == ANY:
        return True
    if kind == PROJECT:
        return value == str(policy.get("project"))
    if kind == PLAN:
        return _plan_key(value) == _plan_key(policy.get("plan"))
    if kind == AGENT:
        return policy.get("agent") is not None and value == str(policy.get("agent"))
    if kind == BRANCH:   # only the Queen's own plan branch: the one known at the start, else the one resolved now
        if policy.get("branch") and value == str(policy["branch"]):
            return True
        if not SAFE_NAME.fullmatch(value) or ".." in value or lookup is None:
            return False
        try:
            now = lookup(policy)
        except Exception:
            return False
        return bool(now) and value == str(now) and value != str(policy.get("integrationBranch"))
    if kind == QUEEN:
        return bool(policy.get("name")) and value == str(policy["name"])
    if kind == CWD_FIELDS:
        return _cwd_fields_ok(value)
    if kind == CWD_PATH:
        return _cwd_path_ok(value)
    if kind == NAME:
        return bool(SAFE_NAME.fullmatch(value)) and ".." not in value
    if kind == INT:
        return value.isdigit()
    return value in kind


def bzz_allowed(args, policy, lookup=live_branch):
    """True when args are one accepted own-Plan bzz command: exact option names, each at most once, no extras."""
    if not args:
        return False
    command, rest = args[0], args[1:]
    if command == 'agent':
        command = 'bee'  # The legacy command and its replacement share the same policy.
    action = rest[0] if rest and not rest[0].startswith("-") else None
    schema = SCHEMA.get((command, action))
    if schema is None:
        return False
    if action is not None:
        rest = rest[1:]
    required, optional, flags = schema
    seen = set()
    if any('"' in word for word in args[:len(args) - len(rest)]):
        return False
    i = 0
    while i < len(rest):
        opt = rest[i]
        if opt in seen:
            return False
        seen.add(opt)
        if opt in flags:
            i += 1
            continue
        kind = required.get(opt, optional.get(opt))
        if kind is None or i + 1 >= len(rest):
            return False
        value = rest[i + 1]
        if '"' in value and kind != CWD_FIELDS:   # Bash and PowerShell may pass inner double quotes differently
            return False
        if value.startswith("-") and value != "-" or not _value_ok(kind, value, policy, lookup):
            return False
        i += 2
    one_of = ONE_OF.get((command, action))
    if one_of and len(seen & one_of) != 1:
        return False
    return all(opt in seen for opt in required)


def push_allowed(words, policy):
    """True for exactly `git push <remote> HEAD:<integration branch>` of the policy."""
    remote, branch = policy.get("remote"), policy.get("integrationBranch")
    return bool(remote and branch) and words == ["git", "push", str(remote), "HEAD:%s" % branch]


def decide(event, policy, lookup=live_branch):
    """Return the reason for an allow decision, or None to leave the decision to Claude's permission rules."""
    if not isinstance(event, dict) or event.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    shell = event.get("tool_name")
    tool_input = event.get("tool_input")
    if shell not in ("Bash", "PowerShell") or not isinstance(tool_input, dict):
        return None
    words = split_command(tool_input.get("command"), shell)
    if not words:
        return None
    if push_allowed(words, policy):
        return "HaiveQueen: push to the integration branch of this Queen"
    args = bzz_arguments(words, policy)
    if args is not None and bzz_allowed(args, policy, lookup):
        return "HaiveQueen: own-Plan bzz command of this Queen"
    return None


def main(argv=None):
    """Hook entry point; never blocks a tool call, it only adds an allow decision."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        with open(argv[0], encoding="utf-8") as f:
            policy = json.load(f)
        event = json.loads(sys.stdin.read())
        reason = decide(event, policy)
    except Exception:
        return 0
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                            "permissionDecision": "allow",
                                                            "permissionDecisionReason": reason}}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
