> Historical interface snapshot: phase and brain references below describe the former implementation, not the current Hive runtime.

# Interface between the loop (S13) and the agent process (S14)

The loop starts every agent as a hidden background process (`loop.launch_headless`, its stdout and stderr go to `runner.log` in the run folder). The process runs

    python -c "<bootstrap>" <tools folder> --project P --name N --provider PR --protocol claude|codex --plan PLAN --package PKG --phase PHASE --cwd DIR --prompt-file FILE [--env NAME=VALUE ...]

The bootstrap puts the tools folder on `sys.path` and calls `haivequeen.agent_runner.main(argv)` with everything after the tools folder, so it works from any
folder (the same as `python -m haivequeen.agent_runner ...` run from the tools folder). `main(argv=None) -> int` should parse the arguments with argparse.
`loop.runner_argv(env, spec)` builds this command line; tests replace it.

| Argument | Meaning |
|---|---|
| `--project`, `--name` | project name and agent name; the active agent row is (project, name) |
| `--provider`, `--protocol` | provider entry name and its protocol (claude or codex) |
| `--plan`, `--package`, `--phase` | plan path (relative to the workspace), package id (`ALL`, `REL`, `INT-...`, `Q-...`, `FIX-...` possible), phase work, verify, release, integrate or triage |
| `--cwd`, `--prompt-file` | working folder; prompt file (UTF-8) in the run folder |
| `--env NAME=VALUE` | repeatable; set in `os.environ` first (test and folder overrides: BZZ_DATA_DIR, BZZ_CONFIG_DIR, BZZ_LOG_DIR, BZZ_RUNTIME_OVERRIDE, BZZ_HOST, BZZ_FAKE_AGENT, BZZ_SAMPLE_SEC) |

What the loop has already written into the agent row before the process starts (read model, effort, command and args from the provider entry of the settings):
`status` starting, `type` (protocol), `provider`, `plan_path`, `package`, `phase`, `cwd`, `class`, `model`, `effort`, `modelShort`, `person`, `color`, `task`,
`bundle_json`, `started_at`, `last_line` queued.

What the runner must do for the loop:
- Set `pid` (its own process id) and `status` running in the row at once: `routine.repair_stale_agents` marks rows without a pid after 2 minutes as `pane never started (no runner process)`.
- Keep `ctxTokens` (current context size) in the row: the routine writes the `HANDOFF` flag file at `contextLimitTokens` (default 300000).
- Run folder: `scheduler.ProjectEnv.run_dir(name)` = `<data>/runs/<project>/<name>/` with `prompt.md` (written by the loop), `final.txt` (the final answer; the routine
  reads the LAST line `Open requirements: N`), `HANDOFF`, `milestones.txt` (the live text the dashboard shows), `runner.log`, `events.jsonl`, `stderr.txt`, `build-samples.txt`.
- End with status `done` or `failed`, `ended_at`, `exit_code`, `tokens_in`, `tokens_out`, `last_line`.
- Build jobs for phases integrate and release: `buildjobs.plan_build(env)` gives `{Jobs, Why, EnvVar, FreeMB}` (set the env var, start the sampler
  `python -m haivequeen.buildjobs sample --parent-pid N --out build-samples.txt`), at the end `buildjobs.complete_measure(env, name, jobs, start_free_mb, sample_file, started)`.
- Claude rate-limit events: `scheduler.save_claude_usage(conn, event)` (global kv key `usage-claude`; the weekly-limit selection reads it).

Provided by S13 for S14, S15 and S16: `scheduler.model_label/model_short/model_long/format_tokens/class_model/claude_usage/codex_usage`,
`buildjobs.jobs_line(env, agents)` (dashboard text), `loop.read_state(conn)` (loop-state: status brain, round or idle, since, nextAt), `brain.totals(conn)`,
`runstate` (S6b), `loop.stop_flag()`. Provided by S14 (`agent_runner.py`): phase rule files (`write_phase_rules`), `Update-AgentRetry` (`AgentRun.set_agent_retry`), milestones.

The dashboard is the graphical window `haivequeen.gui`, which `bzz start` opens once.


---

## Runner side (S14)


The scheduler writes the agent row, then starts the agent runner as a hidden background process. The runner needs the tool folder on
`PYTHONPATH` (or as working directory), because `-m haivequeen...` does not work with `python -I`.

## Command line

```
python -m haivequeen.agent_runner --project P --name N --provider NAME --cwd DIR --prompt-file FILE
       [--plan PATH] [--package ID] [--phase work|verify|release|integrate] [--runs-dir DIR] [--model M] [--effort E]
```

| Option | Meaning |
|---|---|
| `--project` | project name (database key) |
| `--name` | agent id (the `name` of the agents row, also the run folder name) |
| `--provider` | provider entry of `config.toml` (`[[providers]]`: name, protocol claude or codex, command, args template, `[providers.classes.light/medium/heavy]` model and effort, optional endpoint); the protocol decides how the event stream is read |
| `--cwd` | working directory of the agent |
| `--prompt-file` | file the agent is told to read |
| `--plan`, `--package`, `--phase` | as in v1 (`work` default); shown in the task text |
| `--runs-dir` | folder that holds `<name>/` run folders; default `<data folder>/runs` |
| `--model`, `--effort` | optional override; default: class of the agent row, resolved through the provider entry |

The exit code of the runner is 0 (the result is the row status `done` or `failed`); a configuration error (unknown provider) ends it with an error.

## What the scheduler writes before the start (row in `agents`)

`class` (light/medium/heavy, default medium), `person`, `color`, optionally `modelShort` and `task` (the runner fills `modelShort` and `task` when they are missing), `status` `starting`.
Environment: `BZZ_DATA_DIR` / `BZZ_CONFIG_DIR` as for every haivequeen process.

## What the runner writes

- Row fields: `type` (protocol), `provider`, `model`, `effort`, `status` running -> done|failed, `pid`, `started_at`, `ended_at`, `exit_code`, `tokens_in`, `tokens_out` (NULL for Codex until the session log has numbers), free fields `ctxTokens`, `lastLine`, `buildJobs`, `buildUseMB`.
- Run folder `<runs-dir>/<name>/`: `events.jsonl` (raw stream), `stderr.txt`, `milestones.txt` (`colour<TAB>HH:MM text`), `final.txt` (final report), `rules.md` (Claude: rules of the phase), `build-samples.txt` (integrate and release), `HANDOFF` (flag file written once when the context reaches 300000 tokens; the scheduler's `Set-HandoffFlags` equivalent writes the same file).
- Events: `warning` (exit code after a finished task, row update failures), `build` (build start), `rounds` (build measurement line), all with `agent`.
- `kv` project `*`, key `usage-claude`: `{"saved", "fiveHour", "sevenDay"}` from `rate_limit_event`.
- `build_measurements`: one row per measured build (jobs, parallel value, free RAM at start and lowest, highest RAM sum of the build processes `peak_mb`, use per job, duration). Rows without `peak_mb` are old whole-machine values and are never used.

## Build jobs

The settings window shows one block "Build" with three fields: "CPU cores per build" (a choice from 1 to the logical cores, default two thirds rounded up), "Parallel builds" (the number after `--parallel` of the build command, default 1) and "Minimum free RAM before a build" (GB, default 2). They are stored in the `[buildJobs]` table of `config.toml` as `cores`, `parallel` and `minFreeRamGB`; saving the window removes the old keys `coreShare` and `fixed` and a plain number `buildJobs = N`. When read, the core count is `cores`, else `fixed`, else the number N (`buildJobs = 0` and no table mean the default), else `coreShare`; a count above the cores of the machine is the machine's core count.

Other keys of the table are settable in `config.toml` only: `envVar` (variable of the job count, default `BUILD_JOBS`; a configured name such as `BUILD_MP_JOBS` wins), `parallelEnvVar` (default `BUILD_PARALLEL`), `minFreeRamEnvVar` (default `BUILD_MIN_FREE_RAM_GB`), `reserveMB` (RAM that stays free during a build, default 1024), `perJobMB` (a number, or `auto` = the measured median), `firstJobs` (jobs without a valid measurement, default 2) and `measurements` (how many of the last measurements the median is taken from, default 10).

The job count is the smaller of the core limit and the RAM limit, at least 1. Core limit: the chosen cores, divided by the number of builds that run at the same time and by the parallel value N (floor, each target at least 1 job). RAM limit: (free RAM - `reserveMB`) / (median use per job x N). It is computed when `bzz integration acquire` (or the build start of an integrate or release agent) hands it out, with the free RAM of that moment, and it replaces a value set at the Queen start. Every agent that builds (integrate, release, Queen and Bee) gets the three values as environment variables and in its build instructions; they win over a `--parallel` value or RAM minimum in a project rule and no question is filed for the difference.

A build is handed out only while at least "Minimum free RAM before a build" is free: `bzz integration acquire` then answers "waiting for integration" without taking the place, the Queen is marked `integration_wait` and asked again in the next round (also after a restart); the start of an integrate or release agent waits the same way. `ramReserveMb` (minimum free RAM for starting Queens and Bees) is a different setting.

The runner samples every 10 s (`BZZ_SAMPLE_SEC`) the processes named in `buildProcessNames` of project.toml that the agent itself started (its process tree) and their RAM. The use per job is the highest RAM sum of these processes divided by the jobs the build got and N; a build in which no such process was seen stores nothing. The count uses the median of the last `measurements` valid values of this machine and project. The dashboard shows one build line: the running job count during a build, else the count `acquire` would hand out now, and the median per job of each project with `buildProcessNames`.

## Phase rules

Queen and Bee (profile.py): both load the complete project rules, never a selection: the AGENTS.md and CLAUDE.md checked in to the project repository (`codeRepo`, else the workspace) plus those of the repository named by the project setting `upstreamRepo` (none without it); the README, internal workspace files and anything else are never rules, and a file the provider loads itself from its working folder is not copied again. A Queen also loads her role file, the plan, its state file, the handoff of her predecessor and the decisions and owner answers of her Apiary; a Bee its role file and its task (`bzz bee start --rules` is ignored). Claude gets the rules in `rules.md`, other providers in the prompt file. Other phases: Claude agents get `rules.md` in their run folder (`{rules}` in the args template). With `phaseRules` / `ruleSources` in project.toml the listed sections are copied verbatim; without them the workspace `AGENTS.md` is used whole. Other template variables: `{model} {effort} {repo} {tools} {cwd} {claudeTools}` (`claudeTools` from project.toml or config.toml).
