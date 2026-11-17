# Failed integrations and remaining check results

Local Check, Build and Deploy commands can report individual required checks
through `haivequeen.check_events.report`. The command remains responsible for
running its complete suite and its configured retries. HQ does not infer a final
failure from arbitrary console output, or split an opaque shell command.

The first final nonzero required result changes the integration to `diagnosing`
and its step to red. The original worker continues. This phase cannot enter
Push, Deploy, Release, replay/reset, or fix execution. Later successful results,
including a zero command exit, cannot turn that run green. Once the worker ends,
the existing repair coordinator receives a combined failure report. Repair starts
only after the checked worktree is no longer in use. Existing repair deduplication
is reused; this change does not launch speculative repair agents.
This prevents subsequent publication; it cannot undo a Push or file copy that
already happened before a Deploy command reported a failure. Checks intended to
gate publication must therefore run before Push/Deploy. A partially completed
deployment remains a failure and must not be reported as a successful delivery.

## Runner contract

HQ supplies `HQ_CHECK_EVENTS`, `HQ_CHECK_RUN` and `HQ_CHECK_COMMIT` to each local
command, including a detached compile supervisor. A single runner writes an
append-only JSONL journal using this API (other languages may write the same
records, including `run` and `commit` from those environment variables):

```python
from haivequeen.check_events import report

report('suite', checks=['compile', 'menu', 'load'],
       isolated_worktree=True,
       artifacts={'build/game.bin': actual_sha256},
       resources=['game:integration', 'port:31411', 'output:game-build'])
report('result', check='compile', exit=1, final=False, output=first_attempt_output)
# Only after the existing retry policy is exhausted, or the check succeeds:
report('result', check='compile', exit=1, final=True, output=retry_output)
# Emit only once the remaining work cannot write shared integration/deploy data:
report('shared_writes_finished')
# Continue menu and load; never omit them because compile was red.
```

A final failure caused by the test environment rather than the product (for example a
missing tool or service the check needs) carries `cause='infrastructure'`; the default is
`product`, other values are a reporting failure. A run whose failures are all of the
environment is reported as a finding and starts no repair (see
[Complete test runs](OPERATIONS.md#complete-test-runs)).

Declare the complete required check list once. Optional probes must not appear in
that list. Each result records the real exit code and complete output or its
durable log references. A retry attempt is `final=False`; a final result is
immutable. A timeout is a nonzero final result after the runner has reaped its own
child. The runner exits only after all checks and descendants have finished.
The suite must not edit tracked source or replace the declared artifacts. Artifact
paths are relative to the isolated checked worktree; hashes are SHA-256.
An empty artifact map explicitly means that these checks use no build artifacts.

The event journal is durable and run/commit-bound. Partial records are read on the
next poll; a changed manifest, mismatched run, replaced final result or truncated
journal is a reporting failure. HQ stores incremental and final reports under
`orch-check-report:<run>` with plan, commit, event journal and action-log references.
A missing final result is a failure, even if the command exits zero. A worker lost
during a failed diagnostic run closes it as interrupted, not successful or an
unchanged automatic retry. Restarting HQ resumes observation of the same worker
and journal, preserving the failure latch and reservations.

## Integration ordering versus resource ownership

An early queue release requires all of the following: final failure, a valid
suite manifest, explicit `shared_writes_finished`, an isolated worktree, declared
remaining resources, unchanged HEAD and matching artifact hashes. Without this
evidence the UI explains why the integration place remains held. These declarations
are a contract of the configured runner, not an OS sandbox; adapters must be
reviewed against their actual reads and writes before enabling early release.

Releasing the queue entry does not release the worker's compile slot or build-core
allocation. Named diagnostic resources remain reserved until worker completion.
Names are machine-wide and must describe shared game instances, ports, output
folders and other exclusive dependencies. A project may declare step footprints:

```toml
[integrationStepResources]
Check = ["game:integration", "port:31411"]
Build = ["output:game-build"]
Deploy = ["game:integration", "output:game-build"]
```

Missing footprints conservatively conflict with a draining diagnostic run.
Empty lists explicitly declare no shared exclusive resources. CPU/RAM limits
still apply. The same diagnostic gate applies to detached compile-slot acquisition
and build allocations, including repair checks. Independent merges and steps with
disjoint declared footprints can proceed; overlapping or unknown commands wait.
Do not declare two full builds disjoint when they use a common build tree or when
the project prohibits concurrent full builds: give both a shared full-build key.
Already-running commands and foreign processes are never terminated by this gate.

## Existing opaque runners and ODP boundary

An uninstrumented command retains its existing exit-code behavior. No false
individual progress or early safe-release claim is made. Provider CI currently
also retains its aggregate pipeline-result behavior.

The inspected external ODP `run-checks.py` retries WinError 4551 before recording
the final result, and guards its later level test with `red == 0`. Its checks are
inside the Build command. Merely watching its first error line cannot implement
the contract above. The runner needs instrumentation, complete failure output,
and removal of that failure-dependent omission while preserving prerequisites and
all assertions. No ODP source, runner, project settings or production state is
changed by this HQ implementation. Until its adapter is implemented and verified,
the requested behavior is not enabled for that ODP command.

## Focused verification

`tests-py/test_integration_diagnostic_drain.py` exercises irreversible early failure,
retry success/failure, later errors, persisted restart observation, safe queue
release with retained core and named reservations, zero-exit-after-failure,
commit/artifact mismatch, incomplete journals, and missing/crashed/timed-out checks.
These are isolated regression tests, not a completed ODP production acceptance.
