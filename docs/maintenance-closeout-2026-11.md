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
## A-10 login input isolation

Resolved: login input is written by one bounded pending writer per session, outside
 the shared session lock. Requests stop waiting after two seconds; repeated input
is rejected while a write remains pending. Status and shutdown remain available.
The regression reproduces a non-reading pipe and verifies bounded request time,
status access, duplicate rejection and shutdown. Existing interactive login tests
also pass. No production process was restarted.

## A-25 journal failure isolation

Resolved: journal write errors no longer escape into output-draining threads.
A bounded in-memory tail (128 records, at most 4096 characters per output record,
16 failed journals) preserves recent output and terminal status for the process.
Both returned output and the diagnostic logger identify storage failure and the
non-durable fallback. A real subprocess emitting several megabytes still completes
with its original exit code when journal writes fail. Completion-recovery and
existing live/stall output regressions also pass. Full disk failure can still
prevent durable receipts; this fix does not claim persistence without storage.

## A-11 update state transactions

Resolved: state mutations use a process/thread lock and unique atomic temporary
files. Network lookups stay outside the lock and merge only check-owned fields;
older results cannot overwrite newer checks. Install start and settlement update
the latest state under the same lock. Both race regressions failed before the fix;
14 focused update state, check, header and install tests pass.

## A-12 release origin validation

Resolved: checked offers retain their source repository. Install requests and the
worker reject configured or offered repositories that differ from the installation
origin before acquiring the update lock or stopping processes. HTTPS and SSH
origins normalize to the same repository. Two regressions failed before the fix;
27 focused update tests and four origin tests pass. No update worker was started.

## A-13 atomic question answers

Resolved: ordinary answers validate every rejected package against the question's
plan before writing. Answer, decision, events and all package changes share one
transaction. A later write failure rolls everything back. Owner-check answers
remain notes and do not reject packages. Regressions reproduced both invalid IDs
and partial writes before the fix; focused answer and terminology tests pass.

## Merge queue: preserve checks after release failure

The merge-queue requirements explicitly retain speculative Merge, Check and Build,
ordered publication, and replay after a predecessor fails before Push. The CI/CD
requirements add Release after publication; a failed release cannot remove code
already pushed. Only Merge, Check, Build and Push failures invalidate followers.
The regression reproduced a second successor Check after an upload failure, then
verifies that completed checks and builds survive through successor publication.
This does not eliminate required replay after pre-push failures or change the
configured concurrency. Existing Deploy and pre-push replay cases remain covered.

## A-14 atomic owner decisions

Resolved: direct Accept and Reject stage plan files and commit all database changes
with a durable recovery receipt. Inbox decisions join their existing batch.
Missing files and late database failures preserve the original state; interrupted
publication recovers on the next database open without duplicating the decision.
The regression failed before the fix. All 47 focused owner and inbox tests pass,
including subprocess exits immediately before and after the database commit.

## Windows console test isolation

Run 43603154526 failed on both Windows versions because two console fixtures
created completion receipts in the fallback data directory. Console drivers now
receive dedicated data, config and log directories; standalone runs also load the
isolation guard. The two affected tests reproduced the exact leak before the fix.
Nine focused console, receipt-lifetime and isolation tests pass after the fix.

## Integration repair evidence

Action journals record the tested commit, command, worktree and speculative
predecessors. Worker cancellation is recorded as cancellation rather than a
project failure. Repair history retains failures, agent reports, before/after
commits and explicit retry reasons. Empty commits and ended agents without a
committed tree change or changed command do not rerun the unchanged pipeline.
Existing attempt limits and owner-stop handling remain in effect.

Five regressions failed before the corresponding changes and pass afterwards,
including a second failure after the first repair and an integration verification
record only after all pipeline steps complete. Eight existing fix and owner-reject
tests also pass. This is the evidence foundation, not completion of cross-plan
cause coordination or machine-verified targeted repair checks; those remain open.

## Targeted repair verification

Fix agents now supply a regression command tied to the repaired commit and the
original failure log. HQ executes that command through its durable, resource-bound
check supervisor before retrying the full integration pipeline. Missing or stale
evidence, a failed regression, or a changed tree returns to diagnosis with retained
history. Authentication failures request user action instead of repeated repairs.
Observer recovery consumes the existing result without starting a duplicate check.
Unique agent names are assigned before publishing the verification-file location.

Seven focused regressions cover missing evidence, stale commits, failed and passing
verification, observer recovery, access correction and reused agent names. Existing
fix-agent, owner-check, run-log, queue and provider-pipeline cases exercise the new
contract. The original regressions failed before their corresponding fixes.
Cross-plan common-cause coordination remains open. HQ validates execution and
commit identity; the supplied explanation still requires the repair agent to choose
a regression that actually covers the original failure.

## Shared integration diagnostics

Detailed identical local Check/Build diagnostics for the same command and project
now share one repair leader. Worktree prefixes are normalized; a generic exit or
wrapper message never establishes a shared cause. Followers retain their failure
records and wait without starting duplicate fix agents. Their evidence and repair
history are included in subsequent diagnosis prompts. A second error after the
first repair keeps followers waiting until the leader's full integration succeeds.
Followers then merge the published repair and run their own complete pipeline;
they never inherit a successful check result. A cancelled leader transfers diagnosis
without being marked successful. Independent speculative work remains enabled.

The two-plan regression reproduced duplicate fix agents before the change and
passes with two successive failures, targeted verification and both plans reaching
review. Focused cases cover different diagnostics, generic errors, cancellation,
observer recovery and unrelated speculative work. This matching is deliberately
evidence-based: different diagnostic texts or remote provider failures are not
automatically declared the same root cause. Broader causal diagnosis remains open.

## A-15: Atomic machine settings and project registration

Machine settings saves now register projects and stage the settings file within
the existing durable file/database transaction. A save or journal-preparation
failure leaves both unchanged. A publication failure after the database commit
retains the recovery receipt; the next coordinated read publishes the committed
settings without registering a project twice.

Three regressions failed before the fix and now pass: settings-write failure,
journal-preparation failure and publication recovery. Two focused compatibility
tests also pass for secret preservation and workflow settings round-tripping.

## Speculative queue: access failures

An authentication failure before publication previously released the predecessor's
queue reservation without invalidating speculative successors. They could retain
code from the blocked plan. Access failures at Merge, Check, Build or Push now
use the same durable cancellation/replay path as other pre-publication failures.
Successors merge again without the blocked plan and continue independently.
Access failures after publication keep successor checks intact.

The pre-publication regression failed before this fix. Focused coverage also
checks post-publication access failure, normal failed-check replay, cumulative
parallel merges and checks, and parallel builds with ordered publication.
This does not serialize the queue or make successors wait for the repair.

## Technical escalation: retain history without inheriting exhausted attempts

The integration repair counter previously survived successful repairs unchanged.
A later failure could therefore exhaust the model-class budget before any agent
had investigated that failure. The regression reproduces a repaired Check followed
by a distinct failing Build at the highest offered class.

After targeted verification, HQ now retains the original failed step as pending.
Only that step passing in the normal pipeline completes its attempt budget. Its
previous counter and log remain in repair history. A targeted command passing
while the original check still fails does not reset attempts. Nor is a successful
step recorded as completion of the whole integration.

This fixes one source of premature escalation. Autonomous diagnosis after genuine
attempt exhaustion and evidence-backed owner questions remain under investigation.

## Diagnosis before exhausted integration repairs reach the owner

An unsuccessful repair without changed code requires diagnosis before another
repair or a costlier model. A valid structured diagnosis supplied by the repair
agent is reused without starting an additional agent. Otherwise a diagnosis agent
reviews the failure instead of automatically asking the owner for an instruction.
Its prompt includes the failed-run reference, commit and bounded evidence previews;
existing read-only CLI sources retain the full history and active decisions.
The fix and diagnosis role instructions live beside the Queen and Agent roles in roles/.
The plan is referenced rather than embedded, and no duplicate evidence file is created.
It must report concrete observations and either a justified technical next action
or the specific product decision, permission or external prerequisite it lacks.
The diagnosis class is configurable alongside the Queen class; its model and
effort come from the selected provider's class settings.

A technical diagnosis authorizes one repair without erasing attempt history.
The authorization is tied to the commit, failed run and active decisions, and is
consumed only after an agent starts. Changed evidence requires a fresh diagnosis.
An identical previously authorized approach is rejected rather than retried.
Owner answers to specific diagnosis questions preserve the attempt count too.
Targeted verification and the unchanged full integration pipeline remain required.

Focused regressions cover exhausted attempts, concrete product questions, existing
decisions, stale reports and changes after review, one-use authorization, repeated
approaches, configurable model/effort, malformed class rejection, early diagnosis,
reuse of an existing diagnosis, bounded prompts and successful repair verification. Tests use
isolated repositories and controlled agent reports; they establish orchestration
behavior, not the diagnostic quality of a live model. Package exhaustion and other
question creation paths still need the same pre-escalation review. This is not yet
completion of the broader autonomous technical diagnosis request.


### A-18: respect current decisions during question deduplication

- Confirmed that a historical answer (including a later automatic duplicate reference) displaced a newer active decision.
- Select the latest applicable answer or active decision; automatic duplicate answers do not create new authority. Independent decisions win timestamp ties; mirrored answer records retain the question reference.
- Preserve project/plan scope and mandatory fresh owner checks. Superseded decisions are excluded.
- Regression reproduced before the fix: `test_new_decision_supersedes_old_answer_and_auto_duplicates`. Focused coverage: `test_ask_triage` and `test_gui_questions`.
- This fixes decision lookup, not the still-open package diagnosis workflow.


### CI run 43609606492: repair integration test fixtures

- Reproduced all seven errors across six test methods on current main. Project fixtures omitted the workspace and plan required by execution logging.
- Supply isolated plan files and workspace paths without weakening production logging.
- Update the release invalidation regression to exercise pending and successful targeted repair verification before pipeline replay.
- Focused validation: 19 tests passed across CheckRecoveryTests, CoreAccountingTests, release gates and Build/Deploy process registration. No full suite or build ran.


### Package diagnosis: retrieve existing attempt evidence

- Add read-only `bzz agent history --project PROJECT --plan PLAN --package PACKAGE`: five recent previews by default, `--before` for older pages, `--attempt` for full stored error detail. Maximum page size is 20.
- Scope every query to the requested project, plan and package. Reads do not initialize attempt tables or alter counters. No additional evidence file or dependency is introduced.
- Regression coverage checks scoping, pagination, full detail retrieval, bounded previews, invalid input and absence of database writes. The three initial tests fail without the implementation.
- This is evidence retrieval for the pending package diagnosis workflow, not completion of automatic diagnosis or retry authorization.
