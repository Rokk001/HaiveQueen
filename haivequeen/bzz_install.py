"""Install the interactive inbox workflow into the user's CLI profiles."""
from pathlib import Path

PROMPT = """Handle the open HaiveQueen plan questions across all registered Apiaries.
Run `bzz questions --open` and inspect the JSON. Report the open questions.
Read the machine language from the HaiveQueen config.toml in BZZ_CONFIG_DIR,
or in the operating system's HaiveQueen configuration directory when unset.
For each Apiary, read its project.toml and apply its ownerTopics and language.
For technical questions, investigate and answer them yourself with `bzz answer ID TEXT --by coordinator`.
For questions about behaviour, content or design, ask the user one at a time,
labelled Question X of Y in the user's configured language. After each answer,
run `bzz answer ID TEXT --by owner`. Do not invent user answers. Respect any
configured topics that must go to the owner. Report failures and unanswered questions.
"""


def install(home=None):
    """Install supported commands; return per-CLI status without overwriting unrelated files."""
    home = Path(home) if home is not None else Path.home()
    paths = {
        "Claude Code": home / ".claude/commands/bzz.md",
        "Codex CLI": home / ".agents/skills/bzz/SKILL.md",
        "OpenCode": home / ".config/opencode/commands/bzz.md",
        "Kilo Code": home / ".config/kilo/commands/bzz.md",
    }
    result = {}
    for name, path in paths.items():
        body = PROMPT
        if name == "Codex CLI":
            body = "---\nname: bzz\ndescription: Handle open HaiveQueen plan questions.\n---\n\n" + body
        try:
            if path.exists() and path.read_text(encoding="utf-8") != body:
                result[name] = "existing command was not replaced"
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            result[name] = "installed: use /skills or $bzz" if name == "Codex CLI" else "installed"
        except OSError as exc:
            result[name] = str(exc)
    result["Custom CLI"] = "skipped: command mechanism is unknown"
    return result
