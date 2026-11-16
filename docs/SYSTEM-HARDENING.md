# System hardening

The Queen cuts and coordinates the plan; the Orchestrator owns integration after
accepted ready. Diagnosis explains an evidenced failure; a repair agent changes
the implementation; HQ verifies the result before resuming. These responsibilities
remain separate. A stronger model does not replace any result gate.

## Models and plan cutting

New defaults select heavy for Queen coordination and question, package and
integration diagnosis. Explicit existing classes, models and effort values remain
unchanged. Configure a capable model with suitable reasoning effort in Providers;
the example and new heavy entries suggest high effort. A class is a mapping, not
a guarantee of intelligence. Smaller execution packages can use light or medium.
Ready-check classes remain separately configurable. Missing eligible heavy models
cause scheduling to wait, not an automatic downgrade. Existing quota, capability,
memory and concurrency gates continue to apply.

The Queen records a concise Cut assessment beside the requirements in the state
file: complexity, shared mutable state, dependencies, recovery and whole-flow
verification. It identifies the package responsible for joining components and
proving the requested path. Simple work can remain one package. Complex package
class choices need reasons; small file counts do not establish simple work.
The independent ready reviewer follows real entry points across the required
components and failure/recovery paths. A green helper alone is insufficient.

## Repair and recovery

Integration keeps the original failure log, commit, decisions and attempt history
retrievable. After an ineffective repair, diagnosis is required before retrying.
Reusing the same observations with a new action wording, model or incidental
commit does not authorize another repair. The diagnosis must identify the actual
outcome of the previous attempt and new evidence. This mechanical comparison
does not replace the agent's responsibility to assess the meaning of evidence.

A malformed integration diagnosis receives one scoped correction opportunity.
The correction points directly to the previous report and execution directory;
valid observations are reused instead of embedding or reinvestigating the whole
history. Existing repeated-format-failure escalation remains intact.

Committed repairs require a targeted command bound to the original failure and
current commit. HQ executes that command and then the ordinary integration
checks. A targeted helper passing cannot turn a still-failing required check
green. The attempt history survives and known passing results are retained.

After restart, live process identities and durable completion receipts are
reconciled. An interrupted check can be resumed using the existing infrastructure
recovery. A vanished Deploy process **without a completion result** instead
requires diagnosis of the destination, retained logs, commit and artifacts before
a retry is authorized. Copied files alone are neither success nor evidence that
repeating a deployment is safe. The same requirement survives pause/resume;
observing a paused run never starts a diagnosis. Completed steps remain recorded.

Integration reservations and resource reservations have different purposes.
Existing diagnostic-drain handling releases the integration place only when safe;
the actual CPU/build/game/port/output reservations remain until their owners end.
Owner acceptance remains a separate, explicit action after technical integration.
Provider waits distinguish disabled providers and failed connection checks from
occupied slots. An offline provider remains blocked until its real connection
check succeeds; a more accurate label does not bypass that protection.

## Acceptance

`scripts/verify_live_repair.py --test-root <existing-test-installation> --model
<configured-model> --effort high --from-submission` uses the existing isolated
test database/profile and local bare Git remote. It refuses a running test
Orchestrator, foreign active agents, projects outside the test installation or a
missing provider login. It temporarily configures a test provider and restores
the previous configuration when its own runners have ended.

`--resume-fixture <recorded-fixture>` resumes the same test evidence after an
observer restart. `--normal-provider-mode` is an explicit opt-in for an
owner-authorized provider invocation without the CLI sandbox. It changes only
the temporary test provider; it is not a production default. Use it only when
the existing test installation is isolated and this execution mode is approved.

The submitted cases are a simple documentation plan and a CLI/storage workflow
requiring atomic persistence and restart behavior. Real Queens cut the plans;
real agent runners, Git commands, check supervisors and result consumption run.
Two faults are injected at the external provider boundary: the first repair
returns without changes; the first diagnosis loses a required field, retaining
the unmodified report separately. All remaining provider calls use the real CLI.
No HQ success state, accepted-ready result or owner approval is fabricated in
this mode. The run ends at open owner acceptance questions.

Quota is read from the actual test account through the normal account reader;
unknown or exhausted quota does not authorize starts. Detailed reports and logs
remain in the test installation. Focused regression tests cover individual
interruption, resource and protocol cases in addition to this connected run.

### Verification coverage

| Requirement | Verification |
| --- | --- |
| H-01, H-02 | `test_diagnosis_settings`: defaults, retained explicit mappings, actual model/effort and missing-heavy scheduling; documented provider settings. |
| H-03, H-12 | `test_plan_cut`, plus real simple/documentation and coupled CLI/storage plan cuts in the connected acceptance. |
| H-04, H-05 | Existing ready and integration flow, real required-check failure and retained execution evidence; `test_technical_diagnosis`. |
| H-06 | Ineffective repair, malformed-report correction and accepted diagnosis in the connected run; rewording/incidental-commit and changed-owner-decision regressions. |
| H-07 | `test_repair_evidence`, `test_repair_scope`: passing helpers cannot authorize an unchanged failing workflow; real repaired commit goes through the ordinary checks. |
| H-08 | `test_orphan_job_recovery`, real interrupted file-copy/retry in `test_hardening_delivery`, and observer restart retaining the live diagnosis identity. |
| H-09 | `test_integration_diagnostic_drain`, `test_stop_reservation`, `test_hive_lifecycle_recovery`, `test_provider_wait_recovery`; two real plans share the existing integration/repair mechanism. |
| H-10 | Concrete offline/disabled waits; actual local-remote push and commit-bound file-delivery receipt; open owner questions, never automatic acceptance. |
| H-11 | Connected acceptance from submitted plans; provider-boundary faults are explicitly injected, while scheduling, Git, runners, checks and result consumption remain real. |

The original integration responsibility split, speculative merge queue and
local/provider CI/CD boundaries are retained. The new gates refine when repair
or replay is justified; they do not introduce a second integration controller.
Restart, diagnostic drain and resource cases use focused controlled fixtures;
the connected run is not a production update or a live game deployment.

### Recorded connected acceptance

The isolated run `acceptance-live-20261116-131947` passed on 2026-11-16.
It retained the same live diagnosis process across an observer restart, rejected
the injected incomplete diagnosis, reused its observations for correction and
verified the subsequent committed repair through the ordinary integration flow.
Both plans reached open owner acceptance questions. The final local-remote and
file-delivery commit was `57b2d102c924954ab6a750de3ee2130911178d7c`.
No owner acceptance was submitted. All owned runners ended and the original
test configuration was restored.

The test installation retains `data/acceptance-live-20261116-131947/acceptance-result.json`,
`restart-before.json`, `restart-after.json`, the delivery receipt, original and
corrected diagnosis files and the normal execution logs. The earlier fixture
`acceptance-live-20261116-124543` reached acceptance but is explicitly recorded
as **not passed** because its boundary injector missed the intended fault; it
is not substituted for this passing run.
