# Browser parity audit

Compared the removed desktop sources at `bdc99da^` with the browser sources at `2ffcfea`. The dashboard's tiles, active plans and agents, activity, system, queue, limits, status, log filters, live output and update controls are represented in `web-client.js`; data continues through `dashboard_data` and overlays. Plan and question views use `plan_view` and `question_service`. General, Providers and Projects use the shared settings labels, help and defaults. The desktop local-folder and terminal buttons were intentionally removed under W-5. W-15 replaces the former pause and plan controls.

The German browser bootstrap uses the shared translation catalog. `test_web_ui.py` checks German labels and defaults, exercises client behavior and runs the browser smoke scenario covering controls, settings conflict, provider login, questions, updates and live output.

## W-7 follow-up audit

Compared the last desktop sources at `6ef5cda^` with the browser implementation, including the following views and their shared data sources:

| Former desktop feature | Current browser implementation | Findings |
| --- | --- | --- |
| Dashboard and live output | Dashboard, tile details, log controls and live panels; `dashboard_data`, `dashboard_overlays` | Dashboard values remain available. Fixed table headings and Orchestrator and Agent state translation; model names remain unchanged. |
| Plans and plan details | Plans and plan dialog; `plan_view` | Restored the Started column with translated yes/no values. Waiting reason and header errors remain separate visible columns. W-15 controls replace the former menu. |
| Questions and owner checks | Questions and owner-check cards; `question_service` | Question text, recommendation, Project-wide answers, package rejection, accept and reject remain available. |
| General settings | General; shared settings labels, help, build fields and defaults | General fields, build settings and installer remain available; web settings are added. |
| Project settings | Projects; shared project defaults and settings service | Repository, plans folder, integration, compile/build/deploy commands, rules and advanced settings remain available. Restored the warning identifying CLIs that load a local rule themselves even when it is switched off. Upstream files have no such warning. |
| Provider settings and provider form | Providers; `provider_labels`, CLI presets and settings service | Restored Subscription/API key/Local LLM choice labels and explicit Login present/missing text. Model classes, custom models, effort, tools, arguments, custom CLI settings, enable/disable, connection test and benchmark remain available. Browser login replaces the desktop terminal. |
| Provider benchmarks and detail overlays | Provider benchmark data, jobs and tile detail dialog | Shared measurements and overlay rows remain available. |

Regression evidence: before the fixes, the real browser test failed on the untranslated Runtime heading; the rule API test failed because native CLI information was absent. A further browser run failed on the missing Started column. The updated real browser scenario checks English and German, translated status and headings, access choice values and labels, login state, native rule warnings and unchanged model names. Bootstrap checks require every shared field label and help text to have a German catalog entry.

Focused verification passed: `test_web_ui.py` (14 tests, including the real browser), plus `test_i18n.py`, `test_web_api.py`, `test_web_settings.py`, `test_web_documentation.py`, `test_provider_form.py` and `test_profile.py` (89 tests combined). No full compile was run in this follow-up audit.

This is a source and behavior comparison. Desktop keyboard shortcuts and geometry are replaced by browser controls; local-folder and terminal buttons are removed as required. Arbitrary custom setting names, identifiers and owner/agent text remain as entered. Network acceptance and interactive tray behavior remain owner checks.

The W-14/W-15 review covered `force_stop.py`, the web action dispatch and command changes. The desktop-removal diff leaves `hives.py` and `inbox.py` unchanged. Focused tests passed: `test_web_force_stop.py` (11), `test_plan_command_names.py` (3), `test_hives_h1.py` (94) and `test_inbox.py` (15). These use isolated test data and do not operate on production processes.

## Requirement evidence and outstanding verification

| Requirement | Evidence reviewed | Status |
| --- | --- | --- |
| W-1 to W-4 | Web service, authentication, setup and listener test files | Existing coverage; not rerun in this audit |
| W-5 to W-7 | Desktop comparison and W-7 follow-up matrix above; browser and settings tests | Identified field and translation gaps fixed; focused regression checks recorded in the package report |
| W-8 | Browser settings conflict scenario in `test_web_ui.py` | Focused scenario passed |
| W-9 to W-10 | Desktop removal at `bdc99da` and desktop start test files | Existing coverage; not rerun in this audit |
| W-11 | Project rule and dependency policy | No new package added in this audit |
| W-12 | `test_web_documentation.py` | Existing coverage; not rerun in this audit |
| W-13 | Tray implementation and tests | Existing coverage; not rerun in this audit |
| W-14 | `test_web_force_stop.py` | Focused tests passed |
| W-15 | `test_plan_command_names.py`, browser and Queen lifecycle tests | Focused tests passed; full cross-surface audit remains open |

The owner network check and integration are outside this sub-branch audit and remain for the Queen.
