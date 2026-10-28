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
