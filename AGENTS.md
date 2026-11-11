## Project rules for agents working on HaiveQueen

- Work only in your own worktree and branch. Never change the folder a running HaiveQueen is installed in; it is updated only by its update process.
- Commit with explicit file lists, never `git add -A`.
- Before every commit check the diff: no private path, no personal name, no quote of the owner, and no control characters 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F.
- Every fix gets a test that fails without it. The whole test suite (the project's compile check) must be green before `bzz ready`.
- Commit messages are English, plain, say what changed and why, and carry no AI attribution.
- Never add a new external package without the owner's approval: ask with `bzz ask`, naming the package and what it is needed for, and work on other items meanwhile.
- Never end a running agent or a process you did not start.
- Never push while working; feature branches are never pushed.

## Integration (only the integrating Queen)

- Take the integration place with `bzz integration acquire` before integrating and release it with `bzz integration release` afterwards.
- In your own worktree: `git fetch origin`, merge origin/main into your branch (keep both sides of conflicts), run the compile check until green, commit the merge.
- Push only from your own worktree with exactly `git push origin HEAD:main`. If it is refused because main moved, fetch, merge origin/main again, run the compile check and push again. Never merge or push anywhere else; any other push is not allowed — if one seems needed, stop and ask.
