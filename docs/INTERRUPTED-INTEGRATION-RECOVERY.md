# Interrupted integration recovery

The scheduling mutex combines an exclusive operating-system guard with owner
metadata. Unreadable metadata from before the current system boot is archived,
not silently deleted, while holding that guard. Empty, truncated and zero-filled
metadata follow the same recovery path. A live recorded owner or a held OS guard
prevents takeover. Without reboot evidence, unreadable ownership is preserved.

A Check whose supervisor disappeared without a result is an interrupted run,
not proof of a project defect. Its aborted result stays in the action history.
The integration retries only that stage; previously successful stages remain
complete and no code-repair agent is launched solely for the interruption.

A missing Deploy result does not establish success, even if an executable was
copied. A matching result or durable exit receipt is required. Otherwise the
Deploy stage must run again, including its configured validation. The integration
reservation remains until the normal pipeline releases it; followers must not
push past an unverified deployment. Owner acceptance is never inferred.

The dashboard shows a recorded local job whose process is gone as awaiting
recovery rather than running. The scheduler still decides the actual outcome
from stored results and receipts. Start and terminal action records explicitly
flush to disk; output remains streamed. This reduces the durability window but
cannot guarantee preservation through hardware or filesystem failure.

Recovery must preserve logs and a consistent database backup before reconciling
an incident. Use the scheduling guard and the normal job-harvest routines; do not
rewrite database state by hand or manufacture successful results. Do not remove
a STOP request or restart an operator-stopped orchestrator during recovery.
