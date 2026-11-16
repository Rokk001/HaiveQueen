# Questions, decisions and inbox

Every project has an inbox folder: `<workspace>/.haivequeen/inbox/`. Anyone (the owner, a coordinator, a script) can leave answers and decisions there as TOML files, and the Orchestrator takes them over at the start of the next round. No command needed.

- Only files that end in `.toml` (UTF-8) are read, oldest first. Write a file as `<name>.toml.tmp` and rename it when it is complete; other endings are ignored.
- A file holds `[[answer]]` tables (`question_id`, `text`, `by` = `owner` or `coordinator`) and `[[decision]]` tables (`text`, `source` = `owner` or `coordinator`, optional `plan` and `item`). See [`examples/inbox.example.toml`](../examples/inbox.example.toml).
- It may also hold `[[queue]]` tables, orders to the integration queue: `branch`, `action` (`withdraw`, `mark_cleanup` or `requeue`), `reason` and an optional `tip` (full or abbreviated commit id; without it every row of the branch is meant). `withdraw` sets the ready or blocked row(s) to `withdrawn`, but only when the tip is contained in `origin/<integrationBranch>` (checked with `git merge-base --is-ancestor`) or the reason is exactly `superseded` (the German `ueberholt` is still read); otherwise the whole file moves to `inbox/failed/`. `mark_cleanup` stores the mark `erledigt, Aufraeumen erlaubt: <reason>` for the branch; no row changes and nothing is deleted. `requeue` sets the newest ready or blocked row back to `ready` for the next integration run. A row that is in a run or already integrated is never touched.
- A `[[stop_agent]]` table (`name`) stops one running Agent like `bzz agent force-stop <name>`; subagents and the Queen are refused (see the command table).
- Values go through the same functions as `bzz answer` and `bzz decisions add`: same checks, same events, and a Plan waiting for an answer can resume.
- A file is taken over completely or not at all, then moves to `inbox/done/<YYYYMMDD-HHMMSS>-<name>.toml`. An invalid file moves to `inbox/failed/` with `<name>.error.txt` next to it that names the reason and the line, and a warning event is written.

## Owner messages

A decision with source `owner` (from `bzz decisions add`, the inbox, the dashboard or an owner action such as stopping an Agent) is delivered to a running Queen of its plan, once, in her next `bzz` answer as the key `owner_messages`. Decisions of other sources (`auto`, `coordinator`) are never delivered this way, and a delivered message is not repeated. The Queen treats each message as an instruction from the owner.

## Owner action records

The Questions page keeps its answer field for notes beside Accept and Not ok. Not ok requires a finding and supports selecting individual packages for rework. Inbox files support the same actions as `[[accept]]`, `[[reject]]`, `[[pause]]`, `[[resume]]`, `[[release]]`, `[[provider]]` and `[[unassign]]`. Fields match the commands: `project`, `plan`, `text`, `commit`, `provider`, `package` as applicable; rejection uses a `packages` list. `[[provider]]` uses `action = "enable"`, `"disable"` or `"test"`. Optional `by` is `"owner"` (default) or `"coordinator"`; commands record owner. The project must match the inbox project. For example:

```toml
[[reject]]
project = "example"
plan = "docs/plans/example.md"
text = "The export omits the selected date."
packages = ["P2"]
by = "owner"
```

Existing `[[decision]]` entries with `item = "owner-release"` continue to work.



A project-wide answer becomes a decision for every plan of that project, never another project. Technical decisions can use source `auto`; owner behaviour/design choices use `bzz ask`. Active decisions settle repeat questions by reference. An unanswered question blocks only dependent work. See [runtime API](RUNTIME-API.md) for machine-readable decisions and questions.

## Browser question context and history

Questions remains the central place for answers, decisions and owner acceptance. Its API lists open questions by default; `history=1` includes visible answered questions and supports project/plan filtering. Each entry retains its complete question, recommendation, answer, author and timestamps. Context identifies the project, plan and item; acceptance entries expose the recorded `owner_check_result` separately from answer text. A note on an owner-check question stays open and does not count as acceptance. Only the existing Accept or Reject action records that result; history is read-only.

The plan reader exposes its open questions for direct navigation. Question evidence includes the existing state-file Requirements and Completeness sections, when present, plus recorded project/plan decisions and their sources. These are existing records, not a newly inferred readiness verdict; the full original question/report remains available, including pending statements. Missing evidence remains empty. State-file reads are confined to the registered plans workspace.

The Questions view displays the complete current question once, followed by its visible recommendation and answer composer. Project-wide scope is an inline opt-in. Technical context remains collapsed, and recorded history follows the active request; a previous answer is not repeated above it. Draft text, scope and package selections survive refresh and navigation. Acceptance still requires the explicit Accept or Request changes action; saving a note never accepts a result.

Previous owner actions are shown as compact history entries with an action name, completion state and formatted time. Finished actions never display their former progress text or internal job records. Historical release requests are labelled as requests, not confirmed publications; Releases shows the publication status. Failure details require an explicit click. Stored history and live action polling are retained.

Dashboard queue rows separate project, short plan name and status badge. Long titles appear as a single-line preview; clicking the row opens the complete plan. Accessible labels retain the title, without hover tooltips. Counts and active-plan filtering remain unchanged.
