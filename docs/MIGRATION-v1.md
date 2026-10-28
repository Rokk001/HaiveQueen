# Migrating from version 1 (PowerShell) to version 2 (Python)

Version 2 replaces every PowerShell script by Python and keeps the state in one SQLite database instead of JSON and Markdown files.

## What changes

| Version 1 | Version 2 |
|---|---|
| `start.ps1 -Project x.orch.json`, `stop.ps1` | `orch start`, `orch stop` |
| `<project>.orch.json` | `.orchestrator/project.toml` in the project and `config.toml` for the machine (`examples/`) |
| `ORCHESTRATOR.md` | `docs/AGENT-RULES.md` |
| plan header lines and package tables | TOML header between `+++` lines (`examples/plan.example.md`) |
| runtime files (`AGENTS.json`, `ORCH-HANDLED.json`, `NOFOLLOW.json`, `ORCH-STATE.json`, ...) | tables of the SQLite database |
| questions and answers files, integration queue file | tables `questions`, `decisions`, `queue`; optional read-only Markdown views (`orch views`) |
| `zst-usage.py`, `usage.ps1` | `orchlib/usage.py` |
| Windows only, PowerShell 7 | Windows (Windows Terminal) and Linux (tmux), Python 3.11+ |

The commands for agents stay the same four: `orch done`, `orch ready`, `orch ask`, `orch note`.

## Steps

1. Stop the version 1 loop (`stop.ps1`) and let the running agents finish.
2. Install version 2: `git clone`, check out `v2` (or `main` after the merge), `pip install -e .`.
3. Create the settings: `orch init <project folder>` and `orch register <project folder>`; copy your providers, slots and class models into `config.toml` (`examples/config.example.toml`).
4. Convert your plans: move the status and the package table into the TOML header of the plan file.
5. Import the old state once: `orch import-legacy ...` reads the runtime files, the questions and answers files and the queue file; a second run adds nothing.
6. Start with `orch start`.

Old files are only read, never changed, so you can go back to version 1 at any time.
