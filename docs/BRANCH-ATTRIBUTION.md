# Agent branches and end audit

## Accepted scope (B-8, decision 1039)

B-8 is complete under decision 1039. Agents must never create or commit on a
branch other than their assigned package sub-branch. HaiveQueen checks the
attempt when it ends, reports an identified foreign branch by name and counts
that attempt as failed. The existing audit at `f19f0cd` is retained.

The audit covers commits in the Agent's exclusively assigned worktree using
its HEAD reflog, including commits after switching to the plan branch or a
sibling package branch and switching back. The assigned Git environment also
collects durable hook evidence for foreign commits in other worktrees,
including the plan worktree and other packages' worktrees. Shared branch
history alone is not used to blame an Agent for concurrent legitimate work.

Commits outside the assigned worktree with a cleared Git environment are out
of scope under decision 1039. No executor, sandbox or unconditional attribution
of deliberately hidden commits is required. Earlier assessments that left
B-8 partial pending an execution boundary are superseded by that decision.

## Assignment and naming (B-7 and B-9)

The cut Queen writes `branch` and `worktree` into the plan's TOML header.
`bzz plan check` refuses missing fields or the main checkout as the plan
worktree. After the cut these fields are the source of the plan assignment.

At `bzz agent start`, HaiveQueen creates or reuses the package pair:

- Branch: `<plan branch>-<package id in lower case>`, starting from the current
  plan branch tip.
- Folder: `<worktree prefix>-<plan tag in lower case>-<package id in lower case>`
  in the configured worktree location.

The Queen does not choose an Agent worktree. Process Agents start in the
assigned folder; native subagents receive the folder, branch and Git
environment in the start response. A replacement uses the same pair. A branch
already checked out elsewhere uses that folder; a conflicting package folder
blocks that package and logs the folder, actual branch and expected branch.
Existing package worktrees and branches with Agent names are reused and kept.
Running Agents and Queens are neither moved nor ended by this assignment rule.

## How the audit works

`branchguard.prepare` records the worktree HEAD reflog baseline and installs
attempt-specific hooks through Git environment configuration. It preserves
existing repository hooks without changing shared Git configuration.
`branchguard.audit` reads new worktree-local HEAD entries, tracks checkouts and
branch renames, and identifies new commits on a foreign branch. Detached
commits are reported as `(detached HEAD)`; fast-forward merges and switching
branches without a commit do not count as foreign commits.

The hooks preserve named evidence even after a switch back or reflog expiry.
The reflog fallback detects commits made in the assigned worktree after a
hooksPath override or removal of the Git environment. Missing, expired or
rewritten audit evidence fails with `branch_audit_error`; it does not invent a
foreign branch. Identified commits use `foreign_branch` in structured results.
Detected failures persist and repeated result reports count the attempt once.

The end check applies to `bzz done`, runner termination, reattachment,
embedded Queen termination and `bzz agent result`, including result aliases.
A claimed successful result cannot override detected foreign-branch evidence.

## Verification

Real-Git regressions in `tests-py/test_b2_worktrees.py` cover:

- Rules forbidding creation and commits on another branch; naming, reuse,
  conflict reporting and legacy worktree preservation.
- Plan and sibling package branch commits in the assigned worktree and in
  other worktrees with the assigned environment; named end results and a
  single failed attempt.
- Switch-back, detached HEAD, renames, hook overrides, missing or rewritten
  reflogs, merge commits and fast-forward exclusions.
- Process and native subagent assignment, terminal paths and concurrent
  legitimate commits without false attribution.

`test_shared_history_cannot_identify_a_cleared_environment_writer` documents
the explicitly excluded case. It is retained to prevent false attribution
from shared history; it does not leave B-8 open under decision 1039.
