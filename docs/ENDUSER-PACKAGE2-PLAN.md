+++
status = "DRAFT"
+++
# End-user package 2

Implementation is authorized in an isolated worktree, followed by visual verification in the existing HQ-Test installation. This document records the approved scope; it is not a scheduler submission.

## Requirements

- R1: Keep Questions as the central place for answers and acceptance. Show each plan's open questions and acceptance requests with navigation to the relevant question; provide navigation back to the plan. Verify both directions without duplicating question state.
- R2: Explain the owner ready-gate release distinctly from publishing a software release, project-wide answers, rejected packages, and saving a note versus accepting a result. Verify labels and action explanations preserve the existing business behavior.
- R3: Present one unambiguous plan-file-pattern setting while preserving effective existing patterns and exclusions. Verify legacy settings round trips and their discovery behavior.
- R4: Allow selecting a registered project, entering Markdown or importing a Markdown plan, saving a draft, and explicitly submitting it through the existing DRAFT to SENT and Queen workflow. Verify saved plans are discoverable, drafts do not execute, submission is deliberate, and existing files are not overwritten. Editing saved drafts supports completing this workflow; running plans are outside that editing scope.
- R5: Organize Questions using understandable task types (questions, decisions where existing metadata identifies them, acceptance), plan context, and an explanation of the requested action and its consequences. Show concise information first and expandable original technical details. Preserve the full source text and existing answer/acceptance semantics.
- R6: Show open and completed questions with their recorded answers/history. Verify historical items cannot be answered or accepted again through this view.
- R7: Structure existing acceptance evidence (result, commit, integration steps and reports where available), retaining original technical reports and existing accept/reject/package-rework behavior. Do not claim unverified outcomes or generate new evidence.
- R8: Document product changes in the same change, run focused regression tests, and visually verify navigation, question/history/acceptance views, settings and plan submission in existing HQ-Test. Do not create a new environment, alter production, or start/stop running agents or the Orchestrator.

## Boundaries

Reuse existing plan discovery, state transitions, inbox ingestion, answers, decisions and owner action services. No separate inbox processor or new external dependency. No production deployment or feature-branch push. Visual testing uses the existing test configuration/data and the test-only webserver started by this chat.

## Work packages

1. Backend: safe draft authoring/submission and question history API, focused tests, plan/inbox documentation.
2. UI: plan-question navigation, authoring/import, Questions task views/history, acceptance evidence, clear actions, styles/translations, focused UI tests and web documentation.
3. Settings: remove duplicate pattern presentation preserving legacy semantics, focused tests and settings documentation. Independently review the full approved scope.
4. Assemble feature branch, review all requirement evidence and visually verify in HQ-Test. Resolve findings within the approved scope.

## Approval basis

The owner approved all four prioritized proposal items, including the subsequent clarified general Questions UX overhaul and preservation of Questions as the central location, with implementation in an own worktree and subsequent visual testing in the existing test environment.
