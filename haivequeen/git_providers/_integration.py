"""Shared normalization helpers for provider-owned integration operations."""

def reusable(row, sha, commit):
    if not commit:
        return True
    state = str(row.get("state", row.get("status", ""))).lower()
    merged = row.get("merged") is True or row.get("merged_at") or state in ("merged", "completed")
    return sha == commit if merged else state in ("open", "opened", "active")

def state(result):
    value = str(result.get("state", result.get("status", ""))).lower()
    if result.get("merged") is True or result.get("merged_at") or value in ("merged", "completed"):
        return "merged"
    if value in ("open", "opened", "active"):
        return "open"
    if value in ("closed", "declined", "abandoned"):
        return "closed"
    raise ValueError("unknown PR state %r" % value)

def merge_commit(result):
    return (result.get("merge_commit_sha") or result.get("squash_commit_sha") or result.get("merge_commit", {}).get("hash") or result.get("lastMergeCommit", {}).get("commitId") or "")

def pipeline(values):
    if any(v in ("failure", "failed", "error", "canceled", "cancelled", "stopped") for v in values):
        return "failure"
    if values and all(v in ("success", "successful", "succeeded", "passed") for v in values):
        return "success"
    return "pending"
