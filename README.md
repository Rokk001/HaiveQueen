# agent-orchestrator

A small orchestrator for AI coding agents on Windows. It reads the plans of a project, starts one fresh agent per task in its own
Windows Terminal pane, closes the agent when its task is done and starts the next one. No copying of prompts, no manual `/clear`.

What it does:

- Picks every startable package of every plan whose second line says `Status: READY` or `Status: IN_PROGRESS`.
- Starts **one agent per task** (Claude Code or Codex CLI, headless) in its own pane: `work`, then a separate `verify` agent that must answer
  the completeness question with exactly `Offene Anforderungen: 0`, then a `release` agent, then the `integrator`.
- Decides all routine steps by script, without a language model. A "brain" (one fresh `claude -p` call) is only asked when an event needs
  judgement: a new plan, a verify answer with open items, a failed agent, a question of the owner. A quiet round costs no tokens.
- Keeps every agent below a context limit (default 300k tokens): the agent is asked to write a handoff, ends, and a fresh agent continues.
- Effort classes (`light` / `medium` / `heavy`) per package, bundling of small packages, escalation after repeated failures.
- A dashboard pane with agents, usage limits, token usage, RAM and the plan counts.
- An optional `onEvent` hook that calls a command of your project (for example a git sync of your plan files) once per round with the events of that round.

## Requirements

- Windows 10/11 with [PowerShell 7](https://learn.microsoft.com/powershell/) (`pwsh`)
- [Windows Terminal](https://aka.ms/terminal) (`wt.exe`)
- [Claude Code](https://claude.com/claude-code) CLI (`claude`) and/or the Codex CLI (`codex`)
- Optional: Python 3.14 (module `compression.zstd`) for the usage table of compressed Codex logs; `git` for the sync example

## Setup

1. Clone this repository anywhere. The folder of the scripts is the tools folder.
2. Create a Windows Terminal profile for the orchestrator window, for example in `settings.json`:

   ```json
   { "name": "Orchestrator Agent", "commandline": "pwsh.exe", "closeOnExit": "graceful", "font": { "size": 10 } }
   ```

3. Copy `examples/project.example.json` and `examples/project-rules.example.md` next to your project (any folder, for example a private
   repository with your plans) and adapt them. The project file is the only place with project-specific values.
4. Start it: `pwsh -File start.ps1 -Project <path to your project json>`.

## Project file

`examples/project.example.json` shows every key. Paths are Windows paths; `{repo}`, `{tools}`, `{home}` and `{projectDir}` are replaced
where a path or command is read from the file.

| Key | Meaning |
|---|---|
| `window`, `terminalProfile.name`, `terminalProfile.fontSize` | Name of the terminal window, its profile and font size |
| `mutexPrefix` | Prefix of the global mutex names (allows several orchestrators on one PC) |
| `owner`, `openMarker` | Name of the person who answers questions; the heading `<openMarker>` in a state file opens a question |
| `repo`, `runtime` | Repository of the project and the runtime folder (agent list, logs, run folders; never commit it) |
| `claudeSlots`, `codexSlots`, `integratorSlots`, `minFreeRamGB`, `roundMinutes` | Concurrency, RAM limit and round interval |
| `classes`, `minClass`, `escalateAfter`, `bundle` | Model and effort per class and agent type, minimum class per phase, escalation and bundling |
| `claudeArgs`, `codexArgs`, `brainArgs`, `claudeTools` | Command-line templates of the CLIs |
| `plansRoot`, `planFileRegexes`, `planStemRegexes`, `planStandaloneRegex`, `planSuffixRegex`, `planExcludeNameRegex`, `planExcludeDirRegex`, `stateToken` | Where the plans are and how plan files and their state files are named |
| `integrationStateFile`, `questionsFile`, `answersFile` | Integration queue (below the plans root), questions of agents, answers of the owner |
| `integratorInstructions`, `compileCheck`, `buildFlowDoc`, `integrationBranch`, `worktreePrefix` | Names that appear in the agent prompts |
| `ruleSources`, `phaseRules` | Rule files and, per phase, the markdown sections that go into the system prompt of that phase |
| `prompts` | Prompt templates with `{placeholders}` for the follow-up tasks |
| `onEvent` | Command that is called once per round with the events of that round (see below) |

The project rules file (`ruleSources.project`) holds the project's own hard rules under `## 1. Hard rules (project)`. The tool rules in
`ORCHESTRATOR.md` and the project rules together form section 1 of the rules every agent gets; the brain gets both files.

## Plans

A plan is a markdown file. Line 2 is `Status: DRAFT | READY | IN_PROGRESS | DONE | PAUSED` (no status line counts as DRAFT). A table whose first column is
`Package` lists the packages with the columns `Content`, `Depends on` and optionally `Class`. State files (`<plan>-<stateToken>.md`) hold the
results of the agents. Only `READY` and `IN_PROGRESS` plans are started.

## Start, stop, dashboard

- Start: `pwsh -File start.ps1 -Project <project json>`. It opens the terminal window: the orchestrator log (top left), the dashboard (below it)
  and one pane per running agent on the right. A second start for the same project refuses to run.
- Stop: `pwsh -File stop.ps1 -Project <project json>` creates the `STOP` file in the runtime folder. Running agents finish their task, no new agent starts, the loop ends.
- The dashboard shows per agent: name, type, model, plan, package, phase, status, runtime and tokens. It also shows the usage limits, token usage,
  free RAM, running builds, plan counts per status and open questions.

## onEvent hook

If the project file has `onEvent.command`, the orchestrator collects the events of a round (plan status changed, work finished, verify passed, release finished,
queue row ready or integrated, question asked or answered) and calls the command once at the end of the round with the events as arguments.
No events means no call. If the command fails, the failure is written to `ORCH-EVENTS.log` and the same events are sent again in the next round;
the loop never stops. `examples/sync.example.ps1` commits and pushes a repository on every call.

## Tests

`tests/*.ps1` use their own throw-away project (built from the examples), a temporary runtime folder and a stub for `wt.exe`. They never touch a real project:

```
pwsh -File tests/test-s7.ps1     # routine transitions, brain triggers, context limit, finished packages, questions
pwsh -File tests/test-events.ps1 # onEvent hook and the sync example (local bare repository)
pwsh -File tests/test-s10.ps1    # effort classes, bundling, escalation, lock
pwsh -File tests/test-s9.ps1     # rule files per phase
pwsh -File tests/test-restart.ps1
pwsh -File tests/test-panes.ps1
pwsh -File tests/usage-test.ps1
```

## License

MIT, see `LICENSE`.
