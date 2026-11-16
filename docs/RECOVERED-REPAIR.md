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

Focused regression: `python -m unittest discover -s tests-py -p test_recovered_repair.py -v`.
