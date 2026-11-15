# Active agents in the dashboard

Hives shows every active process agent under its project and plan, including
package diagnoses, verification, acceptance, question handling and future roles.
Package diagnoses identify their package as `Diagnosis: <package>` in the task
column. Bees and embedded subagents retain their existing hierarchy; integration
repair agents retain their position under HQ. An agent is shown and counted once.

An active agent remains visible without a running Queen or existing Hive record,
and even if its plan has already been marked done or cancelled. This does not
reactivate the plan. Finished agents are not counted as busy.

Busy Agents and its details include Queens, process agents of all roles and
active embedded subagents. Starting processes count as occupied. Provider Limits
already counts process roles independently of their role name, plus embedded
slots; regression coverage checks consistency with the dashboard total for a
single provider. Disabled providers remain visible while their agents still occupy
slots, without becoming eligible for new work. Per-plan agent counts also include
diagnoses and other auxiliary roles. Resource limits and scheduling behavior are unchanged.
