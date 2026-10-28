# Rules for all agents

Placeholder text: replace it with the rules of this project.

## How you work
- One agent does one task (one package of one plan). Do not start work that belongs to another package.
- Report only through the `bzz` commands below; do not edit runtime or state files by hand.
- Do not build, deploy or run long jobs unless the project rules below allow it.
- Keep changes small and on the topic of your task. Do not touch unrelated files.
- If something needed for the task is unclear, ask with `bzz ask` and continue with other work.

## bzz commands
- `bzz done --agent <id> --result <file>`: final report
- `bzz ready --project <p> --branch <b> --tip <sha> --plan <path> --note "<text>"`: register a finished branch
- `bzz ask --project <p> --plan <path> --item <i> --question "<q>" --recommendation "<r>"`: ask a question
- `bzz note --project <p> --text "<t>"`: write a note into the history

## Project rules
- (add the rules of this project here: coding style, branches, checks, forbidden content)
