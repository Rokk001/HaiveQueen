"""Install the interactive inbox workflow into the user's CLI profiles."""
from pathlib import Path

PROMPT = """Handle open HaiveQueen plan questions across all registered Apiaries.
Run `bzz questions --open` and inspect the JSON; report the open plan questions.
Find registered project names and workspaces in the `projects` list of the machine
config.toml (use `haivequeen.config.load_config()`), and for each question read
<workspace>/.haivequeen/project.toml for its language and ownerTopics. If a
question's workspace cannot be found, report it and leave that question open.
Investigate technical questions yourself. Questions about behaviour, content or
design, and questions matching ownerTopics, must go to the user, one at a time.
Number those owner questions "Question X of Y" in that Apiary's configured
language. Never invent an owner answer.
For each answer create a unique UTF-8 TOML file in the corresponding
<workspace>/.haivequeen/inbox/ directory with one `[[answer]]` table containing
`question_id` (integer), `text` (TOML string), and `by` ("coordinator" for your
technical answers, "owner" for user answers). Write a temporary file first and
rename it to a unique .toml name only when complete. Use a TOML serializer or
correctly escape the text; never overwrite another file. Do not also call
`bzz answer`: the inbox processor records the answer. Report file creation
errors and unanswered questions; do not claim an inbox file has been processed
until its result is known.
"""


def install(home=None):
    """Install supported commands; return per-CLI status without overwriting unrelated files."""
    home = Path(home) if home is not None else Path.home()
    paths = {
        "Claude Code": home / ".claude/commands/bzz.md",
        "OpenCode": home / ".config/opencode/commands/bzz.md",
        "Kilo Code": home / ".config/kilo/commands/bzz.md",
    }
    result = {}
    for name, path in paths.items():
        body = PROMPT
        try:
            if path.exists() and path.read_text(encoding="utf-8") != body:
                result[name] = "existing command was not replaced"
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            result[name] = "installed"
        except OSError as exc:
            result[name] = str(exc)
    result["Codex CLI"] = "skipped: literal /bzz is not supported"
    result["Custom CLI"] = "skipped: command mechanism is unknown"
    return result
