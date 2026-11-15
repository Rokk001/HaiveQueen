# Provider wait recovery and status

A stored `codex_wait` or `provider_wait` records a request to wait, not proof that
a provider is still busy. The next normal scheduler round checks availability,
quota and slots again. When it wakes the plan it clears the old blocker as well.
A wait whose provider-wait record is missing is returned to normal scheduling;
this does not bypass RAM, quota, slot, question or plan eligibility checks.

An idle plan shows the current scheduler gate (stopped, stopping or paused)
before a stored provider blocker. Active work, integration, plan pauses and owner
questions retain their own statuses. When scheduling is running, a recorded
provider reason is displayed, such as exhausted quota, unknown usage or no free
slot. Without a recorded reason the UI says `Waiting for scheduler reassessment`,
not `Waiting for Codex`. Reading the dashboard never wakes a plan or changes the
database. Recovery runs only in the normal scheduler.
