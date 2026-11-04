# HaiveQueen profile

All agents work with one profile folder, `<data folder>/profile/<protocol>`, and never with the owner's private files (global instructions, memory, settings). Each CLI is started with its config-dir variable pointing there. The profile stays almost empty: the login and a few settings.

| Provider | Variable | Login file (made once by the owner) | Settings HaiveQueen writes | Private files it would load otherwise |
|---|---|---|---|---|
| Claude | `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | `.credentials.json` (`claude auth login`) | `settings.json` (auto memory off) | `~/.claude`: `CLAUDE.md`, `settings.json`, rules, memory, plugins |
| Codex | `CODEX_HOME` | `auth.json` (`codex login`) | `config.toml` (history and memories off) | `~/.codex`: `AGENTS.md`, `config.toml`, memories, sessions |
| OpenCode | `OPENCODE_CONFIG_DIR`, `XDG_*_HOME`, `OPENCODE_DISABLE_CLAUDE_CODE=1` | `data/opencode/auth.json` (`opencode auth login`) | none | global `AGENTS.md`, `~/.claude` fallbacks, `~/.local/share/opencode` |

- Log in once with the variable set to the profile folder (the window event names the command and the folder). The ready-made commands for Claude, Codex and OpenCode on Windows (PowerShell) and Linux (bash) are in the README, section "Quick start".
- In the settings window every provider's page shows "Login present" or "Login missing" (is one of the login files above in the profile?) and a **Login** button. The button asks first, then opens a visible terminal window with the variables set and the login command running; the status is read again when that window is closed or the page is opened again.
- A CLI HaiveQueen does not know brings its own values in its provider entry in `config.toml`: `profileEnv` (table: variable = value, `{home}` is the profile folder `<data folder>/profile/<protocol>`), `loginCommand` (the full command line of the login) and `loginFiles` (login files, relative to that folder). They are used for the profile, the login status and the Login button. Without `profileEnv` or without `loginFiles` the rule below applies (the provider cannot be kept to the profile). Claude, Codex and OpenCode keep their built-in values.
- A provider without a login in the profile has no free slot: its packages wait, others go on; the window tells the owner once.
- A provider whose CLI cannot be kept to the profile is not started; its package stops and a question is filed.
- Session logs stay 8 days (the usage table and the Codex limit read them), then they are removed. Nothing is copied from a private folder.
