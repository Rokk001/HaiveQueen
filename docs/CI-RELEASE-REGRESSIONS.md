# Release and detached-job regression coverage

The release compatibility contract recognizes the repository dependency pin
`tomlkit==0.15.2` only when that exact version is installed. Unsupported or
unproven environments still defer an update; release metadata alone cannot
establish compatibility. Release lifecycle fixtures authenticate the archive
against its Git tree and use the current package, rather than a placeholder CLI.

A detached Build or Deploy supervisor owns the command process group. Pipeline
cancellation writes a request beside its result file. The supervisor stops and
reaps its command before publishing exit 143. The pipeline retains its occupied
worktree until the supervisor has ended; cancelling only the supervisor could
otherwise leave a command writing into a worktree being reset by a successor.
PID birth identity checks continue to protect unrelated processes.

Dashboard version lookup uses the matching process lease for an Agent and the
recorded version directory for a detached job, including jobs from an older
installation. An unknown version remains unknown rather than being replaced by
the current core version. Tray command launches use Windows creation flags on
Windows and a new session on POSIX.

Focused regressions cover authenticated staging and rollback, dependency pins,
reservation ownership, detached cancellation and queue replay, version display,
service launches, and update confirmation in an isolated browser. Process tests
use temporary data directories and synthetic jobs. They do not operate production
or implement either optimization plan.
