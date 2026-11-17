# Operations and recovery

## Roles

| Word | Meaning |
|---|---|
| **Project** | a project (a repository with a `.haivequeen/project.toml`) |
| **Plan** | one plan of a Project, worked by one Queen at a time |
| **Queen** | the main agent of a Plan: works the plan, delegates, merges the Agent branches, checks completeness and reports `bzz ready`; she never integrates |
| **Agent** | a helper agent that works one package for its Queen |
| **Orchestrator** | the managing loop: no model, no window, a hidden background process; after an accepted `bzz ready` it runs the integration pipeline (Merge, Check, Build, Push, Deploy) of the plan |
| **Fix agent** | an agent the Orchestrator starts when a pipeline step is red or you reject the result; it repairs the plan in its worktree and the pipeline starts again |

## Background operation

The loop and all agents run as hidden background processes; the dashboard runs in your browser. Closing the browser leaves the loop running, and `bzz start` opens it again without restarting agents. A start during a pending stop, during a running update or while another start is under way is refused: `bzz start` then exits with 1, and the reason is an event and a line in `runs/orchestrator.log`. A start while the loop runs still exits with 0. Windows desktop shortcuts can use `bzz start --desktop` and `bzz stop --desktop` for a visible result or error dialog; ordinary CLI calls keep their JSON output.

## Integration

After an accepted `bzz ready` the Orchestrator integrates the plan in its worktree, in queue order for publication, with these core steps: Merge (the integration branch into the plan branch), Check (the **Compile check command**), Build (the **Build command**), Push (never forced) and Deploy (the **Deploy command**). The project execution settings select enabled steps and local or Git-provider modes; legacy configurations derive enabled steps from nonempty commands. A red step starts a fix agent that repairs the plan in its worktree; the pipeline then starts again at Merge. The dashboard shows the chain of steps and the fix agent. Merge, Check and Build may run speculatively behind earlier queued plans. Push, Deploy, Release and Close wait for preceding plans; `serialIntegrationBuild` also serializes Build. If earlier integration changes invalidate a follower, it replays from Merge.


### Acceptance and done

After the enabled pipeline steps, including Release, complete HaiveQueen itself files an owner check question (`owner-check`, text from the setting `ownerCheckText`, by default "Check the integrated result" in the language setting). Choose Accept in the Questions page or run `bzz plan accept`; choose Not ok with a finding or run `bzz plan reject --text "..."` for rework by a fix agent. Free text alone is a note and leaves the check open. Acceptance sets DONE; a plan whose header says `owner_check = "none"` skips the question and is done after integration. A plan with nothing left to integrate (it changes no tracked file of the code repo and has no plan branch, or every commit of its branch is already in the integration branch) closes the same way without integration: the Queen reports with `bzz ready` (no queue row, no compile check, build or deploy) and ends, a fresh check agent passes it, the owner check asks you to check the result of the plan, and acceptance sets DONE; no "queen-ended" question is filed.

### Complete test runs

Check and Build are the complete test runs of a plan: they run once as its completion evidence in the existing environment, not per repair step. Each run keeps a record (`bzz integration runs --project <p> --plan <plan>` prints it as JSON): reason, step, command, commit, a digest of the project configuration, start, end, duration, result and, for a failure, its text and check report key. The reason is `first run`, the changed input (`changed commit`, `command` or `configuration`), a previous run without a product verdict, or a previous pass that cannot be reused.

- **No repeat with unchanged inputs.** When the last run of a step failed in the product and commit, command and configuration are unchanged, the step is not run again: it is red at once with the earlier failure, so the repair must change something first. A paused plan keeps the verdict of a run that completed meanwhile.
- **Reuse only with a valid reference.** A pass is reused only when commit, command and configuration are unchanged, the checkout is clean and every artifact declared by the runner still has its recorded SHA-256. A run without declared artifacts is never reused. A step without a valid pass never counts as done.
- **Environment failures are findings, not product failures.** A run whose failures are all in the test environment (an invalid check report, a changed checkout or artifact, a worker that ended without a result, or checks the runner reports with `cause: "infrastructure"`) starts no fix agent. It files the question `integration-environment-<step>` with the finding; the answer runs the step again. A product failure in the same run keeps it a product failure.

## Failures and diagnosis

Package classes bind the initial work. After `escalateAfter` failed attempts (default 3), HaiveQueen requests an evidence-bound diagnosis before authorizing another attempt or a more expensive model. The diagnosis must match current attempts, decisions and package commit; a class change needs a reason and an offered class. A technical diagnosis grants one scoped retry, without erasing attempt history. Functional questions go to the owner. Inspect attempts with `bzz agent history`.

**Offline providers.** When an agent of a provider ends with a 5xx or a connection error, the provider is offline: it gets no new Queen or Agent and no retry, new tasks and retries go to other providers, a stored package assignment to it is dropped, and the Model Limits panel shows "offline" in red (the hover text says since when). Agents that run keep running. Every `providerRecheckMinutes` (machine `config.toml`, default 5) HaiveQueen checks an offline provider, a Local LLM with its connection test and a Subscription or API key provider with a short call of its CLI; when that works the provider is online at once. A rate limit keeps its own handling.

**What counts as a failure** is deliberately narrow: only a model that keeps trying the same thing and does not get it done. These never count and never escalate:

- a package marked `implemented` (the Agent finished; the rest is verification),
- open requirements of a package whose `verify` is `build` or `owner`,
- an Agent that waits for something: `bzz agent result --outcome failed --blocked-by question|build|owner|package|provider` sets the package to `waiting` instead (a question needs `--blocked-ref <question id>`, and the package works again when that question is answered),
- interruptions and provider limits.

A `failed` result without evidence (`--open-requirements` or `--error`) is refused, so a bare "failed" never reaches the counter.

## Updates

HaiveQueen updates itself the same way for every user:

- Once a day (`update.checkHours` in `config.toml`, default 24; `0` or the environment variable `BZZ_NO_UPDATE_CHECK=1` switches the daily check off) and whenever you click **Check now** in the header of the dashboard, HaiveQueen asks GitHub whether a newer release exists. `bzz update` does the same check on the command line. A failed check (offline, rate limit) is shown in the header and tried again after the interval or with Check now.
- When one exists, the header shows **New version available: X – Update**. Nothing is installed until you click it and confirm.
- After the confirmation HaiveQueen starts no new Queen or Agent, waits until every running agent of every Project has ended (it ends none), fast-forwards the clone to the release tag `vX`, runs `pip install -e .` again when `pyproject.toml` changed and starts again; the dashboard reopens with the new version. If the new version does not start, it goes back to the previous one and files a question. `bzz update --install X` does the same for the offered version X.
- The header and update comparison use the highest stable Git version tag `vX.Y.Z` reachable from the installed commit. Prerelease, malformed and unrelated-branch tags do not count. Without a locally reachable stable version tag, the installed version is `0.0.0`; neither `pyproject.toml` nor installed package metadata supplies a fallback. The check does not fetch tags or change the installation.
- The releases come from the GitHub repository of the clone's `origin` remote; `update.repository = "owner/name"` in `config.toml` names another one. Local commits on the clone's branch stop the fast-forward; the update then fails and keeps the installed version.

## Cleanup, process recovery and reload

- **Leftover processes.** When a Queen or an Agent ends (normally, with an error or stopped), HaiveQueen ends every process the agent started and that still runs, also one whose parent already ended. The list is kept in the run folder (`processes.json`), so the leftovers of an agent that ended while HaiveQueen was not running are ended at the next start. A compile check started by HaiveQueen and a separate Agent started with `bzz bee start` are not touched.
- **Reload.** Saved changes to the machine `config.toml` and to registered projects' `.haivequeen/project.toml` are reloaded at the next poll, also while idle, without restarting running agents. Valid changes wake scheduling at once; an invalid or temporarily missing file keeps the complete last valid settings. An explicit `--round-minutes` still takes precedence.

- Completed idle Plans are reconciled with the plan status and confirmed integration; active agents and genuine owner questions are preserved.
- A Plan without a Queen shows its recorded start blocker or the numbers of its pending questions instead of a generic waiting label.
- Automatic worktree cleanup requires confirmation that the branch's current commit was integrated; a missing or superseded confirmation keeps the worktree. The cleanup runs in parallel to the round and never holds it.
- Round totals count actual Queen launches, independent of earlier run timestamps.
- Stop prevents every new start of every kind of Agent: Queens, separate Agent processes, embedded subagents and the check agent of `bzz ready`. Stop lets running agents finish, then ends HaiveQueen, and `bzz start` allows starts again. Running Queens and Agents are not ended; a refused start gives the reason.

Runtime state is one local SQLite database; network drives are refused because SQLite WAL requires local storage. `bzz export` exports state and `bzz views` writes read-only Markdown views. After a crash, startup reattaches to live agents. Agent display names come from `bee_names.txt` in the configuration directory and changes apply at the next start.

## Scheduling lock and cleanup

The worktree cleanup runs in parallel to the round, in a thread of its own (one per project at a time), and never holds the round: however long a cleanup takes, rounds go on and end, the inbox is processed, Queens are started and get their orders, and Agents start. A cleanup error is logged and stops nothing. Cleanup locks only the worktree or branch being removed. A Queen start by the round that needs exactly that target is left out of this round (the round line says so) and tried again in a later round, once the removal has ended; it never makes the round wait and never ends it with an error. A start that a Queen makes herself waits for the removal as before (up to the wait for the scheduling lock), and the round never waits for it; unrelated starts proceed. The scheduling lock records its process, reason and heartbeat; dead or expired holders are recovered, and a timeout identifies the holder and reason.

## Git workflow and repair

Local merge brings the integration branch into the plan worktree, preserving both sides of conflicts, then checks/builds and pushes without force. Git mode creates or recovers the matching pull request and tracks provider evidence for the exact commit. Check, Build and Deploy can each use local commands or provider pipelines; Custom combines modes. Authentication or unsupported-provider conditions produce a wait reason. The [settings reference](SETTINGS.md#execution-and-advanced-project-controls) lists the switches.

Repair starts from the failed step's evidence and retained attempts. Repeated technical failures require diagnosis before a retry or higher-cost repair; equivalent shared causes can reuse a diagnosis rather than repeat unsuccessful work independently. A repaired step is verified by running the pipeline again. Owner rejection supplies a finding for rework; it is not an automatic approval of a failed repair.

## Releases

Release is a durable Orchestrator step and must finish before owner acceptance. Current publication creates prereleases such as `v2.1.0-dev.1`. The target version is one patch above the highest full repository tag, unless `nextVersion` chooses a newer version. With no full tag, a start version is required. Sequence numbers are shared by the target version even if the postfix changes. Existing tags are not overwritten.

In Local release mode, files in `releaseFolder` are uploaded and verified before local assets are removed. In Git release mode, HaiveQueen creates the selected tag and waits for the provider workflow to publish it at the expected commit. Failures retain the release identity for retry, so a later plan cannot replace its commit. Credentials come from the configured Git connection or the provider's supported token/login fallback.

The browser Releases page lists provider history. Select a versioned prerelease and confirm promotion to a full release: Local mode verifies and copies its assets; Git mode tags the same commit so the provider workflow can publish it. Promotion uses provider-owned metadata, never a browser-supplied commit. Unsupported history/promotion capabilities are reported explicitly. Legacy `publishReleases` configuration and queued publication records are migrated/read for compatibility; they do not publish outside the Release step.

## Global plan stop and resume

The web Controls > PLANS commands track the plans stopped by that global action in persistent state. Resume restores only those plans to their recorded active status, once their work has stopped. Previously paused, completed, cancelled and draft plans are excluded. Repeated global stops retain the pending group; force stop can end remaining work in that group. Individual status changes remove membership. An unknown previous status never defaults to QUEUED: use an explicit plan submission instead. Confirmation lists the server-selected plans; changes to that selection require a fresh confirmation. Each executed global plan action records its source and affected plan in events.

Package diagnosis requests capture a completion generation. Historical successes do not cancel a new review; only a later, newly recorded success does. Duplicate success reports do not advance the generation. Legacy reviews without ordering evidence retain their diagnosis and establish a baseline instead of assuming an old completion supersedes them. Attempt history, accepted diagnosis signatures and single-use retry authorization remain intact.

## Dashboard scrolling

On desktop, `Hives` uses the currently visible viewport height, including space freed by scrolling past the header. Its vertical scrollbar appears only when the expanded rows exceed that space. Tablet and mobile retain natural page height. Pipeline buttons wrap within their task column instead of creating horizontal scrollbars in every process row. `Hives` columns adapt to the panel width; necessary scrollbars in lists, logs and text areas use a thin, subdued style without hiding content.


### Focused CI verification

Desktop pipeline controls remain compact on both mouse and touch-capable desktops.
Steps wrap when their task column is narrow, without horizontal scrolling; tests
bound row height by the actual number of wrapped lines rather than requiring one
line regardless of available width. The standalone Linux layout fixture uses the
same sandbox-free headless execution needed by the CI runner, with a temporary
browser profile and only generated local HTML.

Pipeline regressions supply a completed acceptance reviewer at the review boundary;
the dedicated acceptance tests cover scheduling, validation and caching. Update API
tests check the displayed state after refresh, not the raw refresh return value.
Draft creation, draft saving and global plan actions have German log translations.

## Loop and web service outside the agent's job

On Windows an agent CLI such as Codex runs its commands inside a job object that
ends every member process when the agent ends. When a `bzz` command from such an
agent started the orchestrator loop or the web service, both ended together with
that agent, and with them the agents the loop had started (observed on 2026-11-16:
loop, web service and a diagnosis Agent ended when a Codex Queen ended, and nothing
ran until the next manual start). `bzz start` and the web service therefore start
their processes with `CREATE_BREAKAWAY_FROM_JOB`; a job that forbids breakaway
refuses the flag and the process starts as before. Such a start also gets its own
console supervisor started outside the job (see below): Windows ends the processes of a
debugger together with it, so a supervisor left inside the job would end the loop anyway.
Focused regressions: `python -m unittest discover -s tests-py -p test_outside_agent_job.py -v` and
`test_shared_console_broker.py` (a real job whose end must not end a detached start).

## Program starts on Windows

Every program HaiveQueen starts on Windows runs under its console supervisor
(`haivequeen/console_tree.py`), so neither it nor its descendants open a console
window. One supervisor per HaiveQueen process serves all ordinary starts; the handles a
program inherits are duplicated into it. A start that inherits every inheritable handle,
or leaves the caller's job, gets a supervisor of its own. A supervisor keeps supervising
running trees after its caller ended and ends with the last of them.

Starting a separate supervisor for every program made Windows the slow platform
(CI run 43955634430: Windows 5,035 s, Ubuntu 739 s for the same 3,442 tests; about
2,900 tests that take under 0.2 s on Ubuntu took 1,833 s together on Windows, and in
`test_hives_h2` 130 of 143 s went to 509 program starts). Measured on one machine, a
`git --version` start took 15 ms without supervision, 220 ms with a supervisor per
start and 41 ms with the shared supervisor; `test_hives_h2` (73 tests) went from 95 s to 42 s.
The remaining difference to Ubuntu comes from supervision itself and from Windows process
starts; the comparison after integration uses the timings of the next CI run.

## Waits of a Queen for her subagents

Besides tokens, cached input and the start context, the agent row of a run stores
`agentWaits` (in its extra fields): the native calls that only waited for a
subagent. Each such call is a model turn over the whole context; a value above
zero for a Queen shows polling, which HaiveQueen avoids by running Agents of
non-blocking subagent protocols as processes. Rows without the value had none.
Focused regression: `python -m unittest discover -s tests-py -p test_agent_wait_count.py -v`.
