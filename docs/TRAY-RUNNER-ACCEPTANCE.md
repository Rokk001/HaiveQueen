> Archive: TRAY-RUNNER-ACCEPTANCE. Current user guidance: [documentation map](DOCUMENTATION-MAP.md).

# Native tray and real runner acceptance

Verified on 2026-11-15 in the existing isolated test installation.

## Tray

The owner confirmed the real native menu opens. Its heading and tooltip identify
`HaiveQueen-Test`, and a distinct cyan T icon separates it from production.
The native confirmation function returned true without executing a stop command.
Desktop automation could not enumerate the notification-area popup; menu opening
is owner-confirmed, not claimed as an automated UI test.

Set `BZZ_TRAY_LABEL=HaiveQueen-Test` when launching the isolated tray. The default
production label and icon remain unchanged. The label does not affect runtime locks.

## Real runner integration

Run `scripts/verify_live_repair.py --test-root <existing-test-root> --model <model>
--effort medium` with that installation's Python. This opt-in harness uses the
existing configuration, database and provider profile. Synthetic Git projects and
a local bare remote are test fixtures under its data directory, not installations.
It refuses an active test Orchestrator or existing active agents.

Evidence: `data/logs/live-repair-result.json`, project
`acceptance-live-20261115-134857`, reports `passed: true`, no live runners, and
`configuration_restored: true`.

- Two accepted-ready plans encountered real comment merge conflicts.
- Actual scheduler rounds started eight real provider runner processes with PID,
  process identity, terminal result and exit code evidence.
- A later Build failure required a committed missing artifact.
- Windows command quoting failures triggered diagnosis and corrected verification.
- A subsequent add/add conflict in regression tests was resolved automatically.
- Both plans passed targeted verification and the unchanged synthetic integration
  pipeline through Merge, Check, Build and Push to the local test remote.
- No owner question was created during the successful acceptance run.

An initial setup run selected an expired provider login and failed; it is not
counted as successful evidence. The harness now selects only the requested test
provider and supplies the existing test configuration directory as an additional
sandbox workspace for reporting commands.

The observed quoting failures motivated a short host-shell instruction in repair
prompts. Its regression failed before the change and passed afterward. Focused
validation: 18 tray tests, 3 harness guard tests, and 2 repair-prompt tests passed.

This acceptance starts at accepted-ready checkpoints. It does not claim Queen
plan decomposition, live-update acceptance, or production deployment. Production
processes were not controlled or updated.
