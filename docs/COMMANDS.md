# Command reference

Run `bzz --help` for groups and `bzz <command> --help` for exact options and defaults. Command responses are generally JSON; this describes terminal output, not exported file formats. `bzz export` creates a SQLite database backup when due (`--force` runs it immediately); `bzz views` writes Markdown files. [Runtime API](RUNTIME-API.md) defines low-level state operations.

`bzz agent` is the standard package-agent command; `bzz bee` remains a compatibility alias. Existing settings and machine-readable field names keep their spelling. Free-text answers to owner-check questions are notes and leave the check open.

The following commands are for the owner. Every one refuses calls with `BZZ_AGENT` set. Plan commands require `--project p --plan f`:

| Command | Effect |
| --- | --- |
| `bzz plan accept --project p --plan f [--text "note"]` | Accept an integrated plan waiting for its owner check; close all owner checks and set DONE. Other open questions and integration steps block acceptance; queen-ended and stall questions are closed automatically. |
| `bzz plan reject --project p --plan f --text "finding" [--package P1 --package P2]` | Reopen a plan waiting for its owner check and pass the required finding to a fix agent. Selected packages become rejected. Accept and reject also work while paused; rejection preserves the pause until resume. |
| `bzz plan stop-gracefully --project p --plan f` | Stop new starts for one plan in SENT through REVIEW, preserving its prior status and running agents. DRAFT, DONE, PAUSED and CANCELLED are refused. `bzz plan pause` remains an alias. |
| `bzz plan force-stop --project p --plan f` | Stop the plan's Queen, agents and running integration step immediately; the plan remains stopped until resumed. |
| `bzz plan resume --project p --plan f` | Restore the prior status of a PAUSED plan. Other statuses are refused. |
| `bzz plan release --project p --plan f [--commit C]` | Release the branch without a check agent, for the specified commit or any commit when omitted. Refused for DONE/CANCELLED. Replaces older releases, including legacy inbox decisions; rejection ends the release. |
| `bzz provider enable --provider N` / `bzz provider disable --provider N` | Save the same Enabled setting as the provider page. |
| `bzz provider test --provider N` | Run and store the provider page's Local LLM connection test; other access types have nothing to test. |
| `bzz package unassign --project p --plan f --package P` | Clear the stored provider for the next start, even while the agent runs. Keep the escalated class and failure counts. No assignment is a successful no-op. |

## Full command inventory
The inventory includes compatibility aliases. Required inputs are marked; optional inputs are listed by name. For group commands with an action, choose a listed action. Do not invoke state-mutating agent commands against an unrelated project.

| Command | Inputs |
| --- | --- |
| `bzz agent` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent start` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent result` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent history` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent wait` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent force-stop` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz agent stop` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee start` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee result` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee history` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee wait` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee force-stop` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz bee stop` | `target`, `--project (required)`, `--plan`, `--package`, `--class`, `--name`, `--provider`, `--task`, `--steps`, `--paths`, `--rules`, `--cwd`, `--outcome {failed,interrupted,succeeded,progress}`, `--repair-result`, `--attempt`, `--before`, `--limit`, `--open-requirements`, `--error`, `--blocked-by {question,build,owner,package,provider}`, `--blocked-ref`, `--ended-evidence`, `--subagents`, `--no-subagents` |
| `bzz package` | `--project (required)`, `--plan (required)`, `--package (required)`, `--state {working,waiting,implemented,done,rejected}`, `--reason` |
| `bzz package unassign` | `--project (required)`, `--plan (required)`, `--package (required)`, `--state {working,waiting,implemented,done,rejected}`, `--reason` |
| `bzz plan` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan check` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan accept` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan reject` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan stop-gracefully` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan force-stop` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan pause` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan resume` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz plan release` | `--project (required)`, `--plan (required)`, `--text`, `--package`, `--commit` |
| `bzz provider` | `--provider (required)` |
| `bzz provider enable` | `--provider (required)` |
| `bzz provider disable` | `--provider (required)` |
| `bzz provider test` | `--provider (required)` |
| `bzz integration` | `--project (required)`, `--plan (required)`, `--owner-pid` |
| `bzz integration acquire` | `--project (required)`, `--plan (required)`, `--owner-pid` |
| `bzz integration release` | `--project (required)`, `--plan (required)`, `--owner-pid` |
| `bzz dequeen` | `plan (required)` |
| `bzz requeen` | `plan (required)`, `provider` |
| `bzz done` | `--agent (required)`, `--result (required)` |
| `bzz ready` | `--project (required)`, `--branch (required)`, `--tip (required)`, `--plan (required)`, `--note` |
| `bzz compile-check` | `--project (required)`, `--plan (required)`, `--worktree` |
| `bzz ask` | `--project (required)`, `--plan (required)`, `--item (required)`, `--question (required)`, `--recommendation` |
| `bzz note` | `--project (required)`, `--text (required)` |
| `bzz status` | `--project`, `--json` |
| `bzz questions` | `--open`, `--project`, `--status`, `--plan`, `--item` |
| `bzz answer` | `id (required)`, `text (required)`, `--by {owner,coordinator} (required)` |
| `bzz queue` | `--project`, `--branch`, `--tip`, `--plan`, `--status`, `--note`, `--run` |
| `bzz queue list` | `--project`, `--branch`, `--tip`, `--plan`, `--status`, `--note`, `--run` |
| `bzz queue upsert` | `--project`, `--branch`, `--tip`, `--plan`, `--status`, `--note`, `--run` |
| `bzz views` | `--project (required)` |
| `bzz export` | `--force` |
| `bzz init` | `folder (required)` |
| `bzz register` | `folder (required)` |
| `bzz agents list` | `--project`, `--scope {active,archive,all}`, `--status` |
| `bzz agents get` | `--project (required)`, `--name (required)` |
| `bzz agents upsert` | `--project (required)`, `--name (required)`, `--fields (required)` |
| `bzz agents update-fields` | `--project (required)`, `--name (required)`, `--fields`, `--cwd` |
| `bzz agents archive` | `--project (required)`, `--hours`, `--match` |
| `bzz event append` | `--project (required)`, `--text (required)`, `--kind`, `--agent`, `--data`, `--mirror {rounds,events,both,none}`, `--error`, `--log-dir` |
| `bzz events list` | `--project`, `--since-id`, `--kind`, `--limit` |
| `bzz events tail` | `--project`, `--since-id`, `--kind`, `-n` |
| `bzz events paths` | `--project (required)`, `--log-dir` |
| `bzz kv get` | `--project (required)`, `--key (required)` |
| `bzz kv set` | `--project (required)`, `--key (required)`, `--value (required)` |
| `bzz kv delete` | `--project (required)`, `--key (required)` |
| `bzz kv list` | `--project (required)`, `--prefix` |
| `bzz handled check` | `--project (required)`, `--key (required)` |
| `bzz handled add` | `--project (required)`, `--key (required)`, `--value` |
| `bzz handled remove` | `--project (required)`, `--key (required)` |
| `bzz handled list` | `--project (required)` |
| `bzz nofollow list` | `--project (required)` |
| `bzz nofollow add` | `--project (required)`, `--key (required)` |
| `bzz nofollow remove` | `--project (required)`, `--key (required)` |
| `bzz build add` | `--project (required)`, `--host`, `--jobs (required)`, `--free-start-mb`, `--free-min-mb`, `--per-job-mb`, `--duration-s` |
| `bzz build max-per-job` | `--project (required)`, `--host` |
| `bzz usage add` | `--provider`, `--model`, `--tokens-in`, `--tokens-out`, `--kind`, `--project`, `--trigger`, `--context`, `--duration-s`, `--actions`, `--error`, `--ts` |
| `bzz usage list` | `--kind`, `--project`, `--since`, `--limit` |
| `bzz plans sync` | `--project (required)`, `--workspace`, `--done` |
| `bzz plans list` | `--project` |
| `bzz plans set-status` | `--project (required)`, `--plan (required)`, `--status (required)`, `--workspace` |
| `bzz packages list` | `--project`, `--plan` |
| `bzz packages set` | `--project (required)`, `--plan (required)`, `--id (required)`, `--phase`, `--locked`, `--reason` |
| `bzz decisions list` | `--project`, `--plan`, `--item`, `--all` |
| `bzz decisions update` | `id (required)`, `--plan`, `--item`, `--status {active,rule,superseded}` |
| `bzz decisions add` | `--project (required)`, `--source (required)`, `--text (required)`, `--plan`, `--item` |
| `bzz run-state` |  |
| `bzz starts-allowed` |  |
| `bzz reattach` | `--project (required)`, `--alive-pids` |
| `bzz import-legacy` | `--project (required)`, `--runtime`, `--questions`, `--answers`, `--queue`, `--plans` |
| `bzz start` | `--foreground`, `--rounds`, `--round-minutes`, `--keep-pause`, `--desktop` |
| `bzz gui` | `--detach`, `--project` |
| `bzz stop` | `--force`, `--desktop` |
| `bzz web` |  |
| `bzz web setup` |  |
| `bzz web off` |  |
| `bzz web start` |  |
| `bzz web stop` |  |
| `bzz web restart` |  |
| `bzz web serve` |  |
| `bzz self-update` | `--worker`, `--version` |
| `bzz update` | `--install` |
| `bzz worktrees` | `--project`, `--dry-run` |
| `bzz worktrees prune` | `--project`, `--dry-run` |

`bzz agent history` pages stored attempts with `--limit` (1–20), `--before`, or retrieves one `--attempt`. Result submission binds evidence to an attempt; an interruption or external blocker is not a model failure. `--ended-evidence` does not release a slot while the matching process is known to remain alive.

`bzz integration acquire` and `release` remain compatibility/runtime coordination interfaces; they do not authorize Queens to merge, check, build, push or deploy. The [Orchestrator pipeline](OPERATIONS.md#integration) owns those actions.

## Operational command behavior

| Command | Who | What it does |
|---|---|---|
| `bzz start` / `bzz stop` | owner | start the loop in the background and open the dashboard (`--foreground`, `--rounds N`, `--round-minutes M`), or stop starting new agents |
| `bzz gui [--detach] [--project p]` | owner | open the browser dashboard |
| `bzz web setup` / `off` / `start` / `stop` / `restart` | owner | configure network access or control the independent web listener; see the [web guide](WEB-API.md) |
| `bzz stop --force` | owner | show the warning and immediately stop running work and the Orchestrator; the web server remains available |
| `bzz status [--project p] [--json]` | owner | "X / Y integrated (%)" and a task / agent / status table per project |
| `bzz questions [--open]`, `bzz answer <id> "<text>" --by owner` | owner | read and answer questions of the agents |
| `bzz queue [upsert]` | owner, Queen | the integration queue |
| `bzz decisions list [--all]`, `bzz decisions update <id> --status active\|rule\|superseded` | owner | decisions; the list shows only `active` ones unless `--all` |
| `bzz init <folder>`, `bzz register <folder>` | owner | create and register a project |
| `bzz export`, `bzz views --project p` | owner | export the state, write the Markdown views |
| `bzz import-legacy ...` | owner | import the state of version 1 (see [v1 migration](MIGRATION-v1.md)) |
| `bzz agent force-stop <name>` | owner | Force stop the one running Agent (`bzz agent stop` remains a compatibility alias; the same action as the dashboard button and an inbox `[[stop_agent]]` entry with `name`). A subagent of the Queen is refused with the reason (use the plan stop controls), the Queen is stoppable only by `bzz dequeen`, and agents cannot use the command. The package stays stopped and waits for an explicit owner instruction; the Queen is told by an owner message |
| `bzz dequeen <plan>` / `bzz requeen <plan> [provider]` | owner | stop the plan Queen and separate agents, leaving it queenless; or restart with a new Queen, optionally on another provider |
| `bzz agent start`, `bzz agent result`, `bzz agent wait` | Queen | start an Agent (a refused start answers `waiting` with a `reason`), record an attempt or wait for its provider; a failed result needs evidence (`--open-requirements` or `--error`) or `--blocked-by question\|build\|owner\|package\|provider` (a question also needs `--blocked-ref <id>`), which is no failure and sets the package to waiting |
| `bzz package --project p --plan f --package id --state working\|waiting\|implemented\|done\|rejected [--reason ...]` | Queen | report the progress of a package; `waiting` needs `--reason` (`question:<id>`, `build`, `owner`, `package` or `provider`); the dashboard progress bar counts the done ones |
| `bzz integration acquire` / `release` | runtime compatibility | coordinate the per-project integration place for runtime compatibility; an external caller supplies a stable owner process id. This is not permission for Queens to integrate |
| `bzz update [--install X]` | owner | check GitHub for a newer release now, or install the offered release X (see [Updates](OPERATIONS.md#updates)) |
| `bzz self-update` | owner | one-time transition to the release updates only: for a HaiveQueen checkout that is itself a project named HaiveQueen, waits until all agents have ended, then fast-forwards its `main` from `origin/<integrationBranch>` of that project's settings (default `main`); refused once a release was published for that project or the checkout is at a release tag – then use `bzz update` |
| `bzz done`, `bzz ready`, `bzz ask`, `bzz note` | agents | final report, branch ready for integration, question, note ([runtime API](RUNTIME-API.md)) |

A diagnosed repair may report `--outcome progress --repair-result <JSON file>` when every assigned repair criterion passed but the package remains open. The Queen reviews the receipt and logs; include the normal `--ended-evidence` for embedded Agents. HQ binds the receipt to the assigned attempt, contract and current commit and checks every prescribed command, zero exit code and nonempty local log. Protocol 5 additionally binds the complete workflow to package requirements and decisions and requires its independent scope review before dispatch. Baseline evidence, changed implementation files, unchanged regression snapshots and improved workflow checkpoints remain required. Unbound legacy receipts return `outcome: observed`, retaining evidence without granting repair progress. Progress preserves failure history without incrementing it or marking the package done. A pending diagnosis returns `nextAction: wait_for_diagnosis` instead of a contradictory `nextClass`; actual owner waits return `wait_for_owner`. See [PACKAGE-DIAGNOSIS-PROGRESS.md](PACKAGE-DIAGNOSIS-PROGRESS.md).
