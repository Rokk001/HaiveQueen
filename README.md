<p align="center">
  <img src="assets/haivequeen.png" alt="HaiveQueen" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Rokk001/HaiveQueen/actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Rokk001/HaiveQueen/test.yml?branch=main&style=for-the-badge&label=CI"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?style=for-the-badge">
  <img alt="Windows and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078D4?style=for-the-badge">
</p>

<p align="center"><b>Run plans with Queens and parallel Bees without copying prompts.</b></p>

---

## Why

HaiveQueen runs plans with one Hive per plan. The Beekeeper is a program loop, not a model; each Hive has one Queen, and the Queen works the plan and starts Bees for independent work. Token economy is the first quality goal.

## Features

- **Hives and Queens**: one Queen per plan, in her own worktree and as a hidden background process; a Hive keeps its provider and colour across Queen changes.
- **Bees**: slim package tasks with only needed paths, numbered steps and rules; independent packages run in parallel up to the configured limit. Code-changing Bees use sub-branches and worktrees which the Queen merges locally.
- **Providers and limits**: select a provider by free weekly limit, with machine-wide preference and exclusions. A Hive rests until reset when its provider is limited; only the owner may change its provider.
- **Escalation**: package classes bind the work. Failed attempts persist per class; after the configured threshold the Queen selects the next offered class. Interruptions never count as failures. Image packages stay on Codex; after its highest class fails the owner is asked.
- **Completeness and integration**: the Queen records evidence for every requirement, continues while any remain open and integrates her own branch. Only one integration per project runs at a time.
- **Handoffs**: at the context limit no new Bees start; running Bees finish and a new Queen continues from the handoff. Open owner questions do not stop independent work.
- **Display**: one graphical window shows the Hives per Apiary, the busy Bees, limits, questions, failed rounds and the live text of every Queen and Bee (the Beekeeper loop has no window of its own).
- **Pause and stop**: pause prevents new Queens and separate Bee processes until you resume; stop lets the running agents finish and then ends HaiveQueen. Existing subagents continue.
- **SQLite**: state survives restarts; every agent runs as a hidden background process and writes its live text to a file in its run folder.

## How it works

1. The Beekeeper reads READY and IN_PROGRESS plans, chooses an available provider offering the Queen class and starts one Hive per plan within the machine-wide limits. Existing Hives ready to resume take precedence over new plans.
2. The Queen works a startable package herself and delegates independent packages to Bees, respecting dependencies, classes and the Bee limit. A higher-class package runs as a Bee even when it is not parallel. Image work goes to Codex; local providers are used when available. When neither the Hive provider nor a local provider offers the required or a higher class for a normal package, another configured provider can be used.
3. Before integration the Queen answers the completeness question with evidence per requirement and `Offene Anforderungen: 0`. She merges the current integration branch into her branch, runs the project check, registers her branch and performs the project's integration steps under the per-project integration place. A waiting Hive hands off and resumes when its turn arrives.
4. At a provider limit the Hive rests on that provider until reset. The owner may explicitly dequeen or requeen it. A Queen awaiting an answer hands off once independent work is finished; any answer can resume the Hive.

## Dashboard

Words: an **Apiary** is a project, a **Hive** is one plan, the **Queen** is the main agent of a Hive, a **Bee** is a helper agent of a Queen, the **Beekeeper** is the managing loop.

`bzz start` starts the Beekeeper loop as a hidden background process (its output goes to `beekeeper.log` in the data folder) and opens the graphical dashboard once; there is no terminal window and no terminal dashboard. Every Queen and Bee is a hidden background process too. `bzz dequeen` and `bzz requeen` stay available.

### Graphical dashboard

The dashboard is a graphical interface written in pure Python (tkinter, no extra packages). One window shows Apiaries, Hives, Queens, Bees, token use, the live log, system load and the integration queue, laid out like `docs/HaiveQueen_Dashboard.png` (logo and icons are cut out of that picture by `scripts/make_gui_assets.py` into `assets/gui/`). Context and token use are shown per Queen, Bee, Hive and Apiary; the model limits are progress bars; every list that does not fit has a scroll bar (or use the mouse wheel). Limits, plans, questions and round errors are in the status panel. `bzz gui` opens only the window (`--detach` returns at once, `--project <name>` shows one Apiary). The one state button at the bottom shows RUNNING, PAUSED, STOPPING or STOPPED and opens a menu with the actions that fit (pause or resume, stop, start). A click on the repository name in the header opens the GitHub page. Keys: `P` pause/resume, `M` (or a right click on a Hive) Queen menu with restart or end of that Hive's Queen, `F` next Apiary, `Up`/`Down` select a Hive, `X` stop the selected Bee, `C` clear, `S` save and `A` auto-scroll the log; every entry of the `bzz` bar at the bottom is clickable. Pause, resume and stop ask for a confirmation first.

**Settings**: the `config` entry of the bottom bar (or `bzz` bar) opens the settings editor. It shows `config.toml` and the `project.toml` of every Apiary as forms: General, one page per provider (with the Claude allowed tools as their own list, one tool per line, and the other start arguments as a second list) and one per project. Save checks the settings the same way the loop does when it reloads them, keeps the old file as `<name>.bak` and keeps the comment lines at the top of the file; the running HaiveQueen applies the change without a restart. Comments inside the file are not kept.

**Plans**: a plan can be paused and resumed in the window (`Space` on the selected Hive or the Hive menu): this sets the status in the plan file to PAUSED or READY. A paused plan gets no new Queen or Bee; a running Queen keeps working.

**Questions**: the `questions` entry of the window (key `Q`) lists the open owner questions; answer them there instead of writing an inbox file (inbox files still work). For an owner check, tick the steps you do not accept: they become rejected and the Queen reworks exactly those.

**Live output**: `Enter` or a double click on a Hive row opens a read-only window with the last 300 lines of the live text of the running Queen of that Hive (the latest one when none runs); on a Bee row it shows that Bee. The window reloads every 2 seconds and follows the end of the text; opening the same agent again focuses its window. The text is the file `milestones.txt` in the run folder of the agent (`<data>/runs/<project>/<agent>/`), written by the agent runner; `runner.log` next to it holds the plain runner output.

## Quick start

**Requirements**

- Python 3.11 or newer
- [Claude Code](https://claude.com/claude-code) CLI (`claude`) and/or the Codex CLI (`codex`)
- Python with tkinter (part of the standard Python installers) for the dashboard window

**Steps**

```bash
git clone https://github.com/Rokk001/HaiveQueen
cd HaiveQueen
pip install -e .                       # optional: gives you the command `bzz` (else use `python -m haivequeen`)

bzz init /path/to/my-project          # creates .haivequeen/project.toml, AGENTS.md and an example plan
bzz register /path/to/my-project      # adds the project to config.toml and the database
bzz start                             # starts the loop in the background and opens the dashboard window
bzz stop                              # no new agents; running agents finish their task
```

Put your machine settings (providers, slots) into `config.toml` (see `examples/config.example.toml`; folder: `%APPDATA%\HaiveQueen` on Windows, `~/.config/HaiveQueen` on Linux).
The loop and all agents run as hidden background processes; the dashboard window is the only interface.
Closing the dashboard window leaves the managing loop running. `bzz start` opens the window again without restarting running agents. A start during a pending stop reports that the existing agents must finish first. Windows desktop shortcuts can use `bzz start --desktop` and `bzz stop --desktop` for a visible result or error dialog; ordinary CLI calls keep their JSON output.

Completed idle Hives are reconciled with the plan status and confirmed integration. Active agents and genuine owner questions are preserved. A Hive without a Queen shows its recorded start blocker or pending question numbers instead of a generic waiting label.
Automatic worktree cleanup requires confirmation that the branch's current commit was integrated; missing or superseded confirmation preserves the worktree. Command output labels executed check scripts as checks, while reading or searching their filenames does not count as a check. Round totals count actual Queen launches independently of previous run timestamps.

## How HaiveQueen develops itself

A Queen works in her plan worktree, checks completeness, merges the integration branch and runs the project check. She registers and integrates only her own ready row, runs the project integration steps and starts self-update. The Beekeeper serializes integrations per project. External windows acquire the same integration place before integrating.

## Plans

A plan is a Markdown file with a TOML header (`examples/plan.example.md`). Only a plan with `status = "READY"` or `"IN_PROGRESS"` starts agents.

```toml
+++
status = "READY"

[[packages]]
id = "P1"
title = "Add the parser"
class = "medium"        # light | medium | heavy: model and effort come from the provider
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

Machine `config.toml` holds `hives` (default 2), `preferredProvider`, `excludedProviders`, provider `slots`, provider `classes`, and the machine resource settings. `queenClass` (default `medium`) and `beesPerQueen` (default 3) may be set in the machine config or overridden in a project. `escalateAfter` defaults to 3, and `contextLimitTokens` defaults to 300000: set it once in the machine config for all Apiaries; a project may override it, and a model entry under `[providers.classes]` may carry its own `contextLimitTokens` that wins for the Queens and Bees running with that model. Project settings override machine settings except machine-wide settings. See `examples/config.example.toml` and `examples/project.example.toml`.

Claude subscription quotas are refreshed at most once per minute with the local CLI `/usage` metadata command, without model turns, hooks, MCP servers or saved sessions. The dashboard and scheduling share the verified account windows; a failed refresh hides the old percentage and pauses automatic Claude selection until a successful refresh.

Saved changes to machine `config.toml` and registered projects' `.haivequeen/project.toml` are reloaded at the next controller poll, including while idle, without restarting running agents. Valid changes wake scheduling immediately; invalid or temporarily missing files retain the complete last valid settings. An explicit `--round-minutes` still takes precedence.

## Inbox

Every project has an inbox folder: `<workspace>/.haivequeen/inbox/`. Anyone (the owner, the coordinator, a script) can leave answers and decisions there as TOML files, and the Beekeeper takes them over at the start of the next round. This works for every project and needs no command.

- Only files that end in `.toml` (UTF-8) are read, oldest first. Write a file as `<name>.toml.tmp` and rename it when it is complete; other endings are ignored.
- A file holds one or more `[[answer]]` tables (`question_id`, `text`, `by` = `owner` or `coordinator`) and `[[decision]]` tables (`text`, `source` = `owner` or `coordinator`, optional `plan` and `item`). See `examples/inbox.example.toml`.
- A file may also hold `[[queue]]` tables, the orders to the integration queue: `branch`, `action` (`withdraw`, `mark_cleanup` or `requeue`), `reason` and an optional `tip` (full or abbreviated commit id; without it every row of the branch is meant). `withdraw` sets the ready or blocked row(s) of the branch to `withdrawn` (the note keeps the old status word and the reason), but only when the tip is contained in `origin/<integrationBranch>` (checked with `git merge-base --is-ancestor`) or the reason is exactly `ueberholt`; otherwise the whole file moves to `inbox/failed/`. `mark_cleanup` stores the mark `erledigt, Aufraeumen erlaubt: <reason>` for the branch (kv key `branch-cleanup:<branch>`); no row changes and nothing is deleted. `requeue` sets the newest ready or blocked row of the branch back to `ready` for the next integration run. A row that is in a run or already integrated is never touched.
- The values are stored through the same functions as `bzz answer` and `bzz decisions add`: same checks, same events, and a Hive waiting for an answer can resume.
- A file is taken over completely or not at all. It then moves to `inbox/done/<YYYYMMDD-HHMMSS>-<name>.toml`. An invalid file (parse error, unknown or already answered question, wrong value) moves to `inbox/failed/` with `<name>.error.txt` next to it that names the reason and the line, and a warning event is written.

## Command line

| Command | Who | What it does |
|---|---|---|
| `bzz start` / `bzz stop` | owner | start the loop in the background and open the dashboard window (`--foreground`, `--rounds N`, `--round-minutes M`), or stop starting new agents |
| `bzz gui [--detach] [--project p]` | owner | open the graphical dashboard window |
| `bzz pause` / `bzz resume` | owner | pause new starts, or resume them |
| `bzz status [--project p] [--json]` | owner | "X / Y integrated (%)" and a task / agent / status table per project |
| `bzz questions [--open]`, `bzz answer <id> "<text>" --by owner` | owner | read and answer questions of the agents |
| `bzz queue [upsert]` | owner, Queen | the integration queue |
| `bzz decisions list [--all]`, `bzz decisions update <id> --status active\|rule\|superseded` | owner | decisions; the list shows only `active` ones unless `--all` |
| `bzz init <folder>`, `bzz register <folder>` | owner | create and register a project |
| `bzz export`, `bzz views --project p` | owner | export the state, write the Markdown views |
| `bzz import-legacy ...` | owner | import the state of version 1 (see `docs/MIGRATION-v1.md`) |
| `bzz dequeen <hive>` / `bzz requeen <hive> [provider]` | owner | leave a Hive queenless or start a new Queen, optionally on a different provider |
| `bzz bee start`, `bzz bee result`, `bzz bee wait` | Queen | start a Bee, record an attempt or wait for its provider |
| `bzz package --project p --plan f --package id --state working\|implemented\|done\|rejected` | Queen | report the progress of a package of her plan; the window's progress bar counts the done ones; a rejected package (the owner did not accept it) is not done and `bzz ready` is refused until it is reworked |
| `bzz integration acquire` / `release` | Queen or external window | acquire or release the per-project integration place; external windows provide a stable owner process ID |
| `bzz done`, `bzz ready`, `bzz ask`, `bzz note` | agents | final report, branch ready for integration, question, note (`docs/RUNTIME-API.md`) |

## FAQ

**Does HaiveQueen call a model all the time?** No. The Beekeeper runs without a model; Queens and their Bees use models for plan work.

**Which agents can I use?** Claude Code and Codex CLI out of the box; any other command through the `providers` list.

**Where does the state live?** In one SQLite database in the data folder. It must be on a local disk, never on a network drive (SQLite WAL needs it; the tool refuses to open it there). Export it with `bzz export`; Markdown views are generated read-only.

**Can I stop it safely?** Yes. `bzz stop` starts no new agent and waits; after a crash the next start re-attaches to agents that are still alive.

**I used version 1 (PowerShell).** See `docs/MIGRATION-v1.md`.

## License

MIT, see [LICENSE](LICENSE).
