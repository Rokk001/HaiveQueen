"""Interrupted agent ends: an end caused by a sleeping laptop, a lost network, an API error or a vanished process is not a failure of the task.

Such an end is marked interrupted (agent fields `interrupted` = kind and `retryAt` = ISO time of the next try, stored in the database) and the
task is tried again after a wait. Every counting of failures, escalation, locks and blocks ignores interrupted ends. The values here are fixed.
"""
import re
from datetime import datetime, timedelta

RATE_LIMIT = "rate_limit"   # API error: rate limit, overloaded, 429
API = "api"                 # API error: 5xx status
CONNECTION = "connection"   # connection lost (reset, refused, closed, timeout, DNS, stream disconnected)
SLEEP = "sleep"             # the wall clock jumped (laptop sleep) during the run
VANISHED = "vanished"       # the runner process is gone (window closed, killed, crash, lost by a Queen restart)

RATE_RE = re.compile(r"(?i)rate[ _-]?limit|overloaded|\b429\b")
API_RE = re.compile(r"(?i)(?:api error|status|http|error)[^\n]{0,20}\b5\d\d\b|internal server error|bad gateway|service unavailable|gateway time-?out")
CONNECTION_RE = re.compile(r"(?i)ECONNRESET|ECONNABORTED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|connection (?:reset|refused|closed)|socket hang up|fetch failed|"
                           r"stream disconnected|error sending request")
VANISHED_RE = re.compile(r"^(pane never started|runner process vanished|process gone after orchestrator restart)")
JUMP_SECONDS = 120   # a wall clock jump above this many seconds during a run counts as laptop sleep
BACKOFF_MINUTES = (1, 2, 4, 8)   # wait before the n-th retry of consecutive interrupted ends (API and connection errors); then LATER_MINUTES
LATER_MINUTES = 15
RATE_LIMIT_MINUTES = 15   # wait after a rate limit when no reset time is known


def classify_end(failed: bool, api_errors=(), stderr: str = "", clock_jumped: bool = False, last_line: str = "") -> str:
    """Kind of interruption of an agent end, or '' when it is a normal end or a normal failure.

    Only the error events of the agent (`api_errors`: error text of a Claude result with is_error, Codex turn.failed / error events) and its
    stderr are searched, never the normal answer text. An agent that ended successfully is never interrupted, also not after a clock jump.
    """
    if not failed:
        return ""
    if VANISHED_RE.match(str(last_line or "")):
        return VANISHED
    errors = " ".join(str(e) for e in api_errors)
    if RATE_RE.search(errors):
        return RATE_LIMIT
    if API_RE.search(errors):
        return API
    if CONNECTION_RE.search(errors) or CONNECTION_RE.search(str(stderr or "")):
        return CONNECTION
    return SLEEP if clock_jumped else ""


def is_interrupted(agent: dict) -> bool:
    """True when an agent row ended interrupted: marked in the database or (older rows) a vanished process by its last line."""
    return bool(agent.get("interrupted")) or bool(VANISHED_RE.match(str(agent.get("last_line") or "")) and agent.get("status") == "failed")


def kind_of(agent: dict) -> str:
    """Kind of the interruption of an agent row ('' when it is none)."""
    if agent.get("interrupted"):
        return str(agent["interrupted"])
    return VANISHED if is_interrupted(agent) else ""


def backoff_minutes(count: int) -> int:
    """Wait in minutes before the retry after `count` consecutive interrupted ends of the same task (API and connection errors)."""
    return BACKOFF_MINUTES[count - 1] if 1 <= count <= len(BACKOFF_MINUTES) else LATER_MINUTES


def retry_at(kind: str, count: int, now: datetime = None) -> str:
    """ISO time of the next try after an interrupted end; '' means at once in the next round (sleep, vanished process, rate limit: the
    rate-limit time depends on the providers and is worked out when the retry is due)."""
    now = now or datetime.now()
    if kind in (API, CONNECTION):
        return (now + timedelta(minutes=backoff_minutes(count))).isoformat(timespec="seconds")
    return ""
