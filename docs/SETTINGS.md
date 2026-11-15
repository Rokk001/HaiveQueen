# Settings reference

Settings are saved in the [machine and project configuration locations](GETTING-STARTED.md). Use [Providers](PROVIDERS.md) for placement and quota policy, [Operations](OPERATIONS.md) for reload and release-update behavior, and [Profile](PROFILE.md) for endpoint, authentication and model details.

- **Project page.** The General page of a project file shows these settings; they live in `.haivequeen/project.toml`.
  - `ownerTopics`: topics whose questions always go to the owner, also a technical one the Queen could decide (default none).
  - `ownerCheckText`: the owner check question after integration (default "Check the integrated result" in the language setting).
  - `compileCheck`, `buildCommand`, `deployCommand` ("Compile check command", "Build command", "Deploy command", beside each other in the main part of the project page): each configures its [pipeline step](OPERATIONS.md#integration). For enabled local steps, the progress watch of the compile check (`compileStallMinutes`, default 10) also watches Build and Deploy; Build and Deploy have no fixed overall time limit (only the Check has `compileMaxMinutes`), and a command without progress counts as red and gets a fix agent. No provider is involved. A package marked `verify = "build"` is closed like a code package and never holds the Queen (its build is the Build step after `bzz ready`).
  - `integrationBranch` (default `main`), `remoteName` (default `origin`) and `worktreeLocation` (default beside the repository; a relative path counts from the repository).
  - `deleteFinishedWorktrees` (default on) removes worktrees whose commits are integrated.
  - Release workflow: see [release publication and promotion](OPERATIONS.md#releases). The legacy `publishReleases` field migrates to `releaseEnabled`; current projects use explicit release modes and settings.
  - `deleteIntegratedBranches` (default off) lets HaiveQueen itself delete the plan branch and its sub-branches (`<branch>-...`, `<branch>/...`), local and on the remote, after the plan is integrated. A branch stays when it holds a commit that is not in the integrated tip, is checked out in a worktree, has an open queue row, belongs to another open plan, is the integration or default branch, or an agent of the plan still runs; the log names the reason. With `deleteFinishedWorktrees` off, the checked-out branches therefore stay too.
- **Slim project settings.** The project retains repository, plan discovery, execution, cleanup, rules and advanced fields listed below. Machine-only settings remain in General. Integration branch and remote defaults are derived from the repository; ambiguous remotes require a selection. Worktree names and audit rules are defined in [Branch attribution](BRANCH-ATTRIBUTION.md). Ignored files are safe to remove only when the explicit `ignoredBuildOutput` allowlist covers them.
- **Migration.** At the start HaiveQueen rewrites every `project.toml` of config.toml `projects` once to the slim set. The old file stays as `project.toml.bak` (replacing an older one), and `removed-settings.md` beside it lists every removed key with its old value and where its effect is now, or that it had none; a later migration adds a dated section. Owner name, Queen class, Agents per Queen, the providers' Context limit and the Claude providers' tools are written to `config.toml` in the same step (`config.toml.bak` kept). A file already in the slim form is not changed.

## Browser editing

Projects are grouped into Basics, Workflow and checks, Agent rules, Git and cleanup, and Advanced. AI providers separate connection, models and effort, usage limits, and Tools/MCP. System groups resources, agent control, web access, retention and updates. Search finds settings across the categories of the current page without losing unsaved edits. The project summary shows the configured execution sequence; execution templates apply only after clicking Apply template. Raw extension keys and CLI arguments remain under Advanced. Inherited application/repository defaults are identified when they have not been explicitly set. Save validates inputs and reveals the category containing an invalid input. The save bar explains when changes take effect; saving settings does not replace the configuration already loaded by running agents. Web listener settings may be applied immediately.

## Machine keys

Values shown are application defaults. Explicit machine settings override them; supported project overrides apply afterwards, except machine-only keys. Models can override the provider context limit. Raw extension keys remain editable in Advanced.

| Key | Default | Purpose |
| --- | --- | --- |
| `slowTestSeconds` | `10` | Show measured individual tests at or above this duration in log details; advisory only, no automatic retry or blocking. |
| `providers` | `[]` | Named provider entries; see provider fields below. |
| `slots` | `2` | The number of agents that may run at once. |
| `weeklyLimitSources` | `[]` | Legacy usage-source configuration; access type now derives each provider limit source. |
| `buildJobs` | `0` | Build budget table; cores, parallel targets and RAM reserve. Legacy numeric values remain readable. |
| `ramReserveMb` | `2048` | Machine RAM reserve in MiB; supplies minFreeRamGB when absent. |
| `exportDir` | `""` | Destination for state exports. |
| `projects` | `[]` | Registered project workspace folders. |
| `retention` | `{"logsDays": 30, "runsDays": 30, "databaseDays": 90, "inboxDays": 30}` | Days retained for logs, run directories, database history and processed inbox files. |
| `update` | `{"checkHours": 24, "repository": ""}` | Release check interval and repository override. |
| `owner` | `"Owner"` | How the Queens and Agents call you in their texts. |
| `hives` | `2` | Maximum simultaneous plans. |
| `excludedProviders` | `[]` | Providers excluded from scheduling. |
| `queenClass` | `"medium"` | The model class of the Queens (light, medium or heavy). |
| `diagnosisClass` | `"medium"` | The class used to review technical questions; model and effort come from the provider settings for this class. |
| `beesPerQueen` | `3` | How many Agents a Queen may have running at the same time. |
| `roundMinutes` | `5` | The time between coordinator rounds in minutes. |
| `escalateAfter` | `3` | Failed attempts trigger diagnosis before another attempt or a more expensive model is authorized. |
| `clockJumpSeconds` | `120` | Clock jump treated as interrupted work (for example suspend/resume). |
| `providerRecheckMinutes` | `5` | Minutes between offline-provider probes. |
| `retryWaitMinutes` | `[1, 2, 4, 8, 15]` | Backoff sequence for interruptions; the final delay repeats. |
| `rateLimitWaitMinutes` | `15` | Wait when a provider supplies no quota reset time. |
| `compileStallMinutes` | `10` | Minutes without output or CPU progress before Check, Build or Deploy fails. |
| `maxConcurrentCompileChecks` | `2` | The most compile checks that run at the same time on this computer, across all projects and Queens, also for several plans of one project. |
| `compileMaxMinutes` | `120` | Overall Check time limit; excludes waiting for a check slot. |
| `contextLimitTokens` | `300000` | Fallback context handoff threshold. |
| `integrationBranch` | `"main"` | The branch finished plans are merged into; empty: the default branch of the remote, else main. |
| `remoteName` | `"origin"` | The Git remote HaiveQueen pushes to and fetches from; choose it because the repository has several and none is named origin. |
| `worktreeLocation` | `""` | The folder that holds the worktrees of the Agents; a relative path counts from the repository. |
| `ownerTopics` | `[]` | Questions about these topics are never answered automatically. |
| `compileCheck` | `""` | The command that compiles the code and runs the tests before integration; empty: no compile check. |
| `buildCommand` | `""` | The command that builds the product after the check, run in the worktree of the plan; empty: no build. |
| `deployCommand` | `""` | The command that deploys the product after the push, run in the worktree of the plan; empty: no deploy. |
| `deleteFinishedWorktrees` | `true` | Remove a worktree once its work is integrated and nothing is lost. |
| `deleteIntegratedBranches` | `false` | Remove plan branches once they are integrated and nothing is lost. |
| `publishReleases` | `false` | Legacy publication switch; migrated into the execution settings below. |
| `nextVersion` | `""` | The version of the next release, for example 2.1.0. |
| `worktreePrefix` | `"wt"` | Internal prefix for newly assigned worktrees. |
| `buildProcessNames` | `[]` | Running programs that show a build is still busy. |
| `language` | `"English"` | Language of the interface and owner-facing text; protocol words remain unchanged. |

## Project and compatibility fields

The labels include legacy fields that can appear in migrated configurations. `repository` and `plansFolder` are browser folder selectors; `codeRepo` stores the code repository. Slim-project migration records removed keys in `removed-settings.md`.

Project Basics shows a single **Plan files (patterns, one per line)** field. It combines legacy `planGlobs` and `planExclude` into `plans`, prefixing exclusions with `!`. Opening settings does not rewrite the file; saving stores the combined field. A populated `plans` field retains its existing precedence over legacy keys. An empty `plans` field uses the legacy patterns and exclusions, or the default `docs/plans/*.md`, so opening, saving and migration preserve effective plan discovery.

| Key | Meaning |
| --- | --- |
| `allowedTools` | Commands agents of this Project may run without asking. |
| `buildCommand` | The command that builds the product after the check, run in the worktree of the plan; empty: no build. |
| `buildProcessNames` | Running programs that show a build is still busy. |
| `claudeTools` | Comma-separated Claude tool names; defaults come from config.DEFAULT_CLAUDE_TOOLS. |
| `codeRepo` | Code repository path; the workspace can keep plans in a different folder. |
| `colour` | The colour of the Project in the main window. |
| `compileCheck` | The command that compiles the code and runs the tests before integration; empty: no compile check. |
| `deleteFinishedWorktrees` | Remove a worktree once its work is integrated and nothing is lost. |
| `deleteIntegratedBranches` | Remove plan branches once they are integrated and nothing is lost. |
| `deployCommand` | The command that deploys the product after the push, run in the worktree of the plan; empty: no deploy. |
| `escalateAfter` | Failed attempts trigger diagnosis before another attempt or a more expensive model is authorized. |
| `ignoredBuildOutput` | Files the build writes that do not count as changes. |
| `integrationBranch` | The branch finished plans are merged into; empty: the default branch of the remote, else main. |
| `language` | Language of the interface and owner-facing text; protocol words remain unchanged. |
| `minFreeRamGB` | Minimum free memory required for a start; defaults to ramReserveMb / 1024. |
| `name` | The name of the Project in HaiveQueen; new worktree names start with it. |
| `nextVersion` | The version of the next release, for example 2.1.0. |
| `onEvent` | Command after each event |
| `onEvent.command` | A program started after each event, for example to sync files. |
| `onEvent.timeoutSeconds` | The command is stopped after this many seconds. |
| `onEvent.workDir` | The folder the command runs in. |
| `owner` | How the Queens and Agents call you in their texts. |
| `ownerCheckText` | The question HaiveQueen asks you when a plan is integrated. |
| `ownerTopics` | Questions about these topics are never answered automatically. |
| `planExclude` | Files matching a plan pattern that are not plans. |
| `planGlobs` | Where the plans are found, relative to the folder with the settings. |
| `plans` | Where the plans are found, relative to the plans folder; a line starting with ! excludes files. |
| `plansFolder` | The folder that holds the plans and the settings of the Project; empty: the repository. |
| `priority` | Projects with a higher number get free places first. |
| `prompts` | Own prompt texts |
| `prompts.integrator` | Legacy field; integration is controlled by the Orchestrator, not a Queen. |
| `protectedWorktrees` | Worktrees that are never removed automatically. |
| `publishReleases` | Publish a GitHub release of the repository at each integration. |
| `remoteName` | The Git remote HaiveQueen pushes to and fetches from; choose it because the repository has several and none is named origin. |
| `repository` | The Git repository of the code; releases, version, branches and worktrees always use it. |
| `rulesOff` | List of project/upstream rule entries disabled in Knowledge base. |
| `upstreamRepo` | Optional upstream repository folder; takes precedence over the upstream Git remote. |
| `upstreamRules` | Reads AGENTS.md and CLAUDE.md of the Git remote named upstream (last fetch) or of the upstream folder. |
| `views` | Generated overviews |
| `views.decisions` | A Markdown file that shows the decisions. |
| `views.questions` | A Markdown file that shows the open questions. |
| `views.queue` | A Markdown file that shows the queue. |
| `worktreeLocation` | The folder that holds the worktrees of the Agents; a relative path counts from the repository. |

## Provider fields and actions

Labels such as `login`, `benchmark` and `test` represent actions or status, not stored scalar configuration. `providerKey` is the API-key form label; the stored key is `apiKey`. `light`, `medium` and `heavy` edit entries in `classes`.

| Key | Meaning |
| --- | --- |
| `access` | How this provider reaches its models: with the login of a subscription, with an API key, or with a local LLM of your own. |
| `address` | Where the local LLM answers, for example http://192.168.1.20:1234, without /v1. |
| `allowedTools` | Commands agents may run without asking, one per line. |
| `apiKey` | The key of the local LLM; optional, shown masked and kept only in config.toml of this machine. |
| `args` | The arguments HaiveQueen gives to the CLI, one per line; {model}, {effort} and the like are filled in at the start. |
| `beeMode` | Subagent is available for every provider: the Queen runs Agents inside her process when supported, otherwise as separate processes; Process: every Agent is a separate process and goes to the provider with the best reserve. |
| `benchmark` | Measures how much context an agent of this provider can use; only for a saved provider that is ready. |
| `capabilities` | What this provider may be used for; a task only goes to a provider that has the capability it needs. |
| `capabilitiesVersion` | Set 1 to make the explicit capabilities list authoritative; older capability names migrate. |
| `capabilityThresholds` | Per-capability used-quota percentages, integers 1 through 100; default 100. |
| `classes` | Tables for light, medium and heavy with model, effort and optional contextLimitTokens/customModel. |
| `claudeTools` | Comma-separated Claude tool names; defaults come from config.DEFAULT_CLAUDE_TOOLS. |
| `cli` | The program HaiveQueen starts for the agents of this provider. |
| `command` | The command that starts your own CLI. |
| `contextLimit` | The context size in tokens at which an agent of this provider is asked to hand off; a limit of a model entry wins. |
| `contextWindow` | How many tokens the model of the local LLM can hold; empty means nothing is handed to the CLI. |
| `enabled` | A disabled provider gets no new work and shows nowhere in the dashboard; its settings stay. |
| `heavy` | Model and effort for the hardest tasks; leave the model empty and this provider does not offer this class. |
| `light` | Model and effort for small tasks; leave the model empty and this provider does not offer this class. |
| `limit` | Usage-refresh configuration. |
| `limit.interval` | Usage refresh interval in seconds; default 60. |
| `login` | The login of the HaiveQueen profile of this CLI; use the Login button when it is missing. |
| `loginCommand` | The command that makes the login of your own CLI. |
| `loginFiles` | The login files of your own CLI, relative to its profile folder, one per line. |
| `medium` | Model and effort for normal tasks; leave the model empty and this provider does not offer this class. |
| `name` | How this provider is called in HaiveQueen; it follows the CLI and the access type until you change it yourself. |
| `nameEdited` | Internal flag preserving a manually chosen provider name. |
| `outputFormat` | Which output format your own CLI writes: claude, codex or opencode. |
| `own` | Other settings of this provider exactly as they stand in config.toml; a new setting is written as typed. |
| `profileEnv` | Variables that point your own CLI to its profile folder; {home} stands for that folder. |
| `protocol` | How HaiveQueen talks to your own CLI and reads its output: claude, codex, opencode or kilo. |
| `providerKey` | Your API key; shown masked, kept only in config.toml of this machine and handed only to the agents HaiveQueen starts. |
| `server` | Legacy server reference migrated to inline provider connection fields. |
| `slots` | How many agents of this provider may work at the same time. |
| `test` | Checks the address, reads the model list and tests the interface each CLI needs; the result is kept. |
| `use` | A preferred provider gets work first; a fallback provider only when the preferred ones are busy or not available. |

## Nested resources, retention, updates and web settings

| Key | Meaning |
| --- | --- |
| `buildJobs.cores` | Total CPU core budget across checks and builds. |
| `buildJobs.parallel` | Targets built concurrently; each shares the available cores. |
| `buildJobs.minFreeRamGB` | Free RAM required before starting a build. |
| `retention.logsDays` | Log retention, default 30 days. |
| `retention.runsDays` | Ended run-directory retention, default 30 days. |
| `retention.databaseDays` | Historical database retention, default 90 days. |
| `retention.inboxDays` | Processed inbox retention, default 30 days. |
| `update.checkHours` | Automatic release-check interval, default 24 hours; 0 disables it. |
| `update.repository` | Optional owner/repository override; empty derives origin. |
| `web.address` | Listener address; default 0.0.0.0, restricted to loopback when network is off. |
| `web.port` | Listener port, default 8484. |
| `web.network` | Enable remote network access; initially false. |
| `web.username` | Web-account name. |
| `web.password` | Write-only password input; the server stores a password hash. |
| `web.certificate` | TLS certificate file. |
| `web.key` | TLS private-key file. |
| `web.trustedProxies` | Trusted proxy IP addresses; empty trusts local/private addresses. See web guide for forwarded HTTPS handling. |
| `classes.model` | Model identifier for the class. |
| `classes.effort` | Provider-specific reasoning effort. |
| `classes.contextLimitTokens` | Per-model handoff threshold, overriding provider and general limits. |
| `classes.customModel` | True for an explicitly entered model outside the detected catalog. |

## Git connections

The Git page supports GitHub, GitLab, Gitea, Bitbucket and Azure connections. Credentials are saved separately from project TOML and are selected per connection. A project uses its chosen remote and repository for fetching, pushing and release operations; configure the remote in project settings when it cannot be inferred.

## Execution and advanced project controls

Choose Local, Git or Custom in Workflow and checks. Templates change the draft only after Apply template; review and save the resulting switches and modes. Local execution requires a nonempty command (or release folder) for each enabled step. Git execution requires a connected Git provider offering the operation; unsupported operations wait with a reason instead of silently switching modes.

| Key | Meaning |
| --- | --- |
| `wayOfWorking` | `local`, `git` or `custom`; selects a consistent execution template. |
| `mergeMode` | Local merge/push or Git-provider pull-request integration. |
| `checkMode` | Local compile command or Git-provider check workflow. |
| `buildMode` | Local build command or Git-provider build workflow. |
| `deployMode` | Local deploy command or Git-provider deploy workflow. |
| `releaseMode` | Local asset upload or Git-provider tag/workflow publication. |
| `checkEnabled` | Enable the Check step; new explicit configurations default off. |
| `buildEnabled` | Enable the Build step. |
| `deployEnabled` | Enable the Deploy step. |
| `releaseEnabled` | Enable the durable Release step before owner acceptance. |
| `releaseFolder` | Local release assets directory, required for enabled local releases. |
| `releasePostfix` | Prerelease suffix, default `dev`; alphanumeric components separated by hyphens. |
| `serialIntegrationBuild` | Wait for earlier integrations before beginning Build. |
| `readyCheckClass` | Independent completeness-check class, default `medium`. |
| `readyCheckTimeoutSec` | Independent check wait budget, default 3600 seconds. |
| `agentStallSeconds` | Silent-agent warning threshold, default 3600 seconds; 0 disables it. A warning never terminates the agent. |

Legacy project files without `wayOfWorking` derive enabled steps from their existing command fields and publication setting. New explicit configurations use the switches above. `nextVersion` selects a newer full-version target for prereleases; see the release guide for numbering and initial-version handling.
