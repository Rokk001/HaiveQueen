# Foreign commit attribution with concurrent Agents

The unconditional B-8 reporting guarantee remains open. Missing or rewritten
audit evidence now fails the attempt with an explicit audit error, rather than
silently passing or inventing a foreign branch name. This does not supply
process identity for commands that discard the assigned environment.

## Approach 1: scan shared Git history

Snapshots of refs, objects and repository-wide reflogs can find new commits.
They cannot identify which Agent issued a command. Git commit author and
committer fields are supplied by the caller; reflogs identify a worktree and
ref, not an Agent process. Timestamps cannot distinguish concurrent writers.

The regression `test_shared_history_cannot_identify_a_cleared_environment_writer`
starts two guards and commits in the second assigned worktree with the guard
environment removed. The same Git invocation produces the same evidence
whether the first Agent entered that worktree or the second Agent worked
legitimately. A scan blaming the first Agent would also blame it for legitimate
concurrent work. A scan that correctly ignores the second Agent's own commit
cannot detect the first case. No extra interpretation of these shared bytes
resolves that ambiguity.

Per-worktree hooks or repository-wide hooks do not supply the missing process
identity. A command can override `core.hooksPath`; a PATH wrapper can be bypassed
by invoking Git directly. A periodic monitor can miss a commit and subsequent
cleanup between observations. Serializing all Agents would sacrifice
concurrency and still would not make editable logs authoritative.

This approach is rejected as a reliable attribution solution. The current
worktree-local audit is retained because it excludes other packages' history.
Its missing/rewritten evidence is now reported honestly as unavailable.

## Approach 2: enforce an execution and filesystem boundary

A reliable design needs identity and evidence outside the Agent's control.
Every process capable of repository writes would have to run under an
attempt-specific enforced identity; access to other packages' worktrees,
shared refs and audit evidence would need mediation. A trusted service could
apply allowed branch updates and retain rejected branch names. Separate Agent
identities or isolated filesystems could preserve parallel execution.

An environment variable or process-tree snapshot alone is insufficient: the
environment can be cleared and short-lived descendants can exit before a
snapshot. An isolated clone alone is also insufficient when the Agent can
still access the original repository. Native subagents currently share their
Queen's execution context, so they would need their own enforceable boundary
as well. The current cooperative guard provides none of these guarantees.

This is a viable architectural direction, but is not implemented here.
It requires runner and provider support, operating-system access controls,
protected durable audit storage, and a specified path for accepted changes
to reach the existing package branch. Acceptance must cover direct Git
invocation, cleared environments, hooks overrides, reflog removal, detached
commits, cross-worktree writes, surviving descendants, native subagents and
concurrent legitimate commits without false attribution. Existing running
Agents must remain in place while any new boundary is introduced.

The bounded change here does not mark B-8 complete: it closes silent success
for observably unavailable audit evidence, preserves concurrency and existing
worktrees, and reports the remaining attribution requirement for integration.
