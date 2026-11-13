## Project rules for agents working on HaiveQueen

- Work only in your own worktree and branch. Never change the folder a running HaiveQueen is installed in; it is updated only by its update process.
- Commit with explicit file lists, never `git add -A`.
- Before every commit check the diff: no private path, no personal name, no quote of the owner, and no control characters 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F.
- Every fix gets a test that fails without it. Run the focused tests of your own change; do not run the full compile check or the whole test suite, the Orchestrator runs the compile check after `bzz ready`.
- Commit messages are English, plain, say what changed and why, and carry no AI attribution.
- Never add a new external package without the owner's approval: ask with `bzz ask`, naming the package and what it is needed for, and work on other items meanwhile.
- Never end a running agent or a process you did not start.
- Never push while working; feature branches are never pushed.

## Integration

- A Queen never integrates and never pushes. After an accepted `bzz ready` the Orchestrator merges the integration branch, runs the compile check, builds, pushes and deploys.
