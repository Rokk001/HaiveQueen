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

### Runner feasibility review

The process path in `agent_runner.py` launches the provider with
`subprocess.Popen`, an assigned working directory and environment. It does
not assign a separate operating-system identity or mediate filesystem writes.
The native subagent start path returns a prompt and Git environment
to the Queen; it does not launch a separately restricted process. Consequently
neither path provides a boundary on which unconditional attribution can rely.
Adding a watcher, another environment marker or a wrapper at these call sites
would retain the ambiguity demonstrated by the concurrent-worktree regression.

Closing the requirement needs an enforced boundary for both paths, including
provider-native tool execution, before collecting protected branch evidence.
That requires a runner/provider integration design rather than an additional
Git-history heuristic. Until then, retain the existing concurrent-worktree
exclusion and report B-8 as partial; do not blame an Agent for another
package's legitimate commits.

### Concrete enforcement gaps

These are findings about the current implementation, not guarantees about
what an external provider might support:

| Entry point | Existing mechanism | Missing enforcement |
| --- | --- | --- |
| `agent_runner.py`, `main` | Adds the attempt's Git configuration to the environment, then calls `subprocess.Popen` with `cwd` and `env`. | No attempt-specific OS identity or filesystem access restriction is applied at launch. |
| `bees.py`, `start_process`, native subagent return | Records an embedded attempt and returns its worktree, branch, environment and prompt to the Queen. | No restricted executor is created for the subagent's tools. Its database identity does not authenticate a filesystem write. |
| `branchguard.py`, `prepare` and `record_violation` | Writes policy, hook scripts and violation evidence to ordinary files. | Durability across Git operations is not protection from a writer with access to those files. |
| `proctree.py`, `Tracker` | Samples descendants and records PID/birth identities for process cleanup. | It does not mediate writes or attach an authenticated attempt identity to a Git operation. Native subagents also share the provider process. |

Changing only the working directory or isolating the profile does not restrict
repository access. A linked worktree still uses shared repository metadata;
allowing unrestricted writes to that metadata leaves other branches reachable.
An independent clone would remove that shared metadata, but would only become
a boundary if the executor could not write the original repository, other
clones, the coordinator database or the audit store. A provider argument alone
does not establish that contract for every configured provider and its native
subagent tools. No such common contract is checked by the current start paths.

### Minimum design to close B-8

A feasible direction is a private clone per attempt inside an enforced
executor, with a trusted publication service outside that executor:

1. The coordinator binds an unforgeable execution identity to the attempt and
   assigned branch before any repository-capable tool runs. Every descendant
   and every native-subagent tool must retain that boundary. A provider unable
   to supply an independently restricted native executor needs a separate
   process executor or must refuse that execution mode.
2. The executor denies writes to the original repository, other attempts and
   coordinator state. Git commands within the private clone still need
   mediation or protected evidence: a clone alone permits foreign local branch
   commits, which B-8 also requires reporting by name.
3. A trusted component records attempted foreign branch commits outside the
   Agent's writable filesystem, before allowing or refusing the operation.
   Interception must cover direct executables and repository-file writes as
   well as provider tools; editable hooks are insufficient. If evidence becomes
   unavailable, the attempt fails with an audit error rather than a guessed
   branch name.
4. Publication accepts only the assigned branch and verifies its expected tip
   atomically. Use a short lock per target branch, not a repository-wide lock
   for the lifetime of an attempt. Other packages can continue concurrently.
   The end check must wait for all writers in the executor to stop before
   finalizing the protected audit and attempt result.

This is an implementation contract, not an implemented feature. The current
code has no trusted executor or publication interface to extend into this
contract. Introducing only its clone or hook portions would still leave B-8
open. Existing active attempts must continue unchanged; any future enforced
mode must distinguish their cooperative audits from its stronger evidence.

The existing real-Git test
`test_shared_history_cannot_identify_a_cleared_environment_writer` is the
minimal counterexample to a history-only repair: the foreign branch advances,
both worktree audits return no violation, and the evidence equally permits a
legitimate concurrent writer. The process and native start-path tests
`test_process_guard_uses_the_runners_final_name` and
`test_cli_subagent_returns_created_pair` verify assignment and cooperative
guard behavior only. Passing them does not establish executor isolation or
unconditional attribution. B-8 remains partial until both execution paths pass
the enforcement acceptance cases listed above.

The bounded change here does not mark B-8 complete: it closes silent success
for observably unavailable audit evidence, preserves concurrency and existing
worktrees, and reports the remaining attribution requirement for integration.
