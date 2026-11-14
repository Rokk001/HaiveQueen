# Maintenance delivery inventory

Verified against local and remote main at `983e53216a6fc66cbe3ef6cbce75cb476ffdceb9`.
This inventory records delivered changes, not a claim that the remaining audit is complete.

## Delivered work

| Request | Integrated evidence |
|---|---|
| Responsive branded dashboard, settings navigation, question badge, compact limits and system panels | `bc39e1b`, `6fb421e` |
| Plan filters, entity inspectors and Queen/agent hierarchy | `bc39e1b`, `ac693d7`, `4db1272` |
| One view with grouped plan processes; compact missing-value icons and project association | `4db1272`; `web-client.js` grouping renderer and `web_hives_groups_test.cjs` |
| Queue lists only unstarted queued/draft/submitted plans, without integration duplicates | `4db1272`; `test_queue_overview.py` |
| Queue entries open full plan details | `73ac65a` |
| Plan package names, individual expandable steps, readable plan text and technical details | `ac693d7`, `c8c3124`; `web-client.js` package-substeps renderer |
| Consistent clickable integration stages and running/waiting/failure states | `ac693d7`, `030d417` |
| Live integration journals and referenced log files, including historical runs | `030d417`, `d7c9f7b` |
| Keep long failure output inside the log instead of the status heading | `1ac08af` |
| ODP build/check streaming before command completion | OpenDungeonsPlus `0d7b3bd9ab45119c7a9c82b801aaf2470c2bedf4`, verified ancestor of remote integration/all |
| Login/setup styling, larger transparent emblem, header/favicon/tray branding | `c9ad23e`, `a9b3c8a`, `ffef090`, `9d22666` |
| LAN HTTP and HTTPS reverse-proxy login | `c9ad23e`, `4db1272`; trusted-peer handling and LAN regression coverage |
| Accurate quota display, explanatory tooltips and ten-segment usage bars | `c9ad23e`, `c8c3124`; `.model-limit progress` mask in `web-client.css` |
| Access-aware provider add/edit forms and capability thresholds | `c9ad23e`, `301ce05`, `9a842c1` |
| Distinct provider/login status presentation and mobile layout | `6fb421e` |
| CI/CD release/provider views and copy buttons for output/textareas | `cab4f17`, `55a2199`, `6fb421e` |
| Workflow toggles, conditional command fields and keyboard-accessible execution choices | `6fb421e`, `983e532`; `web_workflow_controls_test.cjs` |
| Agent startup no longer reserves build CPU cores | `8a774bc` |
| Retry question matching and one-time historical approval reconciliation | `24b47b0`, `4db1272` |
| Entire next-fix findings list disposition, including confirmed functional decisions | `4db1272`; see `maintenance-findings-2026-11.md` for all 25 entries |
| Web credential masking, bounded login work and trusted proxy policy | `4db1272` |
| A-01: credentials cannot follow cross-origin API redirects | `c567b1c` |
| A-02: inbox answers cannot cross project boundaries | `d87bb66` |
| A-03: persisted process identities verified before stopping work | `95dd61b` |
| Reported CI portability/fixture/translation failures | `1d51b08`, `4db1272`, `d6a3a73` |

## Validation and boundaries

- Only focused regression tests were run; no full suite, full compile check or build.
- The real isolated browser interaction test passed on `983e532`, including entity details, settings, questions, live output, copy/update dialogs and language changes. It used mocked services, not production actions.
- ODP's focused fake-command streaming regression passed during this closeout: output is observed before command exit and retained in the log. No compiler was started.
- Remote main inclusion is distinct from a green complete CI run; this inventory does not claim that every CI job has finished successfully.
- OAuth application registration remains deliberately deferred. The historical provider-slot incident remains inconclusive, and exceeding the soft context limit during handoff is intended. These are explicitly recorded in the findings disposition, not silently marked fixed.
- A-04 and subsequent audit work are not included in this delivery.
- Production rollout and worktree cleanup are operational steps reported separately; a merged commit does not imply that an already running process has loaded it.

## A-08 verification follow-up

A-08 is resolved by the login budget introduced in `4db1272`. In
`haivequeen/web_server.py`, `begin_login` limits password hashing to two
concurrent requests and five accepted attempts in a rolling minute, before
`web_auth.verify` runs. Excess requests receive HTTP 429 with a retry explanation;
`end_login` releases the concurrency slot in a `finally` block.

Focused regressions in `test_web_security_findings.SecurityRoutes` verify the
rate limit before hashing, two concurrent requests with a rejected third,
released slots, budget expiry, and normal HTTP login. All four targeted tests
passed. No production process was restarted or changed for this verification.
