# Interface between the loop (S13) and the agent process (S14)

The loop starts every agent through the terminal interface (`orchlib.terminal`). The pane runs

    python -c "<bootstrap>" <tools folder> --project P --name N --provider PR --protocol claude|codex --plan PLAN --package PKG --phase PHASE --cwd DIR --prompt-file FILE [--env NAME=VALUE ...]

The bootstrap puts the tools folder on `sys.path` and calls `orchlib.agent_runner.main(argv)` with everything after the tools folder, so it works from any
folder (the same as `python -m orchlib.agent_runner ...` run from the tools folder). `main(argv=None) -> int` should parse the arguments with argparse.
`loop.runner_argv(env, spec)` builds this command line; tests replace it.

| Argument | Meaning |
|---|---|
| `--project`, `--name` | project name and agent name; the active agent row is (project, name) |
| `--provider`, `--protocol` | provider entry name and its protocol (claude or codex) |
| `--plan`, `--package`, `--phase` | plan path (relative to the workspace), package id (`ALL`, `REL`, `INT-...`, `Q-...`, `FIX-...` possible), phase work, verify, release, integrate or triage |
| `--cwd`, `--prompt-file` | working folder; prompt file (UTF-8) in the run folder |
| `--env NAME=VALUE` | repeatable; set in `os.environ` first (test and folder overrides: ORCH_DATA_DIR, ORCH_CONFIG_DIR, ORCH_LOG_DIR, ORCH_RUNTIME_OVERRIDE, ORCH_HOST, ORCH_FAKE_AGENT, ORCH_SAMPLE_SEC) |

What the loop has already written into the agent row before the pane opens (read model, effort, command and args from the provider entry of the settings):
`status` starting, `type` (protocol), `provider`, `plan_path`, `package`, `phase`, `cwd`, `class`, `model`, `effort`, `modelShort`, `person`, `color`, `task`,
`bundle_json`, `started_at`, `last_line` queued, `pane_id`, `paneCols`, `paneRows`.

What the runner must do for the loop:
- Set `pid` (its own process id) and `status` running in the row at once: the loop waits up to 40 s for the pid, launches the pane once more if it stays away, and
  `routine.repair_stale_agents` marks rows without a pid after 2 minutes as `pane never started (no runner process)`.
- Keep `ctxTokens` (current context size) in the row: the routine writes the `HANDOFF` flag file at `contextLimitTokens` (default 300000).
- Run folder: `scheduler.ProjectEnv.run_dir(name)` = `<data>/runs/<project>/<name>/` with `prompt.md` (written by the loop), `final.txt` (the final answer; the routine
  reads the LAST line `Offene Anforderungen: N`), `HANDOFF`, `milestones.txt`, `events.jsonl`, `stderr.txt`, `build-samples.txt`.
- End with status `done` or `failed`, `ended_at`, `exit_code`, `tokens_in`, `tokens_out`, `last_line`.
- Build jobs for phases integrate and release: `buildjobs.plan_build(env)` gives `{Jobs, Why, EnvVar, FreeMB}` (set the env var, start the sampler
  `python -m orchlib.buildjobs sample --parent-pid N --out build-samples.txt`), at the end `buildjobs.complete_measure(env, name, jobs, start_free_mb, sample_file, started)`.
- Claude rate-limit events: `scheduler.save_claude_usage(conn, event)` (global kv key `usage-claude`; the weekly-limit selection reads it).

Provided by S13 for S14, S15 and S16: `scheduler.model_label/model_short/model_long/format_tokens/class_model/claude_usage/codex_usage`,
`buildjobs.jobs_line(env, agents)` (dashboard text), `loop.read_state(conn)` (loop-state: status brain, round or idle, since, nextAt), `brain.totals(conn)`,
`runstate` (S6b), `loop.stop_flag()`. Not provided (S14): phase rule files (`Write-PhaseRules`), `Update-AgentRetry`, header, milestones.

The dashboard pane (S15) is opened by `orch start` when the module `orchlib.dashboard` exists, with the same bootstrap and `main(argv)`.


---

## Runner side (S14)


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
