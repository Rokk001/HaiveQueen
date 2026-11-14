## Project rules for agents working on HaiveQueen

- Follow the two quality goals in docs/AGENT-RULES.md rule 10: token economy first, context conservation second. Give agents only the material they need and keep their starting context minimal; provide references for targeted retrieval instead of embedding whole plans or histories. Reuse existing evidence and decisions, avoid unchanged retries, and require a reasoned diagnosis before increasing model cost. Preserve correctness, required verification and safety constraints.

- Work only in your own worktree and branch. Never change the folder a running HaiveQueen is installed in; it is updated only by its update process.
- Commit with explicit file lists, never `git add -A`.
- Before every commit check the diff: no private path, no personal name, no quote of the owner, and no control characters 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F.
- Every fix gets a test that fails without it. Run the focused tests of your own change; do not run the full compile check or the whole test suite, the Orchestrator runs the compile check after `bzz ready`.
- Commit messages are English, plain, say what changed and why, and carry no AI attribution.
- Never add a new external package without the owner's approval: ask with `bzz ask`, naming the package and what it is needed for, and work on other items meanwhile.
- Never end a running agent or a process you did not start.
- Never push while working; feature branches are never pushed.

## Integration

- For owner-authorized maintenance pushes to main, publish one version tag per push containing new commits, starting from v0.2.0 and incrementing the patch number (v0.2.1, v0.2.2, ...). Multiple commits in one push share one tag. Never move or overwrite an existing version tag.

- A Queen never integrates and never pushes. After an accepted `bzz ready` the Orchestrator merges the integration branch, runs the compile check, builds, pushes and deploys.
