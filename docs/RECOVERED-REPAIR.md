# Recovering previously committed repairs

The repair coordinator pins the first durable `attempt-started` baseline for the
exact original failure log and pipeline step. Diagnosis and replacement agents
retain that baseline even when their own starting commit is already repaired.
Legacy states recover the baseline from the existing append-only attempt history;
an agent ending is never treated as verification.

A replacement manifest still must identify the exact current commit and original
failure, with a targeted command and explanation. Recovered progress requires a
clean worktree with no open merge, actual Git ancestry from the pinned baseline,
and a committed tree change. A two-parent completed merge whose first parent is
the baseline can also qualify with an unchanged tree if the second parent adds
ancestry. An ordinary empty commit, unrelated failure, stale manifest, dirty or
unmerged tree cannot authorize recovery. A command already failed for the same
commit and failure remains excluded.

Recovery enters the existing bounded durable RepairCheck supervisor exactly once.
Only a successful fresh targeted result can restart the normal integration gates;
failed verification returns to diagnosis. It does not publish, skip checks or
rewrite previous attempts. Maintenance source changes must go through the normal
application update process before a running installation can use this behavior.

When a replacement has no manifest, HQ can retrieve a retained manifest from an
earlier attempt for the same step and failure, but only after proving committed
progress from the original baseline. The manifest must still match the current
commit and failed run. HQ records its source agent and executes a fresh targeted
check; old reported success alone is never accepted. An invalid manifest supplied
by the current replacement is rejected rather than silently substituted.
After an authorized resume with no assigned agent, HQ can consume such a retained
completed result directly, without launching another source-repair agent. A live
source agent is never treated as completed. Existing failed-command exclusions,
the original attempt count and the fresh verification gate remain intact.

Diagnosis and fix instructions keep the assigned repository boundary explicit.
A proposed HQ change is an external maintenance prerequisite for an ODP repair,
not authorization to make HQ changes from an ODP assignment. Existing source
repairs and their evidence remain usable while that prerequisite is resolved.

A resumed diagnostic-drain state without a job is explicitly interrupted rather
than waiting forever for a nonexistent completion. Its retained check-report
reference is preserved and diagnosis is required before another attempt. A still
running in-memory worker remains protected even when its job record is missing.

Focused regression: `python -m unittest discover -s tests-py -p test_recovered_repair.py -v`.
