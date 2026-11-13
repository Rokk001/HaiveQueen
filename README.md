<p align="center">
  <img src="assets/haivequeen.png" alt="HaiveQueen" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Rokk001/HaiveQueen/actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Rokk001/HaiveQueen/test.yml?branch=main&style=for-the-badge&label=CI"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-%E2%89%A53.11-3776AB?style=for-the-badge">
  <img alt="Windows and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078D4?style=for-the-badge">
</p>

<p align="center"><b>Run plans with Queens and parallel Agents: write a plan, set it to QUEUED (or SENT to have it cut), and HaiveQueen works it through.</b></p>

---

HaiveQueen is a local orchestrator for coding agents ([Claude Code](https://claude.com/claude-code) and the Codex CLI out of the box, any other command through a provider entry). You describe the work as a Markdown plan with packages. For every plan HaiveQueen starts a run: one **Queen** works the plan, starts **Agents** for independent packages, checks that every requirement is met and reports `bzz ready`; the Orchestrator then integrates the result. You watch and steer everything in your browser. No prompts to copy, no terminal to babysit.

- [Why](#why) · [How it works](#how-it-works) · [Quick start](#quick-start) · [Plans](#plans) · [Dashboard](#dashboard)
- [Failures and escalation](#failures-and-escalation) · [Configuration](#configuration) · [Inbox](#inbox) · [Command line](#command-line)
- [Documentation](#documentation) · [Development](#development) · [FAQ](#faq) · [License](#license)

## Why

**1. It runs in its own profile, so your private instructions never reach its agents.** HaiveQueen is a development environment of its own. Every agent it starts (Claude, Codex, OpenCode, Kilo Code) works with one profile folder in HaiveQueen's data folder, never with your private files: your global `CLAUDE.md` or `AGENTS.md`, your memory, your settings and plugins stay beside it, untouched, and are not loaded. Each CLI is started with its config-dir variable pointing at the profile, which holds only the login and a few settings (auto memory and history off). Whatever you have piled into your own agents over time costs HaiveQueen's agents nothing. That is **very token-saving**, and you **run into provider limits much later**. Details: [`docs/PROFILE.md`](docs/PROFILE.md).

**2. Only the project rules, and only from the repository.** Queens and Agents load the complete project rules: the instruction files (`AGENTS.md`, `CLAUDE.md`) checked in to the project repository, plus those of an upstream repository when the project's Knowledge base switch for it is on (a folder in `upstreamRepo` or the Git remote named `upstream`). Each rule file can be switched off on the project page; a switched-off file reaches no agent. The README and untracked workspace files never count. A Queen additionally loads her plan, its state file, the handoff of her predecessor and the decisions and owner answers of her Project; nothing else. The Queen selects no rules for an Agent.

**3. Plans, one Queen and parallel Agents per plan.** Every plan gets its own run. The Queen coordinates and never works a package herself; independent packages go to Agents of the right class, in parallel, and the Queen merges and integrates. Fresh, small contexts replace one huge one, and a context handoff lets a successor continue.

**4. A plan is not done because an agent says so.** Before integration the Queen lists every requirement of the plan with evidence in the state file under `## Completeness`. `bzz ready` is refused for a missing, open or partial requirement, and then an independent **check agent** (fresh, not part of the plan, read-only) compares the plan as submitted with the list and the branch. Only `READY-CHECK: PASSED` accepts the branch.

The worktree cleanup runs in parallel to the round, in a thread of its own (one per project at a time), and never holds the round: however long a cleanup takes, rounds go on and end, the inbox is processed, Queens are started and get their orders, and Agents start. A cleanup error is logged and stops nothing. Cleanup locks only the worktree or branch being removed. A Queen start by the round that needs exactly that target is left out of this round (the round line says so) and tried again in a later round, once the removal has ended; it never makes the round wait and never ends it with an error. A start that a Queen makes herself waits for the removal as before (up to the wait for the scheduling lock), and the round never waits for it; unrelated starts proceed. The scheduling lock records its process, reason and heartbeat; dead or expired holders are recovered, and a timeout identifies the holder and reason. The ready check uses the latest submitted plan, including submissions made by setting its status to `SENT`; if that version is unavailable, submit again. The check agent receives owner and coordinator decisions, and an item naming several requirement IDs applies to each.

**5. A plan cutter with requirements and dependencies.** Hand over free text (`status = "SENT"`). The Queen first writes every requirement as `- <id>: <text>` into the state file, then cuts the plan into small packages with class, `depends_on` and the requirements each covers. `bzz plan check` refuses a cut in which a requirement belongs to no package.

**6. Role files you can read and change without code.** What the Queen, an Agent, the plan cutter and the check agent are told lives in plain Markdown files in [`roles/`](roles).

**7. Written for you, in your language.** The setting `language` (default English) decides the language of every free text agents write to you: questions, notes, final reports and state file entries. Protocol words stay fixed.

**8. Answer once for the whole Project.** When you answer a question, a box applies the answer to the whole Project: it is stored as a decision for every plan of that Project (never another one) and the same question is not asked again. Queens read the decisions first and close a question that a decision already settles.

**9. Control from your browser.** The `plans` button lists the plans of all Projects with their status, including plans that have never started, and starts, pauses, rejects or puts a plan back to draft on a right click. The Queen menu restarts a Queen, also on another provider.

**10. Integration to your integration branch.** The Queen works until `bzz ready`; then the Orchestrator merges the project's integration branch (setting `integrationBranch`, default `main`) into the plan branch, runs the Compile check, Build, Push and Deploy commands of the project and integrates one Plan at a time per project. After deploy you accept with an owner check, or a fix agent repairs exactly what you reject.

Also: the manager is not a model (the Orchestrator is a plain program loop that costs no tokens), process Agents are placed by quota reserve and a Plan no longer rests at a limit (its next Queen chooses another provider), and state lives in one local SQLite database, so after a crash the next start re-attaches to agents that are still alive.

## Words

| Word | Meaning |
|---|---|
| **Project** | a project (a repository with a `.haivequeen/project.toml`) |
| **Plan** | one plan of a Project, worked by one Queen at a time |
| **Queen** | the main agent of a Plan: works the plan, delegates, merges the Agent branches, checks completeness and reports `bzz ready`; she never integrates |
| **Agent** | a helper agent that works one package for its Queen |
| **Orchestrator** | the managing loop: no model, no window, a hidden background process; after an accepted `bzz ready` it runs the integration pipeline (Merge, Check, Build, Push, Deploy) of the plan |
| **Fix agent** | an agent the Orchestrator starts when a pipeline step is red or you reject the result; it repairs the plan in its worktree and the pipeline starts again |

## How it works

1. **Plan.** You write a plan (Markdown with a TOML header, see [Plans](#plans)) and set `status = "QUEUED"`. That is the release; there is no submit step. A draft started with **Start** in the Plans window becomes `SENT` and is cut by its Queen first.
2. **Start.** The Orchestrator reads SENT, PREPARING, QUEUED, RUNNING, WAITING and REVIEW plans, picks a provider that offers the Queen class and starts one run per plan within the machine-wide limits. Plans that can resume take precedence over new plans.
3. **Work.** The Queen coordinates and never works a package herself: every package goes to an Agent of its class, respecting dependencies and the Agent limit. Subagent Agents run in the foreground, independent ones in parallel from one message, so the Queen never polls an Agent. She answers technical questions, merges the Agent branches and, when every requirement is done, writes the completeness answer, calls `bzz ready` and ends: the Queen works until `bzz ready` and never integrates. A package that needs a capability (for example `Image generation`) goes to a provider that offers it; local providers are used when available.
4. **Completeness.** Before integration the Queen answers the completeness question with evidence for every requirement and lists every requirement id once in the state file as `- <id>: done | <evidence>`; `bzz ready` is refused for missing, open or partial ids and a fresh check agent must pass the branch. Partial items count as open. Requirements covered exclusively by packages marked `verify = "owner"` are listed separately as owner checks.
5. **Integration.** After an accepted `bzz ready` the Orchestrator integrates the plan in its worktree, one plan at a time per project, in these steps: Merge (the integration branch into the plan branch), Check (the **Compile check command**), Build (the **Build command**), Push (never forced) and Deploy (the **Deploy command**). An empty command skips its step. A red step starts a fix agent that repairs the plan in its worktree; the pipeline then starts again at Merge. The dashboard shows the chain of steps and the fix agent. A waiting plan resumes when its turn comes.
6. **Acceptance and done.** After the pipeline has pushed and deployed (a step with an empty command is skipped) HaiveQueen itself files an owner check question (`owner-check`, text from the setting `ownerCheckText`, by default "Check the integrated result" in the language setting). Choose Accept in the Questions page or run `bzz plan accept`; choose Not ok with a finding or run `bzz plan reject --text "..."` for rework by a fix agent. Free text alone is a note and leaves the check open. Acceptance sets DONE; a plan whose header says `owner_check = "none"` skips the question and is done after integration. A plan with nothing left to integrate (it changes no tracked file of the code repo and has no plan branch, or every commit of its branch is already in the integration branch) closes the same way without integration: the Queen reports with `bzz ready` (no queue row, no compile check, build or deploy) and ends, a fresh check agent passes it, the owner check asks you to check the result of the plan, and acceptance sets DONE; no "queen-ended" question is filed.

Agent names can be edited in `bee_names.txt` in the HaiveQueen configuration directory. Changes take effect when the next agent starts.

Along the way:

- **Handoffs.** At the context limit no new Agents start, running Agents finish and a new Queen continues from the handoff.
- **Questions.** The Queen decides technical questions herself and records the decision (`bzz decisions add`, source `auto`); questions about the result’s behaviour, content or design, and topics configured by the Project to always go to you, go to you with `bzz ask`, and a question that an earlier decision or answer already settles is closed by reference. An open owner question pauses only the item it blocks; independent work goes on. Any answer can resume the Plan.
- **Slots and provider choice.** Subagent Agent mode is selectable for every provider; when the Queen cannot start embedded Agents, they run as separate processes. A subagent Agent uses a suitable local provider first, otherwise its Queen’s provider, otherwise another provider. A process Agent is placed by the normal provider choice: a suitable provider with the best quota reserve. Every running Queen takes one slot of her provider and every Agent takes one slot of the provider it runs on, a subagent Agent inside its Queen included (a Claude Queen with two subagent Agents holds 3 Claude slots); a subagent Agent keeps its slot until HaiveQueen has closed its end. A Queen needs one free slot to start. When her provider has no free slot for an Agent, the Agent starts as a separate process on another provider by the normal provider choice (class, capabilities, quota reserve), and a stored package assignment to the full provider moves to that one; only when no provider has a free slot does the Agent wait, and a waiting Agent of a running Queen gets the last free slot before a new Queen. Every new Queen of an existing Plan chooses its provider by the normal provider choice (quota reserve, slots, class, Queen capability) just like a new Plan, also after a vanished Queen (who keeps her class) or a wait for a provider; a running Queen is never moved or ended. Only the owner's explicit choice to restart the Queen on a given provider binds the Plan to it, until the owner changes it.
- **Provider limits.** Quota reserve is the weekly quota multiplied by the 5-hour quota (capped at 1); each quota is the unused fraction divided by the fraction of its window remaining. A zero reserve (a used-up window) blocks that provider until it resets; the Plan no longer rests, its next Queen chooses another provider.
- **Interruptions** (laptop sleep, network or API errors, a vanished process) are retried with growing waits and never count as failures.

### Package states

Every package moves through a small state machine that the Queen reports with `bzz package`:

```mermaid
stateDiagram-v2
    [*] --> open
    open --> working
    working --> waiting: blocked (question, build, owner, package, provider)
    waiting --> working: answer arrives or the Queen resumes
    working --> implemented: the Agent finished its own work
    implemented --> done: requirements verified
    done --> rejected: owner did not accept
    rejected --> working: reworked
```

`implemented` means the work is written but not yet verified; `waiting` means someone else has to act first. Neither is a failure (see below). A rejected package is never counted as done, and `bzz ready` is refused until it is reworked.

## Quick start

**Requirements**

- Python 3.11 or newer and a web browser
- at least one of the supported CLIs: [Claude Code](https://claude.com/claude-code) (`claude`), the Codex CLI (`codex`), [OpenCode](https://opencode.ai) (`opencode`) or [Kilo Code](https://kilo.ai) (`kilo`)
- Git (Plans work in worktrees)

**Steps**

```bash
git clone https://github.com/Rokk001/HaiveQueen
cd HaiveQueen
pip install -e .                      # installs dependencies and the command `bzz`

bzz init /path/to/my-project          # creates .haivequeen/project.toml, AGENTS.md and an example plan
bzz register /path/to/my-project      # adds the project to config.toml and the database
```

Each Project’s `.haivequeen/` folder contains machine-local settings and runtime files; keep it local to each machine and do not commit it. `bzz init` and `bzz register` add it to the project folder’s `.gitignore` and warn if it is already tracked.


To install a particular release instead of the newest code, check out its tag after cloning (for example `git checkout v2.0.1`) before `pip install -e .`. Keep the clone where it is: the update function below installs new versions into that folder.

### Updates

HaiveQueen updates itself the same way for every user:

- Once a day (`update.checkHours` in `config.toml`, default 24; `0` or the environment variable `BZZ_NO_UPDATE_CHECK=1` switches the daily check off) and whenever you click **Check now** in the header of the dashboard, HaiveQueen asks GitHub whether a newer release exists. `bzz update` does the same check on the command line. A failed check (offline, rate limit) is shown in the header and tried again after the interval or with Check now.
- When one exists, the header shows **New version available: X – Update**. Nothing is installed until you click it and confirm.
- After the confirmation HaiveQueen starts no new Queen or Agent, waits until every running agent of every Project has ended (it ends none), fast-forwards the clone to the release tag `vX`, runs `pip install -e .` again when `pyproject.toml` changed and starts again; the dashboard reopens with the new version. If the new version does not start, it goes back to the previous one and files a question. `bzz update --install X` does the same for the offered version X.
- The header shows the installed version: the release tag of the clone (without one, the version in `pyproject.toml`).
- The releases come from the GitHub repository of the clone's `origin` remote; `update.repository = "owner/name"` in `config.toml` names another one. Local commits on the clone's branch stop the fast-forward; the update then fails and keeps the installed version.

**Log in once per CLI, in the HaiveQueen profile.** Do this for every CLI you want to use, after `bzz register`. The agents never read your own login (see [Why](#why)), so each CLI needs its login in the profile folder `<data folder>/profile/<cli>` (data folder: `%LOCALAPPDATA%\HaiveQueen` on Windows, `${XDG_DATA_HOME:-~/.local/share}/HaiveQueen` on Linux). Set the variable(s) that point the CLI to that folder, then run its login command:

PowerShell (Windows):

```powershell
# Claude
$env:CLAUDE_CONFIG_DIR = "$env:LOCALAPPDATA\HaiveQueen\profile\claude"; claude auth login
# Codex
$env:CODEX_HOME = "$env:LOCALAPPDATA\HaiveQueen\profile\codex"; codex login
# OpenCode
$p = "$env:LOCALAPPDATA\HaiveQueen\profile\opencode"; $env:OPENCODE_CONFIG_DIR = "$p\config"; $env:XDG_CONFIG_HOME = "$p\config"; $env:XDG_DATA_HOME = "$p\data"; $env:XDG_STATE_HOME = "$p\state"; $env:XDG_CACHE_HOME = "$p\cache"; opencode auth login
# Kilo Code
$p = "$env:LOCALAPPDATA\HaiveQueen\profile\kilo"; $env:KILO_CONFIG_DIR = "$p\config"; $env:XDG_CONFIG_HOME = "$p\config"; $env:XDG_DATA_HOME = "$p\data"; $env:XDG_STATE_HOME = "$p\state"; $env:XDG_CACHE_HOME = "$p\cache"; kilo auth login
```

bash (Linux):

```bash
# Claude
CLAUDE_CONFIG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/HaiveQueen/profile/claude" claude auth login
# Codex
CODEX_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/HaiveQueen/profile/codex" codex login
# OpenCode
p="${XDG_DATA_HOME:-$HOME/.local/share}/HaiveQueen/profile/opencode"; OPENCODE_CONFIG_DIR="$p/config" XDG_CONFIG_HOME="$p/config" XDG_DATA_HOME="$p/data" XDG_STATE_HOME="$p/state" XDG_CACHE_HOME="$p/cache" opencode auth login
# Kilo Code
p="${XDG_DATA_HOME:-$HOME/.local/share}/HaiveQueen/profile/kilo"; KILO_CONFIG_DIR="$p/config" XDG_CONFIG_HOME="$p/config" XDG_DATA_HOME="$p/data" XDG_STATE_HOME="$p/state" XDG_CACHE_HOME="$p/cache" kilo auth login
```

Without a login a provider counts as not available and its packages wait; all others go on. You can also do this from the dashboard: the settings page shows "Login present" or "Login missing" on every provider's page, and its **Login** button opens a terminal window with the variables set and the login command running. For a CLI HaiveQueen does not know, set `outputFormat` (the format it writes: `claude`, `codex` or `opencode`; without one of them the provider is rejected with a clear message), `profileEnv`, `loginCommand` and `loginFiles` in its provider entry (see [`examples/config.example.toml`](examples/config.example.toml)); the same status, profile and button then work for it.

```bash
bzz start                             # starts the loop in the background and opens the browser dashboard
bzz stop                              # no new agent of any kind (incl. subagents); running agents finish their task
```

Put your machine settings (providers, slots) into `config.toml` (see [`examples/config.example.toml`](examples/config.example.toml); folder: `%APPDATA%\HaiveQueen` on Windows, `~/.config/HaiveQueen` on Linux).

### Setting up a provider

Open the settings page (`config`) and use **Add provider**. One form holds everything: at the top the switch **Enabled** (on by default; a disabled provider gets no new Queen or Agent, appears nowhere in the dashboard, but stays in the list, dimmed, with all its values, and running agents finish), then the name and the dropdown **Access type** (Subscription, API key or Local LLM) decides which fields the form shows. **CLI** (Claude Code, Codex CLI, OpenCode, Kilo Code; Custom CLI only for Subscription), **Models** (light, medium, heavy), **Use** and **Advanced** are the same for every access type. Under **Use**, **Agent mode** selects Subagent or Process; Subagent is selectable for every provider; Agents run in separate processes when the Queen cannot start embedded Agents.

- **Subscription.** Choose the CLI and press **Login** (or log in as described above). The login belongs to the CLI, so all providers with the same CLI share it.
- **API key.** Choose a CLI that can run with a key (Claude Code, Codex CLI) and enter the key. It is shown masked and kept only in the machine `config.toml`; HaiveQueen hands it to the CLI only in the environment of the agent it starts (never in a profile file), so a Subscription login and API key providers of the same CLI, also with different keys, work side by side. The limit source is derived from the provider access type and CLI.
- **Local LLM** (for example [LM Studio](https://lmstudio.ai) on another machine). Choose the CLI, enter the address (for example `http://192.168.1.20:1234`), an API key if the LLM needs one and the context window in tokens, and press **Test connection**. The test lists the models and shows for each CLI whether the LLM offers the interface it needs (Claude Code: Anthropic Messages, Codex: Responses, OpenCode and Kilo Code: OpenAI-compatible). The model fields offer the models of the last test; **Simultaneous agents** is the number of slots of this LLM. HaiveQueen starts the CLI with the address, key and context window itself, only inside the agent environment; no wrapper script is needed. This works the same on Windows, Linux and macOS. A Local LLM provider counts as local and needs no login. A provider without a usage limit (as every Local LLM has) always ranks before the providers with a limit when it has a free slot, the class model and the capabilities; in the Model Limits panel its quota reserve shows as +99 %. Older `[[servers]]` entries are taken over into the providers that used them (an unused one becomes a Local LLM provider with 0 simultaneous agents and priority fallback) and removed when you save.

Address and key are machine settings: they are stored only in the machine `config.toml`.

The loop and all agents run as hidden background processes; the dashboard runs in your browser. Closing the browser leaves the loop running, and `bzz start` opens it again without restarting agents. A start during a pending stop, during a running update or while another start is under way is refused: `bzz start` then exits with 1, and the reason is an event and a line in `runs/orchestrator.log`. A start while the loop runs still exits with 0. Windows desktop shortcuts can use `bzz start --desktop` and `bzz stop --desktop` for a visible result or error dialog; ordinary CLI calls keep their JSON output.

## Plans

A plan is a Markdown file with a TOML header between `+++` lines ([`examples/plan.example.md`](examples/plan.example.md)). Only a plan with one of the statuses `SENT`, `PREPARING`, `QUEUED`, `RUNNING`, `WAITING` or `REVIEW` starts agents. A broken header, an unknown status or an unknown class is shown as an error in the Plans window. The plan header records `branch` and `worktree`. At the cut the Queen takes them from the plan or uses the default branch `<plan file name in lower case without -PLAN>` and its project worktree; `bzz plan check` requires both and rejects the main checkout. When the branch already exists and the plan has no run history, you are asked whether to take it over or create a new one.

```toml
+++
status = "QUEUED"

[[packages]]
id = "P1"
title = "Add the parser"
class = "medium"        # light | medium | heavy: model and effort come from the provider
depends_on = []

[[packages]]
id = "P2"
title = "Draw the icons"
capability = "Image generation"   # only providers with this capability may run it
verify = "owner"        # code (default) | build | owner: who closes the last requirements
depends_on = ["P1"]

[[packages]]
id = "P3"
title = "Final check of the whole plan"
class = "heavy"
depends_on = ["all"]    # waits for every other package of this plan
+++
# My plan
Free Markdown below the header: the agents read it.
```

| Field | Meaning |
|---|---|
| `status` | `DRAFT` -> `SENT` -> `PREPARING` -> `QUEUED` -> `RUNNING` <-> `WAITING` -> `REVIEW` -> `DONE`, plus `PAUSED` and `CANCELLED`. `SENT` hands over a plan that is not cut yet (see below); `QUEUED` releases a cut plan; `PREPARING` (a Queen cuts it), `RUNNING`, `WAITING` (every open package waits; the Plans window says what for), `REVIEW` (integrated, waits for your owner check) and `DONE` are set by HaiveQueen; `PAUSED` stops new starts (window: `Space`); `CANCELLED` never starts again by itself. The old names `SUBMITTED`, `READY`, `IN_PROGRESS` and `REJECTED` are read as `SENT`, `QUEUED`, `RUNNING` and `CANCELLED` and written with the new name at the next status change |
| `class` | `light`, `medium` or `heavy`; the provider maps each class to a model and an effort |
| `depends_on` | package ids this one waits for, or `["all"]` |
| `capability` | only providers offering it may run the package. The name is free: you define it in the provider entry of the machine settings (`capabilities = ["Coding", "Queen", "Image generation", "3D modelling", "Build"]` and `capabilitiesVersion = 1`) and several providers may offer the same one, for example `3D modelling` or `Build` for work that only some models are suited for. A package whose provider is missing waits alone; one that another provider offers too goes there. |
| `owner_check` | header key (not per package): `"none"` skips the acceptance question after integration, anything else (default) asks you to test the integrated build |
| `verify` | who closes the last requirements: `code` (the Agent does, default), `build` (a build step does) or `owner` (an owner check by you). An Agent is done when its own work is. An unknown value is treated as `code` |

**Submitting a plan that is not cut yet.** Write the plan as free Markdown under a header that only says `status = "SENT"`. The plan gets a run and a Queen like any other, and her first step is to cut it: she reads the text and the capabilities of the configured providers, writes `branch`, `worktree` and the packages (id, title, class, `depends_on`, `verify`) into the header and makes work that only some providers can do (any capability that not every provider offers, such as `Image generation`) a package of its own with `capability`. `bzz plan check` then verifies the header (at least one package, unique ids, a title each, known dependencies without a cycle, every capability offered by a configured provider); when it passes the plan becomes `QUEUED` and the Queen starts Agents. It also warns (without blocking) when a package without a capability waits for one that needs a capability, because that package then stops whenever the provider is missing. `bzz agent start` is refused while a plan is still `SENT` or `PREPARING`. Plans whose header already lists packages run as before. A package that waits for a missing provider stops only itself: the Queen keeps working every other package that can start, and the Plan waits only when none can.

A plan may be paused and resumed in the window. A paused plan gets no new Queen, Agent, embedded subagent or ready-check agent; a running Queen keeps working.

## Failures and escalation

Package classes bind the work: each package is worked at its class, and the Queen escalates to the next offered class only when the current model cannot solve it. Failed attempts are counted per package and class; after `escalateAfter` failures (default 3) the next higher class is used and the count starts again. If no higher class can solve it, the owner is asked. A package that needs a capability escalates only through the providers that offer it; after their highest class fails, the owner is asked.

**Offline providers.** When an agent of a provider ends with a 5xx or a connection error, the provider is offline: it gets no new Queen or Agent and no retry, new tasks and retries go to other providers, a stored package assignment to it is dropped, and the Model Limits panel shows "offline" in red (the hover text says since when). Agents that run keep running. Every `providerRecheckMinutes` (machine `config.toml`, default 5) HaiveQueen checks an offline provider, a Local LLM with its connection test and a Subscription or API key provider with a short call of its CLI; when that works the provider is online at once. A rate limit keeps its own handling.

**What counts as a failure** is deliberately narrow: only a model that keeps trying the same thing and does not get it done. These never count and never escalate:

- a package marked `implemented` (the Agent finished; the rest is verification),
- open requirements of a package whose `verify` is `build` or `owner`,
- an Agent that waits for something: `bzz agent result --outcome failed --blocked-by question|build|owner|package|provider` sets the package to `waiting` instead (a question needs `--blocked-ref <question id>`, and the package works again when that question is answered),
- interruptions and provider limits.

A `failed` result without evidence (`--open-requirements` or `--error`) is refused, so a bare "failed" never reaches the counter.

## Dashboard

`bzz start` starts the local web service and opens the dashboard in your default browser. `bzz gui` opens the same browser interface; `--detach` and `--project` remain accepted for existing shortcuts. No desktop window or Tk installation is required. Closing the browser leaves the loop and agents running.

The dashboard shows plans, Queens and Agents, model limits, tokens, system load, questions and logs. Use the controls to start, pause, resume or stop orchestration, manage plans, answer questions and inspect an agent's live output. Owner checks have explicit Accept and Not ok actions; a rejection needs a finding and can select individual packages for rework.

General, Providers and Projects settings use the existing TOML files. Browser settings changes use revisions to detect concurrent edits. Provider login, connection tests, benchmarks, updates and profile installation run through the same backend services as the CLI.

**Settings.** The `config` entry opens an editor for `config.toml` and the `project.toml` of every Project as forms: General, one page per provider (the Claude allowed tools as their own list, one tool per line) and one per project. Save checks the settings the way the loop does when it reloads them, keeps the old file as `<name>.bak` and keeps the comment lines at the top of the file; the running HaiveQueen applies the change without a restart. Comments inside the file are not kept.

**Live output.** `Enter` on a Plan row opens a read-only window with the last 300 lines of the running Queen of that Plan (the latest one when none runs); on a Queen or Agent row it shows that agent. The window reloads every 2 seconds, follows the end of the text, and opening the same agent again focuses its window. The text is `milestones.txt` in the agent's run folder (`<data>/runs/<project>/<agent>/`); `runner.log` next to it holds the plain runner output.

## Configuration

Machine `config.toml` holds `hives` (default 2), `excludedProviders`, provider `slots`, provider `classes` and the machine resource settings. `queenClass` (default `medium`) and `beesPerQueen` (default 3) are General settings (Queen class, Agents per Queen), not project settings. Per provider, `beeMode` chooses how the Agents of that provider's Queen run: `subagent` (embedded in the Queen's process, small context; default for Claude) or `process` (a separate agent each; default for Codex); it can also be changed under Use in the settings page; Subagent is selectable for every provider; Agents run in separate processes when the Queen cannot start embedded Agents. `escalateAfter` (settings label "Failed attempts before a stronger model") defaults to 3; `providerRecheckMinutes` (default 5) is the wait between two checks of an offline provider. `beesPerQueen` counts every running Agent of a Queen, separate processes and embedded subagents alike. Provider `slots` count them too: every Agent takes one slot of the provider it runs on, a subagent Agent one of its Queen's provider. `readyCheckClass` (default `medium`) is the class of the check agent of `bzz ready`. Free RAM below `minFreeRamGB` (default 2) of a project skips only that project in a round. `contextLimitTokens` defaults to 300000: set it once for all Projects; a project may override it, and a model entry under `[providers.classes]` may carry its own value that wins for Queens and Agents running with that model. Project settings override machine settings except the machine-wide ones. Per project (`.haivequeen/project.toml`): `integrationBranch` (default `main`), `language` (default English; the language of all free text written to you), `upstreamRepo` (folder of an upstream repository whose checked-in `AGENTS.md` and `CLAUDE.md` are project rules too) and `codeRepo`. See [`examples/config.example.toml`](examples/config.example.toml) and [`examples/project.example.toml`](examples/project.example.toml).

- **Quotas.** Claude subscription quotas are refreshed at most once per minute with the local CLI `/usage` metadata command, without model turns, hooks, MCP servers or saved sessions. The dashboard and scheduling share the verified account windows; a failed refresh hides the old percentage in the dashboard and stores what went wrong. Starts keep using the last verified reading for up to 6 hours; with an older or no reading automatic Claude selection pauses until a refresh succeeds. A Plan that waits for a provider shows why (quota used up, usage unknown, or no free slot). It can resume when that provider becomes available or another provider can take a waiting package; the new Queen needs one slot and is chosen by the normal provider choice.
- **Project page.** The General page of a project file shows these settings; they live in `.haivequeen/project.toml`.
  - `ownerTopics`: topics whose questions always go to the owner, also a technical one the Queen could decide (default none).
  - `ownerCheckText`: the owner check question after integration (default "Check the integrated result" in the language setting).
  - `compileCheck`, `buildCommand`, `deployCommand` ("Compile check command", "Build command", "Deploy command", beside each other in the main part of the project page): each is one command line that the Orchestrator runs in the plan's worktree after an accepted `bzz ready` (Merge, Check, Build, Push, Deploy). An empty field skips its step; there are no switches. The progress watch of the compile check (`compileStallMinutes`, default 10) also watches Build and Deploy; Build and Deploy have no fixed overall time limit (only the Check has `compileMaxMinutes`), and a command without progress counts as red and gets a fix agent. No provider is involved. A package marked `verify = "build"` is closed like a code package and never holds the Queen (its build is the Build step after `bzz ready`).
  - `integrationBranch` (default `main`), `remoteName` (default `origin`) and `worktreeLocation` (default beside the repository; a relative path counts from the repository).
  - `deleteFinishedWorktrees` (default on) removes worktrees whose commits are integrated.
  - `publishReleases` "Publish release at integration" (default off; offered only for a Project whose remote is on GitHub): after every integration into the integration branch, HaiveQueen itself (never an agent) creates the tag and publishes the GitHub release of the integrated commit. The version counts the last digit of the latest release up (2.0.1, 2.0.2, ...); without a release yet it counts up from the version in the Project's `pyproject.toml`. `nextVersion` (for example `2.1.0`) takes a bigger step once: it is used when it is newer than the latest release, afterwards counting continues from it. Publishing needs a GitHub token with write access to the repository (`GITHUB_TOKEN` or `GH_TOKEN`, else the login of the GitHub CLI `gh`); a failed publication keeps its version and commit and is tried again in the next rounds, and the log names the reason. The released commit is the one at which the integrated branch tip came into the integration branch (its first-parent history), so a release that is retried later never takes a commit of a later integration; integrations are released in their order, the loop also releases an integration whose Queen did not run `bzz integration release`, and integrations from before the switch was turned on get no release.
  - `deleteIntegratedBranches` (default off) lets HaiveQueen itself delete the plan branch and its sub-branches (`<branch>-...`, `<branch>/...`), local and on the remote, after the plan is integrated. A branch stays when it holds a commit that is not in the integrated tip, is checked out in a worktree, has an open queue row, belongs to another open plan, is the integration or default branch, or an agent of the plan still runs; the log names the reason. With `deleteFinishedWorktrees` off, the checked-out branches therefore stay too.
- **Slim project settings.** A `project.toml` holds only: `name` (default the folder name of the repository), `codeRepo` (the repository), `integrationBranch` (default the default branch of the repository's remote, else `main`), `compileCheck` (empty: no compile check), `buildCommand` (empty: no build), `deployCommand` (empty: no deploy) and the Advanced settings `plans` (plan file patterns, a pattern starting with `!` excludes; default `docs/plans/*.md`), `worktreeLocation`, `deleteFinishedWorktrees`, `deleteIntegratedBranches`, `publishReleases`, `nextVersion`, `onEvent`, `colour`, `priority`, `buildProcessNames` and `ownerTopics`; the internal keys `worktreePrefix` and `upstreamRepo` stay. HaiveQueen derives the rest: the Git remote is `origin`, else the only remote not named `upstream` (a project with several remotes and none named `origin` waits for integration until `remoteName` is set); the plan tag in branch and worktree names is the plan file name in upper case without a trailing `PLAN` or `-PLAN` (the folder name for `PLAN.md`; when a started plan already holds the tag, the folder goes in front; a plan that already runs keeps its tag); files ignored by the repository's `.gitignore` never keep a worktree from being removed; every agent of every phase gets the checked-in project rules of the repository (and of the upstream repository when `upstreamRepo` is set), never a rule file from outside it. Owner name (`owner`), language, round length, failed attempts before a stronger model, `queenClass` and `beesPerQueen` come from General; each provider has a `contextLimit` and, for Claude Code, `claudeTools` and its allowed commands.
- **Migration.** At the start HaiveQueen rewrites every `project.toml` of config.toml `projects` once to the slim set. The old file stays as `project.toml.bak` (replacing an older one), and `removed-settings.md` beside it lists every removed key with its old value and where its effect is now, or that it had none; a later migration adds a dated section. Owner name, Queen class, Agents per Queen, the providers' Context limit and the Claude providers' tools are written to `config.toml` in the same step (`config.toml.bak` kept). A file already in the slim form is not changed.
- **Leftover processes.** When a Queen or an Agent ends (normally, with an error or stopped), HaiveQueen ends every process the agent started and that still runs, also one whose parent already ended. The list is kept in the run folder (`processes.json`), so the leftovers of an agent that ended while HaiveQueen was not running are ended at the next start. A compile check started by HaiveQueen and a separate Agent started with `bzz bee start` are not touched.
- **Reload.** Saved changes to the machine `config.toml` and to registered projects' `.haivequeen/project.toml` are reloaded at the next poll, also while idle, without restarting running agents. Valid changes wake scheduling at once; an invalid or temporarily missing file keeps the complete last valid settings. An explicit `--round-minutes` still takes precedence.

## Inbox

Every project has an inbox folder: `<workspace>/.haivequeen/inbox/`. Anyone (the owner, a coordinator, a script) can leave answers and decisions there as TOML files, and the Orchestrator takes them over at the start of the next round. No command needed.

- Only files that end in `.toml` (UTF-8) are read, oldest first. Write a file as `<name>.toml.tmp` and rename it when it is complete; other endings are ignored.
- A file holds `[[answer]]` tables (`question_id`, `text`, `by` = `owner` or `coordinator`) and `[[decision]]` tables (`text`, `source` = `owner` or `coordinator`, optional `plan` and `item`). See [`examples/inbox.example.toml`](examples/inbox.example.toml).
- It may also hold `[[queue]]` tables, orders to the integration queue: `branch`, `action` (`withdraw`, `mark_cleanup` or `requeue`), `reason` and an optional `tip` (full or abbreviated commit id; without it every row of the branch is meant). `withdraw` sets the ready or blocked row(s) to `withdrawn`, but only when the tip is contained in `origin/<integrationBranch>` (checked with `git merge-base --is-ancestor`) or the reason is exactly `superseded` (the German `ueberholt` is still read); otherwise the whole file moves to `inbox/failed/`. `mark_cleanup` stores the mark `erledigt, Aufraeumen erlaubt: <reason>` for the branch; no row changes and nothing is deleted. `requeue` sets the newest ready or blocked row back to `ready` for the next integration run. A row that is in a run or already integrated is never touched.
- A `[[stop_agent]]` table (`name`) stops one running Agent like `bzz agent stop <name>`; subagents and the Queen are refused (see the command table).
- Values go through the same functions as `bzz answer` and `bzz decisions add`: same checks, same events, and a Plan waiting for an answer can resume.
- A file is taken over completely or not at all, then moves to `inbox/done/<YYYYMMDD-HHMMSS>-<name>.toml`. An invalid file moves to `inbox/failed/` with `<name>.error.txt` next to it that names the reason and the line, and a warning event is written.

## Owner messages

A decision with source `owner` (from `bzz decisions add`, the inbox, the dashboard or an owner action such as stopping an Agent) is delivered to a running Queen of its plan, once, in her next `bzz` answer as the key `owner_messages`. Decisions of other sources (`auto`, `coordinator`) are never delivered this way, and a delivered message is not repeated. The Queen treats each message as an instruction from the owner.

## Command line

`bzz agent` is the standard package-agent command; `bzz bee` remains a compatibility alias. Existing settings and machine-readable field names keep their spelling. Free-text answers to owner-check questions are notes and leave the check open.

The following commands are for the owner. Every one refuses calls with `BZZ_AGENT` set. Plan commands require `--project p --plan f`:

| Command | Effect |
| --- | --- |
| `bzz plan accept --project p --plan f [--text "note"]` | Accept an integrated plan waiting for its owner check; close all owner checks and set DONE. Other open questions and integration steps block acceptance; queen-ended and stall questions are closed automatically. |
| `bzz plan reject --project p --plan f --text "finding" [--package P1 --package P2]` | Reopen a plan waiting for its owner check and pass the required finding to a fix agent. Selected packages become rejected. Accept and reject also work while paused; rejection preserves the pause until resume. |
| `bzz plan pause --project p --plan f` | Pause one plan in SENT through REVIEW, preserving its prior status and running agents. DRAFT, DONE, PAUSED and CANCELLED are refused. |
| `bzz plan resume --project p --plan f` | Restore the prior status of a PAUSED plan. Other statuses are refused. |
| `bzz plan release --project p --plan f [--commit C]` | Release the branch without a check agent, for the specified commit or any commit when omitted. Refused for DONE/CANCELLED. Replaces older releases, including legacy inbox decisions; rejection ends the release. |
| `bzz provider enable --provider N` / `bzz provider disable --provider N` | Save the same Enabled setting as the provider page. |
| `bzz provider test --provider N` | Run and store the provider page's Local LLM connection test; other access types have nothing to test. |
| `bzz package unassign --project p --plan f --package P` | Clear the stored provider for the next start, even while the agent runs. Keep the escalated class and failure counts. No assignment is a successful no-op. |

The Questions page keeps its answer field for notes beside Accept and Not ok. Not ok requires a finding and supports selecting individual packages for rework. Inbox files support the same actions as `[[accept]]`, `[[reject]]`, `[[pause]]`, `[[resume]]`, `[[release]]`, `[[provider]]` and `[[unassign]]`. Fields match the commands: `project`, `plan`, `text`, `commit`, `provider`, `package` as applicable; rejection uses a `packages` list. `[[provider]]` uses `action = "enable"`, `"disable"` or `"test"`. Optional `by` is `"owner"` (default) or `"coordinator"`; commands record owner. The project must match the inbox project. For example:

```toml
[[reject]]
project = "example"
plan = "docs/plans/example.md"
text = "The export omits the selected date."
packages = ["P2"]
by = "owner"
```

Existing `[[decision]]` entries with `item = "owner-release"` continue to work.

| Command | Who | What it does |
|---|---|---|
| `bzz start` / `bzz stop` | owner | start the loop in the background and open the dashboard (`--foreground`, `--rounds N`, `--round-minutes M`), or stop starting new agents |
| `bzz gui [--detach] [--project p]` | owner | open the browser dashboard |
| `bzz pause` / `bzz resume` | owner | pause the start of every kind of Agent (Queen, separate Agent, embedded subagent, ready-check agent), or resume; running agents are not ended; `bzz pause --now` also asks every running agent to hand off after its current step, so you do not have to wait for them to run out |
| `bzz status [--project p] [--json]` | owner | "X / Y integrated (%)" and a task / agent / status table per project |
| `bzz questions [--open]`, `bzz answer <id> "<text>" --by owner` | owner | read and answer questions of the agents |
| `bzz queue [upsert]` | owner, Queen | the integration queue |
| `bzz decisions list [--all]`, `bzz decisions update <id> --status active\|rule\|superseded` | owner | decisions; the list shows only `active` ones unless `--all` |
| `bzz init <folder>`, `bzz register <folder>` | owner | create and register a project |
| `bzz export`, `bzz views --project p` | owner | export the state, write the Markdown views |
| `bzz import-legacy ...` | owner | import the state of version 1 (see [`docs/MIGRATION-v1.md`](docs/MIGRATION-v1.md)) |
| `bzz agent stop <name>` | owner | end the one running Agent (the same action as the dashboard button and an inbox `[[stop_agent]]` entry with `name`). A subagent of the Queen is refused with the reason (use the stop key described above), the Queen is stoppable only by `bzz dequeen`, and agents cannot use the command. The package stays stopped and waits for an explicit owner instruction; the Queen is told by an owner message |
| `bzz dequeen <plan>` / `bzz requeen <plan> [provider]` | owner | leave a Plan queenless or start a new Queen, optionally on another provider |
| `bzz agent start`, `bzz agent result`, `bzz agent wait` | Queen | start an Agent (a refused start answers `waiting` with a `reason`), record an attempt or wait for its provider; a failed result needs evidence (`--open-requirements` or `--error`) or `--blocked-by question\|build\|owner\|package\|provider` (a question also needs `--blocked-ref <id>`), which is no failure and sets the package to waiting |
| `bzz package --project p --plan f --package id --state working\|waiting\|implemented\|done\|rejected [--reason ...]` | Queen | report the progress of a package; `waiting` needs `--reason` (`question:<id>`, `build`, `owner`, `package` or `provider`); the window's progress bar counts the done ones |
| `bzz integration acquire` / `release` | Queen or external window | acquire or release the per-project integration place; external windows provide a stable owner process id |
| `bzz update [--install X]` | owner | check GitHub for a newer release now, or install the offered release X (see [Updates](#updates)) |
| `bzz self-update` | owner | one-time transition to the release updates only: for a HaiveQueen checkout that is itself a project named HaiveQueen, waits until all agents have ended, then fast-forwards its `main` from `origin/<integrationBranch>` of that project's settings (default `main`); refused once a release was published for that project or the checkout is at a release tag – then use `bzz update` |
| `bzz done`, `bzz ready`, `bzz ask`, `bzz note` | agents | final report, branch ready for integration, question, note ([`docs/RUNTIME-API.md`](docs/RUNTIME-API.md)) |

## Good to know

- Completed idle Plans are reconciled with the plan status and confirmed integration; active agents and genuine owner questions are preserved.
- A Plan without a Queen shows its recorded start blocker or the numbers of its pending questions instead of a generic waiting label.
- Automatic worktree cleanup requires confirmation that the branch's current commit was integrated; a missing or superseded confirmation keeps the worktree. The cleanup runs in parallel to the round and never holds it.
- Round totals count actual Queen launches, independent of earlier run timestamps.
- Pause and stop prevent every new start of every kind of Agent: Queens, separate Agent processes, embedded subagents and the check agent of `bzz ready`. Pause lasts until you resume; stop lets running agents finish, then ends HaiveQueen, and `bzz start` allows starts again. Running Queens and Agents are not ended; a refused start gives the reason.

## Documentation

| File | Content |
|---|---|
| [`docs/AGENT-RULES.md`](docs/AGENT-RULES.md) | the hard rules every Queen and Agent follows |
| [`roles/`](roles) | the role texts of Queen, Agent, plan cutter and check agent, plain Markdown |
| [`docs/PROFILE.md`](docs/PROFILE.md) | the separate agent profile and the private files it keeps out |
| [`docs/RUNTIME-API.md`](docs/RUNTIME-API.md) | the JSON commands agents use to read and write state |
| [`docs/MIGRATION-v1.md`](docs/MIGRATION-v1.md) | moving from version 1 (PowerShell) to version 2 (Python) |
| [`examples/`](examples) | machine and project settings, a plan, an inbox file and an `AGENTS.md` |

## Development

HaiveQueen uses only the Python standard library. The tests need no extra packages:

```bash
python -m unittest discover -s tests-py -v
```

CI runs the suite on Windows and Linux with Python 3.11 and 3.14, plus a tmux smoke test on Linux. Window tests skip themselves without a display.

## FAQ

**Does HaiveQueen call a model all the time?** No. The Orchestrator runs without a model; Queens and their Agents use models for plan work.

**Which agents can I use?** Claude Code, the Codex CLI, OpenCode and Kilo Code out of the box (the settings page lists exactly these, plus "Custom CLI"); any other command through the `providers` list, as long as it writes one of the output formats HaiveQueen reads (`claude`, `codex`, `opencode`; set `outputFormat`). Other CLIs are not offered until their documentation allows a run, a login and a profile of their own; the reasons are in [`docs/PROFILE.md`](docs/PROFILE.md).

**Why did my package escalate to a bigger model?** Only when the current model tried the same thing repeatedly and did not get it done. Waiting for an answer, a build or you never counts, see [Failures and escalation](#failures-and-escalation).

**Where does the state live?** In one SQLite database in the data folder. It must be on a local disk, never on a network drive (SQLite WAL needs it; the tool refuses to open it there). Export it with `bzz export`; Markdown views are generated read-only.

**Can I stop it safely?** Yes. `bzz stop` starts no new agent and waits; after a crash the next start re-attaches to agents that are still alive.

**I used version 1 (PowerShell).** See [`docs/MIGRATION-v1.md`](docs/MIGRATION-v1.md).

## License

MIT, see [LICENSE](LICENSE).

Providers select capabilities in the settings. Coding permits ordinary packages; Queen permits coordination, including on endpoint providers. Legacy capabilities `images`, `blender`, and `build` are mapped automatically. For manual configuration, set `capabilitiesVersion = 1` to make the list authoritative. The limit source is derived from the provider access type and CLI; `limit.interval` sets the refresh interval in seconds (default 60). A provider entry from before these ticks gets no capability it did not list, also not for its Queen's integration steps: Build and Deploy are done only by a provider with that tick.

Agent worktrees are created by HaiveQueen when `bzz agent start` runs. The branch is `<plan branch>-<package id in lower case>` and the folder is `<worktree prefix>-<plan tag in lower case>-<package id in lower case>`. A replacement uses the same worktree and branch. Agents must never create or commit on another branch.

The plan header's `branch` and `worktree` fields define the plan assignment; `bzz plan check` rejects missing fields and the main checkout. At an Agent's end, HaiveQueen checks its worktree HEAD reflog and retained Git hook evidence. A foreign commit, including on the plan branch or another package's sub-branch, names that branch in the result and fails the attempt. Missing audit evidence also fails the attempt. Commits outside the assigned worktree with a cleared Git environment are outside this audit's scope. Existing legacy package worktrees and branches are kept. See [Agent branches and end audit](docs/BRANCH-ATTRIBUTION.md) for assignment, scope and evidence.
