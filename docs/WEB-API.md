# Web engine API

The web service is independent of the Orchestrator. `bzz web start`, `stop`
and `restart` control the web listener; `bzz stop` keeps it available.
`bzz web setup` configures one account and enables network access.
`bzz web off` immediately rebinds a running listener to loopback.
Settings remain in `[web]` in config.toml: address (default 0.0.0.0),
port (8484), network (false before setup), certificate, key, username and
a salted PBKDF2 password hash. Both certificate files are required for HTTPS.
The service never generates certificates. HTTP setup and login warn that
the password travels unencrypted.

Before credentials exist, only the loopback setup page and setup submission
are available. The setup page carries a one-time CSRF token; POST `/setup`
requires it in `X-CSRF-Token` and accepts username, password, address, port,
certificate and key. Its response supplies the new login URL. Setup cannot
replace an existing account: run `bzz web setup` to reset credentials.

POST `/login` takes a JSON object with username and password and returns
`{ok, csrf}` with an HttpOnly, SameSite=Strict session cookie (Secure on HTTPS).
Every other page and API requires that cookie, including local requests.
Every authenticated POST also requires `X-CSRF-Token: <csrf>`; browser
requests from another origin are refused. POST `/logout` revokes the session.
Sessions expire after twelve hours and credential changes revoke them.
JSON objects are limited to 1 MiB. Errors return `{error}` with a failing
HTTP status; action results may also contain `ok: false` from the engine.

GET routes use query parameters; POST routes use JSON. All API routes begin
with `/api/`. Project and plan identify a registered project and its plan path.

| GET route | Parameters / response |
| --- | --- |
| state | optional project; shared dashboard snapshot, projects, controls and force warning |
| plans | shared list of all plans and their statuses |
| questions | optional project; open questions |
| settings | optional project; `{data, revision}` for config.toml or project.toml |
| providers | provider names, login and readiness |
| plan/providers | project, plan; eligible Queen providers with `current` marker |
| live | project, agent; bounded live output lines from the registered agent |
| login/status | id; captured output, clickable HTTP(S) links, running state and exit code |
| updates | update check state |
| benchmark | optional provider; stored context measurements |
| jobs/status | id; progress, running state, result and error of a long action |

| POST route | JSON fields |
| --- | --- |
| questions/answer | id, text, optional rejected packages and apiary |
| owner/accept, owner/reject, owner/release | project, plan; shared owner action fields (text, packages, commit) |
| provider | provider, provider_action (enable, disable, test) |
| provider/test | server object; test edited connection settings |
| settings/save | data, revision, optional project; stale saves are refused |
| login/start | provider; returns id for captured provider CLI login |
| login/input | id, text; sends one input line to that login |
| updates/check | manual update check |
| updates/install | version, confirmed: true |
| benchmark/start | provider, confirmed: true; starts a progress job |
| plan/start, plan/back-to-draft | project, plan; plan-list actions |
| plan/stop-gracefully, plan/force-stop, plan/resume, plan/cancel | project, plan |
| plan/restart-queen | project, plan, provider (required) |
| agent/force-stop | project, agent; shared single-agent stop rules |
| package/resume | project, plan, package |
| start, stop-gracefully | Orchestrator controls; browser Start opens no browser on the server |
| force-stop | confirmed: true; shared global stop |
| web/stop, web/restart | listener controls; response is sent before the listener changes |

Display `controls.force_warning` before offering the global Force stop
confirmation. The API refuses this action unless `confirmed` is exactly true.
`bzz stop --force` prints the same warning; its explicit flag is confirmation.
Global Force stop preserves plan status and completed pipeline steps. It ends
registered agent and build/deploy process trees and the Orchestrator alone;
the detached web service remains available for Start. Unfinished work is retried
without charging an interrupted attempt as a task failure.

There are no global Pause, Resume or Restart actions. Plan Restart Queen
always names an eligible provider and includes the current provider among the
choices. The interface labels are Start, Stop gracefully, Force stop, Resume,
Cancel plan, Restart Queen on provider, and web Stop/Restart.

Provider logins run in the existing isolated profile with captured stdout and
stdin. Codex uses device login. Claude uses its login command, and browser
launches are returned as URLs for the remote user. Custom CLIs retain their
configured login command. Render output as text and links as HTTP(S) anchors.

The root page is the dashboard package's rendering boundary. The local control
listener and service state file are private lifecycle implementation details;
they are separate from browser sessions. No external Python or browser package
is required.
