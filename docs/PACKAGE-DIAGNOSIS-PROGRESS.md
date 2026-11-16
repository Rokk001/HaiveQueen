# Package diagnosis without repeated no-progress repairs

Package retries remain single-use and bound to the commit, attempt history and
active decisions. Previously, changing the wording of a diagnosis could authorize
another identical repair at the same commit after an unsuccessful attempt.

When code and decisions have not changed after an authorized repair, the next
technical diagnosis must investigate why it made no progress and perform one
narrow, safe experiment. It must preserve project files and running processes.
The package-specific prompt requests an `experiment` object containing `question`,
`command`, integer `exit`, `finding` and a relative `log` filename. The nonempty
output file must exist inside the diagnosis run directory and be at most 4 MiB.
The diagnosis must explain how the observation changes the bounded repair and
its focused verification. The repair agent receives the finding and log reference.

HQ rejects missing evidence and previously used output bytes at the same commit
and decision scope, even with a new filename or different prose. Reproducing the
same output on a new commit is valid evidence that a defect persists. A fresh observation can justify the same repair
approach only with the follow-up assessment described below. New attempt records alone do not count as progress. Consumed scopes
and evidence digests persist across restarts; legacy accepted reports retain
protection. Changed code or decisions allow a fresh diagnosis under the existing
scope checks. Real product decisions or external prerequisites remain eligible
for owner questions; exhausted attempts alone are not an owner decision.

The last accepted technical diagnosis stores its own `accepted_scope`, separate
from the running review's `scope`. Starting or restarting a review must not move
that comparison baseline. A repair commit therefore permits a new diagnosis
without being misclassified as an unchanged retry during result collection.
For older records without this field, the retained accepted report supplies the
commit; the running review's commit is never used as evidence of a prior grant.

Experiment fingerprints persist with their commit and decision scope. Legacy
unscoped hashes protect the last accepted commit, never an unrelated newer commit;
attempt history is preserved. Repair instructions prioritize the diagnosed
boundary and its failing-before regression over a broader package task. A follow-up
diagnosis compares the repair result with that objective and reuses findings.
Supporting fixture changes must not be represented as completion of a different
diagnosed defect. These instructions guide the agents; HQ does not claim to verify
the semantic adequacy of a repair from a commit or output hash alone.

This is an evidence gate, not proof that a model's causal interpretation is
correct. HQ validates the output file and its novelty; the diagnosis agent must
evaluate its meaning. Existing bounded protocol correction handles invalid
reports. No counters are reset, model cost is not raised automatically, and a
diagnosis is not treated as successful implementation or verification.

Only the retry needing this investigation receives the additional instructions.
Existing logs and history stay referenced rather than copied into every prompt.

## Repair continuation after an unsuccessful authorized attempt

Previously the gate validated fresh experiment output, but did not require a
structured assessment of the repair that followed the last accepted diagnosis.
The next agent could receive essentially the same broad task, appended to the
Queen's package instructions, even after an attempt without implementation or tests.

Every follow-up technical report now binds `continuation.previous` to the digest
of the retained diagnosis and `continuation.attempt` to the latest recorded attempt.
It distinguishes `no_progress` from `partial`, explains why that attempt stopped,
and supplies a specific `target`, bounded `change` and focused `verification`.
Missing or stale assessments cannot authorize another repair, even on a new commit.
Target/change/class fingerprints persist across restarts and incidental commits;
repeating an already authorized action with fresh log bytes is rejected. A class
change still requires the existing diagnosis justification and configured class.

The follow-up task and steps replace broad Queen-supplied instructions. No failure
counter is reset, no package is marked complete, and no larger model is selected
automatically. The existing bounded report-correction path handles invalid reports;
genuine product or external prerequisites retain their existing question path.
Unrelated packages remain runnable.

Only follow-up diagnoses load `roles/package-continuation.md`, through
`package_diagnosis.prompt` and `roles.role_text`. Queens and ordinary Agents do not
receive this protocol text. Repair agents receive the resulting bounded task,
with evidence references, rather than a copied investigation history.

Regression coverage includes fresh output without assessment, stale attempt IDs,
incidental commits with repeated actions, and a narrower repair reaching a single-use
grant. Structural validation cannot prove that differently worded proposals are
semantically different or that an agent will implement them successfully; that
judgment remains the diagnosis agent's responsibility and completion still needs
verification. This change does not claim to complete any live project plan.

## Executable contracts and repair-level progress

Technical package diagnoses now include one authoritative `contract`: `objective`,
`why_executable`, `prerequisites`, `requires`, `resolves`, `criteria` and `repair`. Each
prerequisite has a stable `id`, `status` (verified/unresolved), `reason`, and, when
verified, `evidence` indexes into the report's evidence list. Required prerequisites
must be verified. A step cannot assume the prerequisite it is assigned to establish;
unresolved prerequisites cannot disappear from subsequent contracts. An independently
executable behavior-changing repair can proceed while the larger implementation stays blocked.
Investigation probes belong in the diagnosis, not another delegated repair task.
The model evaluates evidence and feasibility; Python checks dependency consistency,
not the truth of arbitrary prose.

Every criterion has a unique `id`, `expectation`, and focused `check` command.
HQ delivers the complete next_action, continuation assessment and contract, including
safety and cleanup conditions. A shortened continuation no longer replaces the full
instruction. The contract digest and actual attempt identity are stored at Agent
start. Legacy grants without this protocol cannot launch a repair: they need a new
diagnosis, with history preserved.

After reviewing a completed step, the Queen submits `agent result --outcome progress
--repair-result <file>` with `attempt`, `contract` digest, current `commit`, and
`checks`. Each criterion occurs exactly once with its prescribed command, actual
zero `exit`, and relative `log`. Nonempty logs must reside beneath the receipt's
directory; their hashes are retained. Invalid receipts do not release Agent slots
or modify failure state. HQ validates reported evidence rather than independently
rerunning it: Agents must not fabricate results and the Queen must review them.

Progress leaves the package open and preserves failure counters without incrementing
them. The receipt survives restart. The next diagnosis receives that verified progress
and selects the next behavior-changing repair using retained findings. Remaining package requirements are not
a failure of the completed step. A changed implementation commit is required; an unchanged commit or a test-only characterization
cannot qualify as a new repair. Later genuine failures still enter
diagnosis without erasing earlier progress; replayed receipts cannot close another
attempt. Normal package success still requires the complete package's verification.

While diagnosis or an owner prerequisite is pending, result responses return
`nextAction: wait_for_diagnosis` or `wait_for_owner`, without a contradictory
`nextClass`. Class changes still require a reasoned diagnosis and configured model.

Focused flow tests cover blocked prerequisites, independent repair dispatch, complete
cleanup-criterion handoff, receipt validation, restart persistence, independent work,
next-step dispatch followed by another genuine failure, and stale grants and receipts.
They operate on isolated fixtures, not live plans or processes.


Package diagnosis reports are bound by HQ to their scheduled output path and evidence
scope. The opaque failure-history identity is supplied by HQ, not transcribed by the
model. Only a running, persisted binding with the same current scope permits this;
legacy or foreign reports still require an exact identity. The reported commit and
reviewed decisions remain mandatory and validated. Changed attempts or decisions
invalidate the binding. The original report is retained unchanged for inspection.

Experiment logs may use relative or absolute paths resolving inside the diagnosis
run directory. Outside paths (including resolved links), missing or empty logs and
non-integer exit codes remain invalid. Evidence reuse guards and one-use repair
permissions are unchanged. A protocol correction receives the prior report path
and reuses valid observations instead of repeating a probe just to repair JSON.
This does not grant a repair retry or complete any package by itself.

## Preventing investigation chains (protocol 3)

Previously a contract could ask only for characterization of a defect. Its green
checks counted as progress and automatically started another diagnosis, even while
the original defect remained unchanged. New commits and differently named probes
passed the action novelty guard. The missing distinction was implementation versus
observation, not the number of failed attempts.

`contract.repair` now names a stable `goal`, corrected `behavior`, a positive
`criterion` ID, and explicit, separate `implementation_paths` and `regression_paths`.
The goal cannot drift into a new question in a follow-up. Diagnosis must perform
necessary safe probes itself and then prescribe an executable implementation;
a new observation alone is not a delegated repair contract. Genuine external
prerequisites keep their existing owner-question path.

At dispatch HQ records the starting commit. A progress receipt additionally supplies
`baseline: {commit, command, exit, log, regression_files: {repository_path: snapshot}}`.
The final positive regression is run on the starting implementation before modifying
it, and exact regression file copies and the nonzero baseline log are retained beside
the receipt. HQ requires the same command to pass, a descendant commit changing at
least one declared implementation file, and regression snapshots matching committed
files using Git's path-specific normalization. Altering assertions to accept the
observed defect cannot count as a repair. No extra model calls or full test suites
are introduced. Exit codes remain reported evidence reviewed by the Queen; this is
not an independent execution of every supplied command or a semantic proof of a test.

Protocol-2 grants cannot start new repairs. In-flight legacy receipts are preserved
as `observations`; the response reports `observed`, without resetting failures or
closing the package. Historical receipts without implementation evidence also no
longer justify `step_complete` or bypass the unchanged-code guard. Their evidence
remains referenced for the next diagnosis. No active process is stopped by migration.

Regression coverage includes a real failing check, Git implementation commit and
passing identical check through authorization, result recording and next-step dispatch;
five renamed investigation proposals with incidental commits; changed goals; test-only
commits; changed assertions plus incidental implementation edits; invalid baselines;
legacy receipts and grants; replay protection and persisted progress after restart.

## Full-workflow progress (protocol 4)

Protocol 3 proved local implementation changes but could still accept an endless
series of unused components. The new contract includes `workflow: {entrypoint,
command, checkpoints, regression_paths}` and `repair.advances`. The initial diagnosis
must define a focused real-path check covering the required complete flow, including
failure/recovery, against existing requirements. It must exercise the repaired boundary
rather than replace it with a mock. No new requirements are invented.

The workflow specification is persisted independently of individual reports. Follow-ups
cannot replace its entrypoint, command or checkpoint set with a new component test,
even after restart or an intervening nontechnical report. Selected advances must
include an unsatisfied workflow checkpoint. Existing requirement and safety checks
remain mandatory; workflow completion alone does not automatically close a package.

A receipt adds `workflow: {command, before, after}`. Both files are local JSON execution
summaries: `{checks: {checkpoint: "passed"|"failed"}, exit: integer}` covering the fixed
checkpoint set, with zero exit only when all passed. Detailed execution output is kept
beside these summaries for Queen review. HQ requires a selected failing checkpoint
to become passing, preserves every prior passing checkpoint, and retains the hashes
of the workflow regressions between rounds. Tests may not be rewritten to move the
goalposts. Unrelated remaining failures are allowed only with that demonstrated progress.

The initial workflow's relevance and the truth of reported execution remain review
responsibilities; schema validation is not semantic proof. The gate mechanically rejects
unchanged workflow results despite green helper tests, new names or incidental commits.
No full project suite or extra model invocation is introduced by this validation.

Protocol-3 grants are not reused. In-flight older results and historical local-only
receipts remain evidence but cannot claim verified workflow progress. Failure counters,
logs and active processes are preserved. Tests cover an actual unused component with
a passing unit check and still-failing caller flow, real integration success, changed
workflow definitions/assertions, regressed checkpoints, satisfied-checkpoint recycling,
legacy grants/receipts and persistence across restart.
