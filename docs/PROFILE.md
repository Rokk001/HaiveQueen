# HaiveQueen profile

All agents work with one profile folder, `<data folder>/profile/<protocol>`, and never with the owner's private files (global instructions, memory, settings). Each CLI is started with its config-dir variable pointing there. The profile stays almost empty: the login and a few settings.

| Provider | Variable | Login file (made once by the owner) | Settings HaiveQueen writes | Private files it would load otherwise |
|---|---|---|---|---|
| Claude | `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` | `.credentials.json` (`claude auth login`) | `settings.json` (auto memory off) | `~/.claude`: `CLAUDE.md`, `settings.json`, rules, memory, plugins |
| Codex | `CODEX_HOME` | `auth.json` (`codex login`) | `config.toml` (history and memories off) | `~/.codex`: `AGENTS.md`, `config.toml`, memories, sessions |
| OpenCode | `OPENCODE_CONFIG_DIR`, `XDG_*_HOME`, `OPENCODE_DISABLE_CLAUDE_CODE=1` | `data/opencode/auth.json` (`opencode auth login`) | none | global `AGENTS.md`, `~/.claude` fallbacks, `~/.local/share/opencode` |
| Kilo Code | `KILO_CONFIG_DIR`, `XDG_*_HOME` | `data/kilo/auth.json` (`kilo auth login`) | none | `~/.config/kilo`, `~/.local/share/kilo` (credentials, `kilo.db` sessions) |

- Log in once with the variable set to the profile folder (the window event names the command and the folder). The ready-made commands for Claude, Codex, OpenCode and Kilo Code on Windows (PowerShell) and Linux (bash) are in the README, section "Quick start".
- Kilo Code keeps its sessions in the database `data/kilo/kilo.db`; HaiveQueen never removes it (only the log folder `data/kilo/log` is pruned).
- In the settings window every provider's page shows "Login present" or "Login missing" (is one of the login files above in the profile?) and a **Login** button. The button asks first, then opens a visible terminal window with the variables set and the login command running; the status is read again when that window is closed or the page is opened again.
- A CLI HaiveQueen does not know brings its own values in its provider entry in `config.toml`: `profileEnv` (table: variable = value, `{home}` is the profile folder `<data folder>/profile/<protocol>`), `loginCommand` (the full command line of the login) and `loginFiles` (login files, relative to that folder). They are used for the profile, the login status and the Login button. Without `profileEnv` or without `loginFiles` the rule below applies (the provider cannot be kept to the profile). Claude, Codex, OpenCode and Kilo Code keep their built-in values. A custom CLI is accepted only with `outputFormat` set to a format HaiveQueen reads (`claude`, `codex` or `opencode`); otherwise it is rejected with a message naming the three.
- A provider without a login in the profile has no free slot: its packages wait, others go on; the window tells the owner once.
- A provider whose CLI cannot be kept to the profile is not started; its package stops and a question is filed.
- Session logs stay 8 days (the usage table and the Codex limit read them), then they are removed. Nothing is copied from a private folder.

## Sources per CLI

Every value above (non-interactive command, JSON output, profile variables, login command, login file) comes from the official documentation or the official repository of the CLI.

| CLI | Documentation / source |
|---|---|
| Claude Code | [Environment variables](https://code.claude.com/docs/en/env-vars), [Authentication](https://code.claude.com/docs/en/authentication#credential-management), [CLI reference](https://code.claude.com/docs/en/cli-reference), [Headless mode](https://code.claude.com/docs/en/headless) |
| Codex CLI | [Authentication](https://developers.openai.com/codex/auth), [Non-interactive mode](https://developers.openai.com/codex/noninteractive) |
| OpenCode | [CLI](https://opencode.ai/docs/cli/), [Config](https://opencode.ai/docs/config/), [Providers](https://opencode.ai/docs/providers/); the XDG folders and the JSON events are read from the [repository](https://github.com/anomalyco/opencode) (`packages/util/src/global.ts`, `packages/cli/src/run/`) |
| Kilo Code | [CLI](https://kilo.ai/docs/code-with-ai/platforms/cli), [CLI reference](https://kilo.ai/docs/code-with-ai/platforms/cli-reference); the XDG folders, `auth.json`, `kilo.db` and the JSON events are read from the [repository](https://github.com/Kilo-Org/kilocode) (`packages/core/src/global.ts`, `packages/opencode/src/cli/cmd/run.ts`, `packages/schema/src/v1/session.ts`) |

Kilo Code is a fork of OpenCode and writes the same JSON lines (`step_start`, `text`, `tool_use`, `step_finish`, `error`), so both use one reader.

## CLIs not offered yet

The settings window lists a CLI only when HaiveQueen can really run it: it reads its output, keeps it to the profile folder, can log it in and has a test for it. For the CLIs below the official documentation does not give a value that is needed; nothing is guessed, so they stay out of the list until it does. A custom CLI that writes one of the three formats can still be added through its provider entry.

| CLI | What is missing | Documentation |
|---|---|---|
| Pi | No login command in the shell, only `/login` inside the TUI; no permission switch | [Pi docs](https://github.com/earendil-works/pi/tree/main/packages/coding-agent/docs) |
| Kimi Code | Where the login is stored (`oauth.storage`, file or keychain) is not documented; the `stream-json` schema is only in the source | [Kimi Code docs](https://moonshotai.github.io/kimi-code/llms.txt), [repository](https://github.com/MoonshotAI/kimi-code) |
| MiniMax Code | Where the login is stored (file or keychain) is not documented; login needs a browser callback, no headless login | [MiniMax Code docs](https://agent.minimax.io/docs/llms.txt) |
| Antigravity | Login is kept in the OS keychain, no variable moves it | [headless](https://antigravity.google/docs/cli/headless/), [settings](https://antigravity.google/docs/settings/) |
| Crush | No JSON output, login file not documented | [repository](https://github.com/charmbracelet/crush) (`internal/cmd/run.go`) |
| Qwen Code | Login only through `/auth` in the TUI | [headless](https://qwenlm.github.io/qwen-code-docs/en/users/features/headless/) |
| Continue | `cn login` is documented but removed in the source | [CLI docs](https://docs.continue.dev/cli), [repository](https://github.com/continuedev/continue) |
| Mistral Vibe | The key lives in the global OS keychain (`VIBE_HOME` does not move it); login only interactive (`--setup`) | [docs](https://docs.mistral.ai/vibe/code/cli/), [repository](https://github.com/mistralai/mistral-vibe) |
| ZCode | No model flag; the profile needs a `HOME` override; login file is encrypted | [repository](https://github.com/zai-org/ZCode) |
| Cline | `~/.agents` and `~/Documents/Cline` are not moved by `CLINE_DIR`; the profile would need a `HOME` override, which breaks git and ssh for the agents | [CLI docs](https://docs.cline.bot/cline-cli), [repository](https://github.com/cline/cline) |
| GitHub Copilot CLI | Login is kept in the OS keychain; the JSON schema of `-p --output-format json` is not documented | [CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference), [authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli) |
