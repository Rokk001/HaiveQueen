<p align="center">
  <img src="assets/haivequeen.png" alt="HaiveQueen" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Rokk001/HaiveQueen/actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Rokk001/HaiveQueen/test.yml?branch=main&style=for-the-badge&label=CI"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?style=for-the-badge">
  <img alt="Windows and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078D4?style=for-the-badge">
</p>

<p align="center"><b>Run many Claude Code and Codex agents in parallel, one fresh agent per task, without copying a single prompt.</b></p>

---

## Why

Working through a big plan with AI coding agents means a lot of babysitting: paste the prompt, wait, `/clear`, paste the next one, check the result, repeat.
With several agents at once it gets worse, and a supervising model that just watches burns tokens while it waits.

**HaiveQueen** removes that loop. It reads your plans, builds the prompt for every task itself, starts a fresh agent for it and checks the result with a second, independent agent.
A language model is only asked when something needs judgement; everything routine is plain Python.

## Features

- **Multi-project**: one loop serves many projects; a free slot goes to the project with the highest `priority`, ties to the oldest waiting package.
- **Providers with weekly-limit balancing**: Claude Code, Codex CLI or any command you list; the scheduler picks the provider with the most weekly limit left.
- **Local LLMs**: a provider entry can point at an OpenAI-compatible endpoint (for example LM Studio).
- **One fresh agent per task**: own headless agent, own pane; work, then an independent verify, then release, then the integrator.
- **Triage**: when a package is locked after repeated open points, a triage agent decides technical points itself and asks you only about game or design decisions.
- **Pause and drain**: `P` pauses or resumes new starts, `D` lets running agents finish; a restart re-attaches to agents that are still alive.
- **Build jobs**: parallel build jobs are chosen per machine from the cores and the free RAM, and measured per job.
- **SQLite state**: one database holds agents, events, questions, decisions and the integration queue; Markdown views can be exported.
- **Dashboard**: agents, usage limits, token use, RAM, builds, plans and open questions in one pane.
- **Windows Terminal or tmux**: panes are opened with `wt` on Windows and `tmux` on Linux; a headless backend runs the tests.

## How it works

```mermaid
flowchart LR
    PL[Plan<br/>READY] --> W[Work agent]
    W --> V[Verify agent<br/>open requirements: 0]
    V -->|open items| W
    V --> R[Release agent<br/>merge, compile check, push]
    R --> I[Integrator<br/>build, checks, deploy]
    DB[(SQLite<br/>database)]
    W <--> DB
    V <--> DB
    R <--> DB
    I <--> DB
    LOOP([Queen<br/>round loop, brain on events]) <--> DB
```

1. The **Queen** is the main instance: the round loop with its routine and the brain. Every round she syncs the plans of all projects into the database and applies the fixed transitions (work done, then verify, then release, then integrate).
2. Only if something needs judgement does it become an **event**, and the brain (one fresh `claude -p` call) is asked once. Its answer is a validated JSON action.
3. The agents she starts carry short female names (Ada, Bea, Cleo, ...), so the panes are easy to tell apart. A started agent writes its result to the plan's state, reports with `bzz done` / `bzz ready` / `bzz ask` and ends. The next round sees the new state.

## Dashboard

Rendered by the tool itself from a throw-away runtime with invented data (the script `scripts/make_dashboard_svg.py` converts the ANSI output to SVG, no screen capture).

<p align="center"><img src="assets/dashboard.svg" alt="Dashboard with agents, limits, usage, system, plans and questions"></p>

## Quick start

**Requirements**

- Python 3.11 or newer
- [Claude Code](https://claude.com/claude-code) CLI (`claude`) and/or the Codex CLI (`codex`)
- [Windows Terminal](https://aka.ms/terminal) (`wt`) on Windows, or `tmux` on Linux

**Steps**

```bash
git clone https://github.com/Rokk001/HaiveQueen
cd HaiveQueen
pip install -e .                       # optional: gives you the command `bzz` (else use `python -m haivequeen`)

bzz init /path/to/my-project          # creates .haivequeen/project.toml, AGENTS.md and an example plan
bzz register /path/to/my-project      # adds the project to config.toml and the database
bzz start                             # opens the HaiveQueen window and the dashboard
bzz stop                              # no new agents; running agents finish their task
```

Put your machine settings (providers, slots) into `config.toml` (see `examples/config.example.toml`; folder: `%APPDATA%\HaiveQueen` on Windows, `~/.config/HaiveQueen` on Linux).
The Queen, the dashboard and every agent share one terminal window; its name is the machine setting `window` (default `HaiveQueen`), a project cannot set its own.

## Plans

A plan is a Markdown file with a TOML header (`examples/plan.example.md`). Only a plan with `status = "READY"` or `"IN_PROGRESS"` starts agents.

```toml
+++
status = "READY"

[[packages]]
id = "P1"
title = "Add the parser"
class = "medium"        # light | medium | heavy: model and effort come from the provider
agent = "claude"        # claude, codex or any provider name
depends_on = []

[[packages]]
id = "P2"
title = "Draw the icons"
capability = "images"   # only providers with this capability may run it
depends_on = ["P1"]
+++
# My plan
Free Markdown below the header: the agents read it.
```

## Configuration

`config.toml` (machine): `slots`, `buildJobs` (0 = automatic), `ramReserveMb`, `agentStallSeconds` (an agent that prints nothing for this long is only marked as stalled in its row, an event, the dashboard and `bzz status`, never ended or failed; default 3600, 0 = off), `[[providers]]` (name, protocol, command, args, slots, use = preferred | fallback | capability-only, classes, limit) and `[[projects]]`.

`.haivequeen/project.toml` (per project): `name`, `colour`, `priority`, `planGlobs`, `planExclude` (globs removed from the planGlobs matches), `[views]` and the branch and build settings. See `examples/`.

## Inbox

Every project has an inbox folder: `<workspace>/.haivequeen/inbox/`. Anyone (the owner, the coordinator, a script) can leave answers and decisions there as TOML files, and the Queen takes them over at the start of the next round. This works for every project and needs no command.

- Only files that end in `.toml` (UTF-8) are read, oldest first. Write a file as `<name>.toml.tmp` and rename it when it is complete; other endings are ignored.
- A file holds one or more `[[answer]]` tables (`question_id`, `text`, `by` = `owner` or `coordinator`) and `[[decision]]` tables (`text`, `source` = `owner` or `coordinator`, optional `plan` and `item`). See `examples/inbox.example.toml`.
- A file may also hold `[[queue]]` tables, the orders to the integration queue: `branch`, `action` (`withdraw`, `mark_cleanup` or `requeue`), `reason` and an optional `tip` (full or abbreviated commit id; without it every row of the branch is meant). `withdraw` sets the ready or blocked row(s) of the branch to `withdrawn` (the note keeps the old status word and the reason), but only when the tip is contained in `origin/<integrationBranch>` (checked with `git merge-base --is-ancestor`) or the reason is exactly `ueberholt`; otherwise the whole file moves to `inbox/failed/`. `mark_cleanup` stores the mark `erledigt, Aufraeumen erlaubt: <reason>` for the branch (kv key `branch-cleanup:<branch>`); no row changes and nothing is deleted. `requeue` sets the newest ready or blocked row of the branch back to `ready` for the next integration run. A row that is in a run or already integrated is never touched.
- The values are stored through the same functions as `bzz answer` and `bzz decisions add`: same checks, same events, and a package that was locked by the question is released in the same round.
- A file is taken over completely or not at all. It then moves to `inbox/done/<YYYYMMDD-HHMMSS>-<name>.toml`. An invalid file (parse error, unknown or already answered question, wrong value) moves to `inbox/failed/` with `<name>.error.txt` next to it that names the reason and the line, and a warning event is written.

## Command line

| Command | Who | What it does |
|---|---|---|
| `bzz start` / `bzz stop` | owner | start the loop (`--terminal auto/wt/tmux/headless`, `--rounds N`), or stop starting new agents |
| `bzz pause` / `bzz resume` / `bzz drain` | owner | pause new starts, resume, or let the running agents finish |
| `bzz status [--project p] [--json]` | owner | "X / Y integrated (%)" and a task / agent / status table per project |
| `bzz questions [--open]`, `bzz answer <id> "<text>" --by owner` | owner | read and answer questions of the agents |
| `bzz queue [upsert]` | owner, integrator | the integration queue |
| `bzz decisions list [--all]`, `bzz decisions update <id> --status active\|rule\|superseded` | owner | decisions; the list shows only `active` ones unless `--all` |
| `bzz init <folder>`, `bzz register <folder>` | owner | create and register a project |
| `bzz export`, `bzz views --project p` | owner | export the state, write the Markdown views |
| `bzz import-legacy ...` | owner | import the state of version 1 (see `docs/MIGRATION-v1.md`) |
| `bzz triage ...` | triage agent | list, decide or ask about the open points of a locked package |
| `bzz done`, `bzz ready`, `bzz ask`, `bzz note` | agents | final report, branch ready for integration, question, note (`docs/RUNTIME-API.md`) |

## FAQ

**Does HaiveQueen call a model all the time?** No. A model is called only on events (new plan, open items, failure, question) and for triage.

**Which agents can I use?** Claude Code and Codex CLI out of the box; any other command through the `providers` list.

**Where does the state live?** In one SQLite database in the data folder. It must be on a local disk, never on a network drive (SQLite WAL needs it; the tool refuses to open it there). Export it with `bzz export`; Markdown views are generated read-only.

**Can I stop it safely?** Yes. `bzz stop` starts no new agent and waits; after a crash the next start re-attaches to agents that are still alive.

**I used version 1 (PowerShell).** See `docs/MIGRATION-v1.md`.

## License

MIT, see [LICENSE](LICENSE).
