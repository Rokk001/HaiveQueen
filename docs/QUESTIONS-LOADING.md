# Questions loading

The Questions view shows a loading state until its first successful list response.
It does not claim zero pending questions or an empty inbox before that response.
Request failures remain visible and the normal refresh retries loading.

The web client requests compact question history: question text, answers, timestamps
and acceptance results remain available without expanding every question's plan
state, decisions and package list. The selected question loads its complete details
separately. Historical context and evidence load when their disclosure is opened.
A detail failure offers Retry. A bounded client cache coalesces concurrent requests
and invalidates the entry when the underlying question record changes.

`GET /api/questions?history=1&compact=1` is the lightweight list.
`GET /api/questions/detail?id=<id>` returns the complete question and evidence
through the same authenticated API. Existing non-compact API consumers are unchanged.
This changes loading and transfer cost, not question visibility, answer semantics
or acceptance decisions.
