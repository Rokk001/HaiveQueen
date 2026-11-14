# Verified maintenance findings

Review baseline: `24b47b07147d2dd14f047660fa9997845df39196`.
Scope: the next-fix findings list, authorized CI repairs, dashboard consolidation and web/credential security. Production processes and the production database were not changed.

## Findings disposition

| # | Finding | Disposition and evidence |
|---|---|---|
| 1 | Busy-agent total includes Queens | Already implemented: `dashboard_data.snapshot` counts running agents against the machine slot total. |
| 2 | Search processes overload the machine | Limited to the requested orphan-process repair in #9; no search restrictions, priorities or new time limits. |
| 3 | Waiting despite one free provider slot | The current gate accepts one slot: `hives.provider_wait_check`; introduced in `5131d4b`. The historical reported incident cannot be reconstructed conclusively from a later snapshot. The focused resume test passes; no speculative scheduling change. |
| 4 | Context exceeds 100 percent | Confirmed intended soft limit: `routine.set_handoff_flags`, `roles/bee.md`. A current step may finish before handoff. |
| 5 | Cleanup might delete ignored non-build files | Fixed: `worktrees._candidate_reason` calls `_ignored_blocker` with the explicit `ignoredBuildOutput` allowlist. Git ignore rules do not grant deletion permission. Project settings preserve the allowlist. |
| 6 | General settings use Project help and old label | Fixed: General-specific fallback help and Advanced labels in `web_ui.page` and the browser field renderer. |
| 7 | Shared ctypes argument-type race | Not present in the two requested sites: `localtime._windows_format` never changes shared argtypes; `loop.system_boot_time` sets the same primitive result type. No speculative rewrite. New DPAPI code uses private WinDLL objects. |
| 8 | Agent instructed to run full suite | Already fixed in `11d911d`, `roles/bee.md`; current repository instructions also require focused tests only. |
| 9 | Runner exemption protects all leftover children | Fixed: `proctree.is_exempt` stops inheritance at the owning cleanup root. Explicitly exempt nested processes remain protected. PID identity checks remain. |
| 10 | Cut Queen ends without recorded handoff | Fixed: `cli.cmd_plan` records handoff before ending and compares the actual Queen folder with the plan header; a replacement Queen in the right folder does not hand off again. |
| 11 | Integration runtime | Existing `dashboard_data.hive_row` uses integration start, including waits, matching the confirmed requirement. |
| 12 | Branch takeover question for an active plan | Existing `bees.foreign_plan_branch` recognizes active ownership. No additional takeover relaxation. |
| 13 | Dependencies between plans | Implemented: top-level `depends_on` with project-relative plan paths. Missing, cyclic and invalid dependencies block new starts. The latest predecessor queue record must be integrated. Independent plans remain independent; running Queens are not interrupted. |
| 14 | Repeated long integration checks | Check coverage retained. Explicit authentication/permission failures now ask for corrective action and block instead of repeatedly running the pipeline. Answering resumes the failed step. |
| 15 | Plan contradicts integration flow | Existing ready gate keeps integration work after ready. Cut role now requests a specific correction or explicit permission to ignore a conflicting instruction; an open `plan-flow` question blocks `plan check`. |
| 16 | Verdict in report.txt not found | Already fixed in `af8a48c`: `readygate.ANSWER_FILES` includes report.txt and result.txt. |
| 17 | Queen command instructions incomplete | Already fixed in `11a2018`: `agent_runner.report_prompt` includes commands required by the role. |
| 18 | Provider CLI updates invisible | Implemented read-only version notices and manual refresh, cached in background. No installer is called. Unsupported/unreachable checks are not shown as up to date. |
| 19 | MCP configuration absent from HQ profile | Implemented confirmed import into HaiveQueen's provider profile, followed by initialization and tool discovery. No generated setup instructions or server catalog. |
| 20 | Capability checkbox without tool evidence | External capabilities require confirmed configuration and advertised required tools. Changed or failed configuration invalidates evidence. Native coding/build/deploy/Queen and confidentiality semantics remain. |
| 21 | HTTPS reverse proxy login | Existing external-scheme origin handling retained and restricted to trusted peers. Defaults trust loopback, RFC1918, IPv6 ULA and link-local peers; explicit IPs restrict this. Direct LAN HTTP remains supported. |
| 22 | Git provider OAuth application registration | Deliberately deferred as specified; no new application registration or OAuth scope expansion. |
| 23 | Push scope failure restarts Merge/Check | Fixed: explicit auth rejection blocks Push and asks for credentials/permissions correction. Previously completed steps remain completed; no extra fetch/remerge after the rejection. |
| 24 | Historical recovery approval missed | Current text matching already fixed in `24b47b0`. Added conservative one-time reconciliation using attempt end times and consumed-answer records. Later failures and attempt history survive; ambiguous timestamps do not grant retries. |
| 25 | CPU field describes per-build rather than total | Renamed to Total CPU budget for builds and checks. Shared budget behavior retained. |

## Additional authorized fixes

- CI: remove obsolete tmux smoke job; canonicalize Windows temporary roots to avoid 8.3/long-path mismatches; accept regional milestone times with spaces; wait correctly when browser actions replace their button.
- Dashboard: one Hives section groups each plan with HQ, Queen and agent processes. The project is shown once on the plan. Child groups can collapse. The queue lists only unstarted queued/submitted/draft plans, without integration duplicates. Missing model, context and Queen values use compact accessible icons with hover text.
- Login: bounded attempts and concurrent password hashing; generic unexpected API error responses.
- Settings: provider API keys are masked in reads and preserved during unrelated saves, including legacy configurations. Explicit clearing remains possible; renamed masked entries require re-entry.
- Git credentials: Windows writes use user-bound DPAPI; legacy files remain readable and migrate at the next normal save. Existing production files were not rewritten. POSIX writes set owner-only permissions before publishing the replacement.

## Configuration

A plan header may contain `depends_on = ["plans/preceding-plan.md"]` before package tables. Paths belong to the same project. This is separate from package dependencies.

General / Web server / Trusted proxy IP addresses accepts explicit IPv4 or IPv6 addresses, one per line. An empty list uses the private/local defaults; it does not mean trust the public Internet. Forwarded host headers are not used to relax same-origin checks. Private-network clients are trusted under this selected default; an explicit list narrows that trust.

Provider Tool connections accepts an owner-supplied object with `mcpServers` and capability bindings, for example a binding with `server` and a nonempty `tools` list. Import and verify requires confirmation before executing the supplied command or contacting the server. Only discovery is performed, not tool execution. Supported CLI profile formats are Claude Code, Codex, OpenCode and Kilo.

## Focused validation and limits

Regression coverage is in `test_findings_process_cleanup`, `test_k14_release_retry`, `test_findings_auth_wait`, `test_plan_dependencies_findings`, `test_cli_updates_findings`, `test_mcp_setup_findings`, `test_web_security_findings`, `test_queue_overview`, `test_findings_ui`, and `test_ci_findings`, plus the directly affected existing tests. Browser state/grouping/icon tests run under Node. An actual Python stdio MCP fixture verifies handshake and paginated tools. Windows DPAPI round-trip and non-plaintext storage are exercised.

Only targeted tests were run. No build, full suite or full compile check. The optional Chromium browser test was skipped because its executable was unavailable; this is not a claim of visual browser verification. Historical runtime evidence was read from a database copy only. No production process was restarted and no live agent was stopped.

Protocol references: [OpenCode installation](https://opencode.ai/docs), [Kilo MCP](https://kilo.ai/docs/automate/mcp/using-in-cli).
