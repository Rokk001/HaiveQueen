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
- Every start of a Claude Queen writes `claude-integration-settings.json` in its run folder and passes it with `--settings` (a `--settings` file or JSON of the provider entry is merged, its rules and hooks are kept). It adds exact allow rules for `integration acquire`/`release` of its plan and `git push <remote> HEAD:<integration branch>`, and a PreToolUse hook (`haivequeen/claude_hook.py` with the run's `claude-hive-policy.json`). The hook allows a Bash or PowerShell command only when it is one simple `bzz` command (or the HaiveQueen Python CLI) of the own-Hive list with exactly that Queen's project, plan and agent id, and for `ready` and `queue upsert` her own plan branch (the branch the plan or its state file names, else the branch of her worktree at the start; never the integration branch). A branch that does not match the start value is looked up again when the hook runs: the branch the plan or state file names now, else the branch checked out in the worktree she recorded as her cwd; it is refused when it is the integration branch, when a queue entry belongs to another plan, or when an active agent of another plan has the same cwd. When none is known the hook allows neither. `agents update-fields` is allowed only for her own name with exactly one of `--cwd '<absolute existing folder>'` (the form the Queen prompt asks for; it works in Bash, PowerShell 7 and Windows PowerShell 5.1) or `--fields '{"cwd": "<absolute existing folder>"}'` (single quotes, nothing else in the object; a double quote is accepted only in that value; Windows PowerShell 5.1 strips those inner quotes, so this form fails there without a change); the folder has no `..` part and no trailing backslash. Every allowed command uses exact option names, each once, no extra words, and only quoting that Bash and PowerShell read alike (no `;`, `|`, `&&`, redirection, `$`, backticks, globs or PowerShell number literals). Any other command gets no hook decision, so Claude's own rules (deny, ask, the owner's prompt) decide. `bzz decisions update`, `answer`, `kv` and the commands outside the Hive are never allowed by the hook.
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

## Local LLM and API key

A provider with `access = "llm"` (field absent = Subscription, `access = "api"` = API key) has, besides its name, `address` (for example `http://192.168.1.20:1234`, without `/v1`), `apiKey` (optional; shown masked, stored only in `config.toml`) and `contextWindow` (tokens, optional). The fields stand in the provider entry itself; the name of the provider is the name of the LLM. HaiveQueen starts the CLI itself with the server, the same way on Windows, Linux and macOS, without a wrapper script, only through the environment and arguments of the agent it starts. Nothing is written to the user's own profile and no file to the HaiveQueen profile. An empty key is handed over as the placeholder `lmstudio` where a CLI needs a value. With an empty context window nothing about the window is handed over.

| CLI | What HaiveQueen hands over | Interface the server needs | Source |
|---|---|---|---|
| Claude Code | `ANTHROPIC_BASE_URL` = address, `ANTHROPIC_AUTH_TOKEN` = key, `CLAUDE_CODE_ATTRIBUTION_HEADER=0`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW` = context window; `ANTHROPIC_API_KEY` of the user is removed from the agent environment so it is never sent to the server | Anthropic Messages, `/v1/messages` | [LM Studio: Claude Code](https://lmstudio.ai/docs/integrations/claude-code), [Environment variables](https://code.claude.com/docs/en/env-vars) |
| Codex CLI | arguments `-c model_provider=<id>`, `-c model_providers.<id>.name`, `.base_url=<address>/v1`, `.wire_api=responses`, `.env_key=HAIVEQUEEN_SERVER_KEY` (the key is in that variable, never in the command line), `-c model_context_window=<window>` | Responses, `/v1/responses` | [LM Studio: Codex](https://lmstudio.ai/docs/integrations/codex), [config sample](https://developers.openai.com/codex/config-sample) |
| OpenCode | variable `OPENCODE_CONFIG_CONTENT` with an OpenAI-compatible provider block (`@ai-sdk/openai-compatible`, `options.baseURL` = address + `/v1`, `options.apiKey`, the models of the classes with `limit.context`) and the model named `<server id>/<model>`. The block is written in both spellings, `provider`/`npm`/`options` (OpenCode 1.x) and `providers`/`package`/`settings` (current documentation); an OpenCode reads the one it knows | OpenAI-compatible, `/v1/chat/completions` | [Providers](https://opencode.ai/docs/providers/), [Models](https://opencode.ai/docs/models/), [Config](https://opencode.ai/docs/config/) |
| Kilo Code | variable `KILO_CONFIG_CONTENT` with the same provider block (`provider`/`npm`/`options`) | OpenAI-compatible, `/v1/chat/completions` | [Kilo CLI](https://kilo.ai/docs/code-with-ai/platforms/cli) (names `KILO_CONFIG_CONTENT` and refers to the OpenCode config documentation) |

The output limit of an OpenCode/Kilo model, which the block needs next to the context limit, is half the context window, at most 32000 tokens (the fallback the OpenCode documentation names for models without an entry is 32000).

**Connection test.** The test asks `/v1/models` (reachability and model list) and sends an empty request to the interface of each CLI; a path that answers anything but 404, 405 or 501 counts as offered (no model is loaded by the test). The result (time, models, one line per CLI) is kept in `server-tests.json` in the data folder until the next test, also over a restart, and is dropped when address or key change. It runs on the button, after the settings window saved a new or changed server or a provider using it, at the start of HaiveQueen, and once per round while a provider is not ready because of it.

**Ready check.** Subscription needs the login, API key needs a key, Local LLM needs a last test that shows the interface of the CLI; with a Local LLM the last test must show that the server answers and offers the interface of the chosen CLI; every model of a class must be in the model list of its source (a class entry with `customModel = true` is an own entry and counts as available). A provider that is not ready gets no agents.

**API key.** `access = "api"` with `apiKey`: Claude Code reads it from `ANTHROPIC_API_KEY`, Codex CLI from `CODEX_API_KEY` (only these two can run with one key; OpenCode and Kilo Code read a key per model vendor and are not offered for this type). The variable is set only in the environment of the agent HaiveQueen starts, never written to a profile; the usage check defaults to none and the provider is not local.

**Old settings.** `[[servers]]` entries are taken over into every provider that named them (`server = "<name>"`); an entry no provider uses becomes a provider of the same name with `access = "llm"`, `slots = 0` and `use = "fallback"`. The key `servers` is dropped at the next save.  A Codex CLI provider with `endpoint` is read as a provider with an own server (address = `baseUrl` without `/v1`; providers with the same address share one server); other providers keep their `endpoint` as before. `config.toml` is rewritten in the new form only when the owner saves the settings window.

**Models and effort values of the provider page.** The choice lists of the provider page come from `haivequeen/cli-models.json` (per CLI: `models`, `efforts`, `source`). A file of the same name in the data folder replaces the entry of a CLI; an entry there may be only a list of models. With an own server the model list is the list of the last connection test. The ready check uses only a list the owner put in the data folder, so a model the documentation does not list yet never stops a working provider (the page still shows a model outside the list as an own entry).

| CLI | Models | Effort values | Source |
|---|---|---|---|
| Claude Code | the aliases `fable`, `opus`, `sonnet`, `haiku`, `best`, `opusplan`, `sonnet[1m]`, `opus[1m]` and the full names of the model page | `low`, `medium`, `high`, `xhigh`, `max` | [Model configuration](https://code.claude.com/docs/en/model-config) (Available models, Adjust effort level) |
| Codex CLI | `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` (the levels depend on the model) | [Codex models](https://developers.openai.com/codex/models), [Configuration reference](https://developers.openai.com/codex/config-reference) (`model_reasoning_effort`) |
| OpenCode | none: the catalog depends on the connected providers (models.dev); the page offers the own entry | `low`, `medium`, `high`, `max` (variant names; not every model has all of them) | [Models](https://opencode.ai/docs/models) (Variants) |
| Kilo Code | none: its documentation has no fixed list; the page offers the own entry | none documented; the page offers the own entry | [CLI](https://kilo.ai/docs/code-with-ai/platforms/cli) |

## Personal inbox command

The machine settings page offers **Install /bzz**. It creates user commands in
`~/.claude/commands/bzz.md`, `~/.config/opencode/commands/bzz.md` and
`~/.config/kilo/commands/bzz.md`. Existing different files are kept and reported
on the page. Codex CLI is skipped because it does not support a literal `/bzz`;
custom CLIs with an unknown command mechanism are also skipped with a message.
These are commands for the owner's personal CLI sessions.

The command reads open plan questions across registered Apiaries, investigates
technical questions, and asks behaviour, content, design and configured owner-topic
questions one at a time in the Apiary's language. Answers are written as UTF-8
TOML files to that Apiary's inbox and processed on the next round.

Command mechanisms: [Claude Code](https://code.claude.com/docs/en/slash-commands),
[OpenCode](https://opencode.ai/docs/commands/),
[Kilo Code](https://kilo.ai/docs/customize/workflows),
[Codex slash commands](https://learn.chatgpt.com/docs/reference/slash-commands).

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
