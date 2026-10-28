# Interface between the scheduler (S13) and the agent runner (S14)

The scheduler writes the agent row, then opens a pane whose command is the agent runner. The runner needs the tool folder on
`PYTHONPATH` (or as working directory), because `-m orchlib...` does not work with `python -I`; the runner sets `PYTHONPATH` itself
for its own children (header process, wrapper child).

## Command line

```
python -m orchlib.agent_runner [--wrap] --project P --name N --provider NAME --cwd DIR --prompt-file FILE
       [--plan PATH] [--package ID] [--phase work|verify|release|integrate] [--runs-dir DIR] [--model M] [--effort E]
```

| Option | Meaning |
|---|---|
| `--project` | project name (database key) |
| `--name` | agent id (the `name` of the agents row, also the run folder name) |
| `--provider` | provider entry of `config.toml` (`[[providers]]`: name, protocol claude or codex, command, args template, `[providers.classes.light/medium/heavy]` model and effort, optional endpoint); the protocol decides how the event stream is read |
| `--cwd` | working directory of the agent |
| `--prompt-file` | file the agent is told to read |
| `--plan`, `--package`, `--phase` | as in agent-run.ps1 (`work` default); shown in the header task text |
| `--runs-dir` | folder that holds `<name>/` run folders; default `<data folder>/runs` |
| `--model`, `--effort` | optional override; default: class of the agent row, resolved through the provider entry |
| `--wrap` | pane wrapper (pane-wrap.ps1): runs the agent as a child, keeps the pane for `paneCloseDelaySeconds` (config.toml, default 30), sets `paneClosed`, always exits 0 |

The exit code of the runner is 0 (the result is the row status `done` or `failed`); a configuration error (unknown provider) ends it with an error and, in `--wrap` mode, still with exit code 0.

## What the scheduler writes before the start (row in `agents`)

`class` (light/medium/heavy, default medium), `person`, `color`, optionally `modelShort` and `task` (the runner fills `modelShort` and `task` when they are missing), `status` `starting`.
Environment: `ORCH_DATA_DIR` / `ORCH_CONFIG_DIR` as for every orchlib process. `ORCH_NO_HEADER=1` suppresses the header process (the runner also skips it when its stdout is not a terminal).

## What the runner writes

- Row fields: `type` (protocol), `provider`, `model`, `effort`, `status` running -> done|failed, `pid`, `started_at`, `ended_at`, `exit_code`, `tokens_in`, `tokens_out` (NULL for Codex until the session log has numbers), free fields `ctxTokens`, `lastLine`, `paneCols`, `paneRows`, `buildJobs`, `buildUseMB`, `paneClosed`.
- Run folder `<runs-dir>/<name>/`: `events.jsonl` (raw stream), `stderr.txt`, `milestones.txt` (`colour<TAB>HH:MM text`), `final.txt` (final report), `rules.md` (Claude: rules of the phase), `build-samples.txt` (integrate and release), `HANDOFF` (flag file written once when the context reaches 300000 tokens; the scheduler's `Set-HandoffFlags` equivalent writes the same file).
- Events: `warning` (exit code after a finished task, row update failures), `build` (build start), `rounds` (build measurement line), all with `agent`.
- `kv` project `*`, key `usage-claude`: `{"saved", "fiveHour", "sevenDay"}` from `rate_limit_event`.
- `build_measurements`: one row per integrate/release agent (jobs, free RAM at start and lowest, use per job, duration).

## Build jobs

`config.toml` `buildJobs`: a number above 0 is a fixed job count, 0 is automatic, a table `[buildJobs]` may set `envVar` (default `BUILD_JOBS`), `coreShare`, `reserveMB`, `perJobMB`, `firstJobs`, `fixed`. For phases `integrate` and `release` the runner sets the variable in the agent's environment, samples free RAM and compiler processes (`buildProcessNames` of project.toml) every 10 s (`ORCH_SAMPLE_SEC`) and stores the measurement at the end. The count logic is also in `orchlib/agent_runner.py` (`build_job_count`); it may be replaced by `orchlib.buildjobs` after the merge of S13.

## Phase rules

Claude agents get `rules.md` in their run folder (`{rules}` in the args template). With `phaseRules` / `ruleSources` in project.toml the listed sections are copied verbatim; without them the workspace `AGENTS.md` is used whole. Other template variables: `{model} {effort} {repo} {tools} {cwd} {claudeTools}` (`claudeTools` from project.toml or config.toml).
