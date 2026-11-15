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

HQ rejects missing evidence and previously used output bytes at the same commit
and decision scope, even with a new filename or different prose. Reproducing the
same output on a new commit is valid evidence that a defect persists. A fresh observation can justify the same repair
approach. New attempt records alone do not count as progress. Consumed scopes
and evidence digests persist across restarts; legacy accepted reports retain
protection. Changed code or decisions allow a fresh diagnosis under the existing
scope checks. Real product decisions or external prerequisites remain eligible
for owner questions; exhausted attempts alone are not an owner decision.

The last accepted technical diagnosis stores its own `accepted_scope`, separate
from the running review's `scope`. Starting or restarting a review must not move
that comparison baseline. A repair commit therefore permits a new diagnosis
without being misclassified as an unchanged retry during result collection.
For older records without this field, the retained accepted report supplies the
commit; the running review's commit is never used as evidence of a prior grant.

Experiment fingerprints persist with their commit and decision scope. Legacy
unscoped hashes protect the last accepted commit, never an unrelated newer commit;
attempt history is preserved. Repair instructions prioritize the diagnosed
boundary and its failing-before regression over a broader package task. A follow-up
diagnosis compares the repair result with that objective and reuses findings.
Supporting fixture changes must not be represented as completion of a different
diagnosed defect. These instructions guide the agents; HQ does not claim to verify
the semantic adequacy of a repair from a commit or output hash alone.

This is an evidence gate, not proof that a model's causal interpretation is
correct. HQ validates the output file and its novelty; the diagnosis agent must
evaluate its meaning. Existing bounded protocol correction handles invalid
reports. No counters are reset, model cost is not raised automatically, and a
diagnosis is not treated as successful implementation or verification.

Only the retry needing this investigation receives the additional instructions.
Existing logs and history stay referenced rather than copied into every prompt.
