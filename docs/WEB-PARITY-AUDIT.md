# Browser parity audit

Compared the removed desktop sources at `bdc99da^` with the browser sources at `2ffcfea`. The dashboard's tiles, active plans and agents, activity, system, queue, limits, status, log filters, live output and update controls are represented in `web-client.js`; data continues through `dashboard_data` and overlays. Plan and question views use `plan_view` and `question_service`. General, Providers and Projects use the shared settings labels, help and defaults. The desktop local-folder and terminal buttons were intentionally removed under W-5. W-15 replaces the former pause and plan controls.

The German browser bootstrap uses the shared translation catalog. `test_web_ui.py` checks German labels and defaults, exercises client behavior and runs the browser smoke scenario covering controls, settings conflict, provider login, questions, updates and live output. This comparison and these tests do not establish pixel-for-pixel equivalence or independently exercise every desktop field and language string; those remain an audit limitation.

The W-14/W-15 review covered `force_stop.py`, the web action dispatch and command changes. The desktop-removal diff leaves `hives.py` and `inbox.py` unchanged. Focused tests passed: `test_web_force_stop.py` (11), `test_plan_command_names.py` (3), `test_hives_h1.py` (94) and `test_inbox.py` (15). These use isolated test data and do not operate on production processes.

## Requirement evidence and outstanding verification

| Requirement | Evidence reviewed | Status |
| --- | --- | --- |
| W-1 to W-4 | Web service, authentication, setup and listener test files | Existing coverage; not rerun in this audit |
| W-5 to W-7 | Desktop comparison above and browser test file | Focused browser tests passed; exhaustive parity remains open |
| W-8 | Browser settings conflict scenario in `test_web_ui.py` | Focused scenario passed |
| W-9 to W-10 | Desktop removal at `bdc99da` and desktop start test files | Existing coverage; not rerun in this audit |
| W-11 | Project rule and dependency policy | No new package added in this audit |
| W-12 | `test_web_documentation.py` | Existing coverage; not rerun in this audit |
| W-13 | Tray implementation and tests | Existing coverage; not rerun in this audit |
| W-14 | `test_web_force_stop.py` | Focused tests passed |
| W-15 | `test_plan_command_names.py`, browser and Queen lifecycle tests | Focused tests passed; full cross-surface audit remains open |

The owner network check and integration are outside this sub-branch audit and remain for the Queen.
