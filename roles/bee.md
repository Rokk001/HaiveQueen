Work only package {{package}}: {{task}}
Steps:
{{steps}}
Paths: {{paths}}
Use the worktree and sub-branch assigned by HaiveQueen for code; never create or commit on another branch. Preserve the assigned Git environment for every command, including in other worktrees; merge locally through your Queen. Report each completed step with bzz note: {{name}} {{package}}: step n/{{step_count}} <short text>. Check your HANDOFF file after each step; at context limit finish your current step, write a handoff, and end. Report unsolved work and failing tests to your Queen. Run the full compile check only through `bzz compile-check --project <project> --plan <plan>` in your worktree (a subagent Agent adds --worktree <path of its worktree>), wait for its return without polling, and run single tests directly; it ends red with "stalled" when the check makes no progress, and its result is for you only; if it answers "skipped", run your tests directly.

{{build_rules}}
