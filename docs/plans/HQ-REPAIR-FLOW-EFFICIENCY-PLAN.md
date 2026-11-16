+++
status = "DRAFT"
+++

# Repair flow: reach verified progress with less repeated work

## Scope and approval

This is an analysis and proposed implementation plan, not authorization to change
the repair scheduler. The source request asks for the last HQ-LIVE-UPDATE execution
to be assessed against token economy first and context conservation second, including
test cost, agent turnover and a route toward a roughly ten-minute repair cycle.
The accompanying CI corrections are separate maintenance. Production plan state,
acceptance and live processes must not be changed by this plan.

## Evidence and limits

Read-only runtime evidence was collected on 2026-11-16 from the agents and events
tables, the last diagnosis validation and milestone files, and retained implementation
reports identifiable by commits `2d70420` and `fa396a9`. The table timestamps are
reported as stored; they are not independently normalized to UTC.

* For this plan on that date: 118 persisted agent rows: 43 Queen, 64 fix, 3 bee,
  8 verify. The `fix` phase includes diagnoses; these are not 64 implemented fixes.
  Embedded Agents may instead be accounted through their parent: these counts are
  not an exhaustive count of provider-native subagents or unique problems.
* From 16:00 in the same stored timestamps: 19 rows, comprising 3 Queen, 11 fix,
  5 verify. Reported input/output totals are 86,939,971 / 338,120 tokens. Whole-day
  totals are 185,232,371 / 810,133. These accumulated counters include repeatedly
  processed context; they do not establish unique context size, billed tokens,
  cache savings or monetary cost.
* The final two lengthy Queen intervals were approximately 40 and 77 minutes.
  They reported about 24.7M and 47.0M input tokens respectively. Their counters
  cannot separate their own reasoning from provider-native child accounting.
* Commit `2d70420` made the downloaded compatible-update scenario pass in 28.030s
  after a 17.481s failing baseline. Commit `fa396a9` made the database drain and
  successor scenario pass in 59.232s after a 33.605s failing baseline. These are
  actual implementation gains, not evidence that every iteration was pointless.
* The latter report records 45 passing focused test executions, but the broad
  17-scenario workflow still failed: baseline 674.348s; final 714.779s with
  13 failures and 6 errors (a scenario can fail and also error during cleanup).
  The report explicitly identified remaining publication-rejection recovery,
  browser/capture/cleanup and non-database coverage gaps. It withheld completion.
* The last diagnosis, recorded at 20:07–20:11, correctly proposed the publication
  rejection recovery already named in that report. Its validation recorded an
  unchanged workflow, an unconsumed action, a 42,306-byte report and no project
  tests executed. This was a useful diagnosis, but another model had to reconstruct
  an already-described next repair.
* CI [43899663135](https://github.com/Rokk001/HaiveQueen/actions/runs/43899663135)
  tested `dcddd2c`, not the subsequent merged update. Python 3.11 ran 3,225 tests
  in 409.590s on Linux and 4,364.864s on Windows, with the same ten reported
  failure/error entries. Several Windows real-process scenarios have tens-of-seconds
  output intervals. Buffered log timestamps do not prove an exact per-test profile
  or a single cause for the approximately 10.7-fold runtime difference.

The manual takeover reused those commits, prior diagnoses and many earlier tests.
Its last roughly ten minutes were integration and publication, not a complete
from-scratch repair and acceptance. The final merge was not retested after the
explicit test stop. Neither that merge nor this comparison proves full acceptance.

## Assessment against the quality goals

The observed overall flow does not meet the intended token/context economy.
There is real code progress, but too much context reconstruction and repeated
coordination around each partial advance. Correct rejection of missing evidence
is necessary; asking another Agent to rediscover a known next step is avoidable.
Heavy routing was already present in a final Queen prompt. There is no evidence
that merely increasing effort or model price would remove these costs.

The current implementation separates `progress` from completed repair, binds
diagnoses to commit/failure/decisions, and independently reviews changed workflows
in `package_diagnosis.advance`, `repair_contract` and `repair_scope`. Preserve those
protections. The missing efficiency is a coherent continuation across these gates:
validated partial implementation, explicit unresolved checks and the existing next
repair must survive without rebuilding the entire diagnostic transaction each time.
Determine precisely which invalidations were necessary before changing any gate.

Testing was partly justified: process ownership, version handover, data migration
and rollback need real lifecycle evidence. It was also inefficient: broad workflows
were repeated while known harness/public-interface failures remained, mixing product
failures with capture, authentication and cleanup failures. The manual session also
spent time correcting these fixtures and rerunning their subsets. New coverage was
added before the existing acceptance was closed. This increased scope and duration.
The remedy is better selection and evidence reuse, not declaring untested paths green.

## Requirements and acceptance

1. **R1 — Establish one measurable baseline.** Reconstruct the final repair episode
   from persisted attempt IDs, commits, verdicts, decisions and command receipts.
   Separate useful code work, coordination, provider waiting, test execution and
   test-harness failure. Explicitly label missing timing/cost data. Acceptance:
   every claimed repeated launch or redundant check points to matching evidence;
   counters are not presented as billing or exclusive Agent context.
2. **R2 — Continue validated work.** Reuse the existing checkout, diagnosis, approved
   workflow and accepted partial results. Dispatch the recorded next repair without
   a fresh diagnosis when its assumptions still hold. Changed failure, relevant
   code, requirements or decisions must invalidate only affected evidence.
   Acceptance: a recorded partial repair with a known next failing checkpoint
   resumes after a core/Queen restart without an unchanged rediscovery cycle;
   stale or unrelated evidence is still rejected.
3. **R3 — Make each restart earn its cost.** Before replacing or escalating an Agent,
   record the actual reason, remaining task and new evidence expected. Distinguish
   a certainly unstarted handoff, malformed report, harness failure and product
   failure. Recover a valid handoff and correct a report through the existing path;
   do not create competing work. Acceptance: replay the observed rejection and
   continuation paths, show fewer duplicate launches, retain attempt history and
   stop/pause, ownership, quota and actual-process protections.
4. **R4 — Reduce reconstruction context.** Give the next Agent the unresolved outcome,
   current commit, concise accepted findings, changed constraints and targeted log
   references. Keep authoritative requirements and rules retrievable and apply the
   existing complete-rule loading contract. Acceptance: measure generated payload
   and retrieved evidence against the baseline; no repeated full historical report
   unless its contents are necessary. Do not silently drop an active decision.
5. **R5 — Use a risk-based verification sequence.** Establish that interpreter,
   authentication, fixture, command selection and output collection work before an
   expensive lifecycle run. Repair the smallest failing checkpoint; reuse successful
   checks only with valid code/environment/artifact provenance. Run the relevant
   combined lifecycle after its blockers are repaired, and repeat only for a
   changed relevant input or unresolved finding. The current progress validator
   requires complete before/after workflow evidence for each accepted advance.
   Preserve that complete evidence: selective reuse is permitted only when all
   checkpoints remain valid for the evaluated inputs. Until this is established,
   the required intermediate full runs remain necessary and must be budgeted.
   Acceptance: every execution has a
   reason, scope, commit, duration, result and coverage; unchanged expensive repeats
   disappear, while mandatory checks and data/rollback guarantees remain.
6. **R6 — Diagnose slow tests rather than waiting blindly.** Attribute Windows test
   cost to fixture setup, child process startup, intentional timeout and cleanup.
   Keep real process tests for lifecycle boundaries; use deterministic checks for
   pure state transitions. Polls need bounded deadlines and useful failure receipts.
   Acceptance: compare the same scenarios/environment, preserve assertions and
   resources, and show where runtime decreased. Do not infer a faster product from
   shorter timeouts or skip the failing cases.
7. **R7 — Demonstrate the whole continuation.** Replay diagnosis acceptance,
   implementation, partial progress, a second repair, restart, focused verification
   and return to regular integration. Separately retain final acceptance and owner
   decisions. Acceptance: no identical unconsumed task is repeatedly dispatched;
   evidence identifies an actual implemented improvement and eventual workflow
   result, including honest interruption/failure when completion is not proven.
8. **R8 — Report the performance target honestly.** Aim for ten minutes or less for
   a bounded reproducible repair with ready providers and a known fixture. Report
   elapsed time, waits, model calls, input/output counters and verification cost.
   Acceptance: compare like-for-like episodes and explain any miss. Ten minutes is
   an optimization target, never permission to remove required checks or a guarantee
   for arbitrary changes, provider outages or mandatory long-running acceptance.

## Proposed work order

First consolidate the evidence and locate duplicate rediscovery/invalidation (R1).
Then implement coherent continuation and bounded context together (R2–R4), using
the existing mechanisms rather than introducing a second repair scheduler.
Optimize only the demonstrated testing costs (R5–R6). Finally perform one representative
continuation acceptance and publish the measured comparison (R7–R8).
Package/model selection follows actual coupled state and risk, not file count.
An increased model budget requires a reasoned diagnosis; no new blanket escalation.

## Exclusions and delivery

No implementation is authorized by this draft. No changes to the update plan's
functional requirements, ODP code, production processes, acceptance decisions or
security controls. No automatic release/update, no new permanent test installation,
no generalized rewrite or promise that a higher effort tier alone solves the issue.
Future implementation documents its behavior and focused regressions in the same
change. The independent QS review evaluates all eight requirements before approval.

QS: all eight requirements reviewed; one independent review. Its technical finding
about complete workflow evidence was corrected in R5. No open functional decisions.
Status remains DRAFT pending approval; no optimizer behavior was implemented.
