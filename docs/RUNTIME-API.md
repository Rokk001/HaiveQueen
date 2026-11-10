# Runtime API

The orchestrator scripts read and write their runtime state through `haivequeen/cli.py`. Every command prints one JSON value on stdout.
On a failure it prints `{"ok": false, "error": "..."}` and exits with 1 (usage errors with 2).

- Call: `python -I haivequeen/cli.py <command> [<action>] [options]`
- The database is `haivequeen.db` in the data folder (`BZZ_DATA_DIR`, else the machine default). It uses WAL and a busy timeout of 10 s and must lie on a local disk: opening it on a network drive (UNC path, mapped share, NFS/CIFS mount) fails with an error;
  every command is one transaction, so several processes can call at the same time.
- `--project` names the project. List commands accept it as optional filter (without it: all projects); all other commands require it.
- An option value of a single `-` is read from stdin (`--fields`, `--value`, `--text` of `event append` and `decisions add`, `--data`), for long or awkward text.
- Text mirror files: `event append` also appends one line to a plain text log in `BZZ_LOG_DIR` (else `<data folder>/logs/<project>`):
  `ROUNDS.log` lines are `YYYY-MM-DD HH:MM:SS text`, `ORCH-EVENTS.log` lines are `HH:MM|O or E|text`.
- Agent rows are flat. Known fields are columns (camelCase or snake_case accepted, `plan` is `plan_path`); any other field is stored and returned as a free field.

## Commands

| Command | Options | Result |
|---|---|---|
| `agents list` | `[--project] [--scope active\|archive\|all] [--status]` | array of agent rows (default scope: not archived) |
| `agents get` | `--project --name` | newest row of that name, or `null` |
| `agents upsert` | `--project --name --fields <json>` | the row; creates it if there is no active row of that name |
| `agents update-fields` | `--project --name` and exactly one of `--fields <json>` or `--cwd <path>` (sets only `cwd`, no JSON quoting needed) | the row; error if the agent does not exist |
| `agents archive` | `--project [--hours 12] [--match <regex>]` | `{moved}`; ended rows older than the hours, or rows whose plan/name match, get `archived` |
| `event append` | `--project --text [--kind] [--agent] [--data <json>] [--mirror rounds\|events\|both\|none] [--error] [--log-dir]` | `{id, ts}`; history row plus mirror line(s) |
| `events list` | `[--project] [--since-id N] [--kind] [--limit N]` | events after the id, oldest first |
| `events tail` | `[--project] [-n 20] [--since-id N] [--kind]` | the newest events, oldest first |
| `events paths` | `--project [--log-dir]` | `{rounds, events}` file paths of the mirrors |
| `kv get` / `set` / `delete` / `list` | `--project --key [--value] [--prefix]` | `{found, value}` / `{ok}` / `{deleted}` / array of `{key, value, updated_at}` |
| `handled check` | `--project --key` | `{handled, value, ts}` |
| `handled add` / `remove` / `list` | `--project --key [--value]` | `{ok}` / `{deleted}` / array; `add` replaces the value (for example a retry counter) |
| `nofollow list` / `add` / `remove` | `--project [--key]` | array of keys / `{ok}` / `{deleted}`; keys are stored in lower case |
| `build add` | `--project --jobs [--host] [--free-start-mb] [--free-min-mb] [--per-job-mb] [--duration-s]` | `{ok}` |
| `build max-per-job` | `--project [--host]` | `{per_job_mb}` highest value of this host and project, or `null` |
| `usage add` | `[--project --provider --model --tokens-in --tokens-out --kind --trigger --context --duration-s --actions --error --ts]` | `{ok}`; `kind` describes the recorded usage |
| `usage list` | `[--kind] [--project] [--since <iso>] [--limit N]` | rows oldest first (with `--limit`: the newest N) |
| `plans sync` | `--project [--workspace] [--done id,id]` | `{plans, startable}`; reads the plan files (globs from the project settings) into `plans`/`packages`, keeps phase and lock, `startable` follows rule 11 and skips locked packages |
| `plans list` | `[--project]` | plan rows |
| `plans set-status` | `--project --plan --status [--workspace]` | `{ok, plan, status}`; writes the status into the table and the plan file |
| `packages list` | `[--project] [--plan]` | package rows (`depends_on` as array, `locked` as boolean; `agent_type` and `capability` are the `agent` and `capability` fields of the plan header) |
| `packages set` | `--project --plan --id [--phase] [--locked 0\|1] [--reason]` | the package row; phase `done` counts as done for `plans sync` |
| `queue [list]` | `[--project]` | integration queue rows |
| `queue upsert` | `--project --branch [--tip] [--plan] [--status ready\|in_run\|integrated\|blocked\|withdrawn] [--note] [--run]` | the row; changes the newest row of the branch, a new tip after `integrated` adds a row |
| `questions` | `[--project] [--open] [--status] [--plan] [--item]` | question rows |
| `decisions list` | `[--project] [--plan] [--item] [--all]` | decision rows; only `active` ones unless `--all` (status values: `active`, `rule`, `superseded`) |
| `decisions add` | `--project --source owner\|coordinator\|auto --text [--plan] [--item]` | `{ok, id}` |
| `decisions update` | `<id> [--plan] [--item] [--status active\|rule\|superseded]` | `{ok, decision}` |
| `pause` / `resume` | none | the run state `{state, since, running_agents}`; see "Run state" below |
| `run-state` | none | `{state, since, running_agents}` |
| `starts-allowed` | none | `{allowed, state}`; the round asks this before every agent start |
| `reattach` | `--project --alive-pids 123,456` | `{ok, kept, failed}`; see "Run state" below |
| `done`, `ready`, `ask`, `note`, `answer`, `status` | see the command line help | agent and owner commands |

## Run state and restart (D8)

The run state is one machine-wide value: kv row with project `*`, key `run-state`, value `{"state","since"}`; it survives restarts. No row means `RUNNING`. State changes write events with project `*`.

- `pause`: any state to `PAUSED`. `resume`: any state to `RUNNING`. A `DRAINING` or `DRAINED` value stored by an older version counts as `PAUSED`.
- `starts-allowed` is true only in `RUNNING`. Running agents are never stopped.
- `reattach --project p --alive-pids 123,456` (run once on orchestrator start): `running` rows of the project whose PID is in the list stay `running`; rows with another or no PID become `failed` (`last_line` "process gone after orchestrator restart", one `failed` event each). The real PID check stays in PowerShell.

## What stays a file

`STOP`, `HANDOFF`, the loop lock, `idle.pid`, the raw files of a run (`prompt.md`, `rules.md`, `milestones.txt`, `events.jsonl`, `stderr.txt`, `final.txt`, `build-samples.txt`), `window.txt` and the usage cache folder.
The loop state, the event hook state and the Claude usage windows are stored as JSON text with `kv` (suggested keys: `loop-state`, `event-sync`, `usage-claude`).

`bzz agent start` prepares the package worktree and returns the actual Agent `name`, `cwd` and `branch`. The branch is `<plan branch>-<package id in lower case>` and the folder is `<worktree prefix>-<plan tag in lower case>-<package id in lower case>`. The same pair is reused for replacements, including legacy package worktrees. Agents must never create or commit on another branch.

Agent Git commands inherit an attempt-specific `core.hooksPath` through the process environment. The hooks preserve existing repository hooks, refuse foreign-branch commits, and retain evidence independently of reflogs. Use the returned `name` as the result attempt identity, including when it has a suffix to keep historical runs distinct. A subagent start returns `environment` alongside `cwd`, `branch` and `prompt`; apply those variables to every Git command of that subagent, including commands in another worktree. Never apply them to the Queen or another Agent. End reports and terminal process updates count a recorded branch violation as a failed attempt; embedded Agents are checked when their Queen ends too.

At the end of an attempt, HaiveQueen also audits new commit entries in the assigned worktree's HEAD reflog. This detects foreign and detached commits made there after clearing the environment or overriding `core.hooksPath`, including after switching back. Detected evidence is saved independently of subsequent reflog changes. Other worktrees' history is excluded so concurrent Agents are not blamed for each other's commits. The assigned worktree must be used exclusively by its Agent during the attempt; the audit cannot distinguish another process writing in that same worktree.

These checks are cooperative safeguards, not an operating-system security boundary. The fallback needs the worktree's intact reflog: disabling, expiring or rewriting that log before the audit can remove its evidence. Commands that discard the assigned environment and write in another worktree cannot safely be attributed to an Agent. Keep the assigned Git environment and reflogs intact; repository-wide hooks or branch scans would affect concurrent Agents and still would not establish process identity.
