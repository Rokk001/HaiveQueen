# Package diagnosis without repeated no-progress repairs

Package retries remain single-use and bound to the commit, attempt history and
active decisions. Previously, changing the wording of a diagnosis could authorize
another identical repair at the same commit after an unsuccessful attempt.

When code and decisions have not changed after an authorized repair, the next
technical diagnosis must investigate why it made no progress and perform one
narrow, safe experiment. It must preserve project files and running processes.
The package-specific prompt requests an `experiment` object containing `question`,
`command`, integer `exit`, `finding` and a relative `log` filename. The nonempty
output file must exist inside the diagnosis run directory and be at most 4 MiB.
The diagnosis must explain how the observation changes the bounded repair and
its focused verification. The repair agent receives the finding and log reference.

HQ rejects missing evidence and previously used output bytes, even with a new
filename or different prose. A fresh observation can justify the same repair
approach. New attempt records alone do not count as progress. Consumed scopes
and evidence digests persist across restarts; legacy accepted reports retain
protection. Changed code or decisions allow a fresh diagnosis under the existing
scope checks. Real product decisions or external prerequisites remain eligible
for owner questions; exhausted attempts alone are not an owner decision.

This is an evidence gate, not proof that a model's causal interpretation is
correct. HQ validates the output file and its novelty; the diagnosis agent must
evaluate its meaning. Existing bounded protocol correction handles invalid
reports. No counters are reset, model cost is not raised automatically, and a
diagnosis is not treated as successful implementation or verification.

Only the retry needing this investigation receives the additional instructions.
Existing logs and history stay referenced rather than copied into every prompt.
