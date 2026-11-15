# Documentation map and migration evidence

This index records where the former long README's details now live. The README presents the product and command summaries; topic guides own the detailed behavior.

| Former README section or detail | Canonical destination / disposition |
| --- | --- |
| Why: lean profiles, logins, CLI configuration and restrictions | [Profile](PROFILE.md); absolute private-file guarantees corrected to a lean configuration profile, not a sandbox |
| Why: checked-in project and upstream rules, role templates, startup context | [Agent rules](AGENT-RULES.md) and [roles](../roles) |
| Why: Queen delegation, independent completeness check, cutter | [Plans](PLANS.md) and the authoritative gate in Agent rules |
| Why: cleanup locking, recovery and loop responsiveness | [Operations](OPERATIONS.md#scheduling-lock-and-cleanup) |
| Why: language, project-wide answers and decisions | [Settings](SETTINGS.md), [Inbox](INBOX.md) |
| Words | [Operations roles](OPERATIONS.md#roles) |
| How it works: submit and cut | [Plans](PLANS.md); contradictory direct-QUEUED/no-submit guidance replaced with SENT as standard |
| How it works: slots, capabilities, quota, handoffs | [Providers](PROVIDERS.md) |
| How it works: integration, no-change closure and owner acceptance | [Operations](OPERATIONS.md#integration); Queen-integration claim removed because the Orchestrator owns it |
| Package states and plan header example | [Plans](PLANS.md); example now includes required branch, worktree and requirement mappings |
| Quick start, installation location and pinned release | [Getting started](GETTING-STARTED.md); README retains a short installation entry point |
| Manual Windows/Linux subscription login commands | [Profile manual login](PROFILE.md#manual-subscription-login) |
| Updates and self-update transition | [Operations](OPERATIONS.md#updates), [Commands](COMMANDS.md) |
| Setting up a provider; API key and Local LLM | [Providers](PROVIDERS.md), protocol-specific implementation in Profile |
| Plans and plan menu | [Plans](PLANS.md) |
| Failures and escalation | [Operations diagnosis](OPERATIONS.md#failures-and-diagnosis); automatic escalation corrected to evidence-bound diagnosis and one scoped retry |
| Dashboard, controls at all three levels, live output | [Web guide](WEB-API.md); former desktop images removed, current screenshot stays in README |
| Configuration, migration, deletion guards and field values | [Settings](SETTINGS.md); unsafe .gitignore cleanup claim corrected to explicit ignoredBuildOutput allowlist |
| Leftover processes, reload, status reconciliation, crash recovery | [Operations](OPERATIONS.md) |
| Release publication | Operations Releases; old full-release-per-integration description replaced with durable prereleases and explicit owner promotion, matching publish.py and web_releases.py |
| Inbox and owner messages | [Inbox](INBOX.md) |
| Command line | [Commands](COMMANDS.md); full parser syntax and owner/runtime behavior |
| Good to know | [Operations](OPERATIONS.md#cleanup-process-recovery-and-reload) |
| Development | [Development](DEVELOPMENT.md); standard-library-only claim corrected because tomlkit is a runtime dependency |
| FAQ: model usage, supported agents, failure rules, local state, safe stop, v1 | Operations, Providers, Profile and [v1 migration](MIGRATION-v1.md), instead of repeating answers |
| Post-license capability and limit details | Providers, Settings; provider-required integration claim removed because pipeline commands have no model provider |
| Post-license package worktrees, branch assignment and end audit | [Branch attribution](BRANCH-ATTRIBUTION.md) |
| License | README license line and [LICENSE](../LICENSE) |

## Current feature coverage

The guides above cover setup and updates; project discovery and migration; plan decomposition, dependencies, class/capability routing and verification; scheduling, quota, resource limits, process recovery and diagnosis; integration, releases and owner checks; browser sessions, network/TLS, tray and Linux service; settings, Git connections, provider login/test/benchmark and MCP/profile setup; logs, state export, inbox and low-level runtime commands.

## Retained technical and historical references

[Runtime API](RUNTIME-API.md) describes the JSON state interface. [v1 migration](MIGRATION-v1.md) is for installations moving from PowerShell. [S13/S14 contract](S13-S14-INTERFACE.md), [web parity audit](WEB-PARITY-AUDIT.md), [tray acceptance](TRAY-RUNNER-ACCEPTANCE.md), [maintenance findings](maintenance-findings-2026-11.md) and [maintenance closeout](maintenance-closeout-2026-11.md) retain dated implementation evidence; their historical text does not replace current user guides.

## Maintenance verification

`tests-py/test_end_user_docs.py` checks the current parser paths, settings keys, local topic links, SENT submission and the profile boundary. Source references for corrections: `package_diagnosis.py`, `worktrees.py`, `orchestrator.py`, `profile.py`, `web_ui.py`, `settings.py`, `scheduler.py` and `cli.py` under `haivequeen/`.
