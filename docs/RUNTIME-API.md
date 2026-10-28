# Runtime API

The orchestrator scripts read and write their runtime state through `orch.py`. Every command prints one JSON value on stdout.
On a failure it prints `{"ok": false, "error": "..."}` and exits with 1 (usage errors with 2).

- Call: `python -I orch.py <command> [<action>] [options]`
- The database is `orchestrator.db` in the data folder (`ORCH_DATA_DIR`, else the machine default). It uses WAL and a busy timeout of 10 s;
  every command is one transaction, so several processes can call at the same time.
- `--project` names the project. List commands accept it as optional filter (without it: all projects); all other commands require it.
- An option value of a single `-` is read from stdin (`--fields`, `--value`, `--text` of `event append` and `decisions add`, `--data`), for long or awkward text.
- Text mirror files: `event append` also appends one line to a plain text log in `ORCH_LOG_DIR` (else `<data folder>/logs/<project>`):
  `ROUNDS.log` lines are `YYYY-MM-DD HH:MM:SS text`, `ORCH-EVENTS.log` lines are `HH:MM|O or E|text`.
- Agent rows are flat. Known fields are columns (camelCase or snake_case accepted, `plan` is `plan_path`); any other field is stored and returned as a free field.

## Commands

| Command | Options | Result |
|---|---|---|
| `agents list` | `[--project] [--scope active\|archive\|all] [--status]` | array of agent rows (default scope: not archived) |
| `agents get` | `--project --name` | newest row of that name, or `null` |
| `agents upsert` | `--project --name --fields <json>` | the row; creates it if there is no active row of that name |
| `agents update-fields` | `--project --name --fields <json>` | the row; error if the agent does not exist |
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
| `usage add` | `[--project --provider --model --tokens-in --tokens-out --kind --trigger --context --duration-s --actions --error --ts]` | `{ok}`; `kind` is for example `brain` or `agent` |
| `usage list` | `[--kind] [--project] [--since <iso>] [--limit N]` | rows oldest first (with `--limit`: the newest N) |
| `plans sync` | `--project [--workspace] [--done id,id]` | `{plans, startable}`; reads the plan files (globs from the project settings) into `plans`/`packages`, keeps phase and lock, `startable` follows rule 11 and skips locked packages |
| `plans list` | `[--project]` | plan rows |
| `plans set-status` | `--project --plan --status [--workspace]` | `{ok, plan, status}`; writes the status into the table and the plan file |
| `packages list` | `[--project] [--plan]` | package rows (`depends_on` as array, `locked` as boolean; `agent_type` and `capability` are the `agent` and `capability` fields of the plan header) |
| `packages set` | `--project --plan --id [--phase] [--locked 0\|1] [--reason]` | the package row; phase `done` counts as done for `plans sync` |
| `queue [list]` | `[--project]` | integration queue rows |
| `queue upsert` | `--project --branch [--tip] [--plan] [--status ready\|in_run\|integrated\|blocked] [--note] [--run]` | the row; changes the newest row of the branch, a new tip after `integrated` adds a row |
| `questions` | `[--project] [--open] [--status] [--plan] [--item]` | question rows |
| `decisions list` | `[--project] [--plan] [--item]` | decision rows |
| `decisions add` | `--project --source owner\|coordinator\|auto --text [--plan] [--item]` | `{ok, id}` |
| `triage list` | `--project` | locked packages with open points and no running triage agent (`phase` `triage`): `[{plan, id, title, lock_reason, points}]`; points are the lines of the lock reason (`item: text`, else `pN`) without a decision or open question |
| `triage prompt` | `--project --plan --package [--python]` | `{prompt, points}`; prompt for the triage agent (class medium): technical points go to `triage decide`, game/balance points to `triage ask`, in doubt game/balance |
| `triage decide` | `--project --plan --package --item --text --reason --source` | `{ok, id, unlocked, remaining}`; writes a decision with source `auto` (text carries reason and source) and unlocks the package after the last point |
| `triage ask` | `--project --plan --package --item --question [--recommendation]` | `{ok, question_id, answered_by: owner, locked: true}`; the package stays locked, unlocking after `answer` is the routine's job |
| `pause` / `resume` / `drain` | none | the run state `{state, since, running_agents}`; see "Run state" below |
| `run-state` | none | `{state, since, running_agents}`; also settles a finished drain |
| `starts-allowed` | none | `{allowed, state}`; the round asks this before every agent start |
| `reattach` | `--project --alive-pids 123,456` | `{ok, kept, failed}`; see "Run state" below |
| `done`, `ready`, `ask`, `note`, `answer`, `status` | see the command line help | agent and owner commands |

## Run state and restart (D8)

The run state is one machine-wide value: kv row with project `*`, key `run-state`, value `{"state","since"}`; it survives restarts. No row means `RUNNING`. State changes write events with project `*`.

- `pause`: any state to `PAUSED`. `resume`: any state to `RUNNING`. `drain`: `RUNNING` or `PAUSED` to `DRAINING`.
- `DRAINING` becomes `DRAINED` automatically (every `drain`, `run-state` and `starts-allowed` call settles it) as soon as no agent (any project, status `starting` or `running`, not archived) runs; exactly one `drained` event is written.
- `starts-allowed` is true only in `RUNNING`. Running agents are never stopped.
- `reattach --project p --alive-pids 123,456` (run once on orchestrator start): `running` rows of the project whose PID is in the list stay `running`; rows with another or no PID become `failed` (`last_line` "process gone after orchestrator restart", one `failed` event each). The real PID check stays in PowerShell.

## PowerShell wrappers (lib.ps1, block "v2 runtime wrappers")

`Invoke-Orch <args> [<stdin text>]` runs `python -I orch.py` (python from `ORCH_PYTHON`, else the python on the PATH), quotes every argument, parses the JSON and throws with the error text when the exit code is not 0.
The other wrappers take the project config first and use the project name from `Get-OrchProject` (`ORCH_PROJECT_NAME`, else `projectName` of the config, else the project file name).

| Wrapper | Command |
|---|---|
| `Get-OrchAgents`, `Get-OrchAgent`, `Set-OrchAgent`, `Update-OrchAgentFields`, `Move-OrchAgentsToArchive` | `agents list`, `get`, `upsert`, `update-fields`, `archive` |
| `Add-OrchEvent`, `Get-OrchEvents`, `Get-OrchLogPaths` | `event append`, `events list` / `tail`, `events paths` |
| `Get-OrchKv`, `Set-OrchKv`, `Remove-OrchKv` | `kv get`, `set`, `delete` |
| `Get-OrchHandled`, `Add-OrchHandled`, `Remove-OrchHandled` | `handled check`, `add`, `remove` |
| `Get-OrchNofollow`, `Add-OrchNofollow`, `Remove-OrchNofollow` | `nofollow list`, `add`, `remove` |
| `Add-OrchBuild`, `Get-OrchBuildMax` | `build add`, `build max-per-job` |
| `Add-OrchUsage`, `Get-OrchUsage` | `usage add`, `usage list` |
| `Sync-OrchPlans`, `Get-OrchPlans`, `Get-OrchPackages`, `Set-OrchPlanStatus`, `Set-OrchPackage` | `plans sync` / `list` / `set-status`, `packages list` / `set` |
| `Get-OrchQueue`, `Set-OrchQueue` | `queue list`, `queue upsert` |
| `Get-OrchQuestions`, `Add-OrchQuestion`, `Get-OrchDecisions`, `Add-OrchDecision` | `questions`, `ask`, `decisions list`, `decisions add` |

## What stays a file

`STOP`, `HANDOFF`, the loop lock, `idle.pid`, the pane slot counters, the raw files of a run (`prompt.md`, `rules.md`, `milestones.txt`, `events.jsonl`, `stderr.txt`, `final.txt`, `build-samples.txt`), `window.txt` and the usage cache folder.
The loop state, the event hook state and the Claude usage windows are stored as JSON text with `kv` (suggested keys: `loop-state`, `event-sync`, `usage-claude`).
