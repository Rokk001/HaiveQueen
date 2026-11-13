"""Shared release response validation; provider endpoints stay in their modules."""


def workflows(rows):
    """Use the newest attempt of each workflow, never an earlier failed retry."""
    latest = {}
    for row in rows:
        key = row.get('workflow_id', row.get('name', row.get('id')))
        if key not in latest or int(row.get('id', 0)) > int(latest[key].get('id', 0)):
            latest[key] = row
    values = list(latest.values())
    if any(r.get('status') == 'completed' and r.get('conclusion') in
           ('failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure') for r in values):
        return 'failure'
    if values and all(r.get('status') == 'completed' and r.get('conclusion') == 'success' for r in values):
        return 'success'
    return 'pending'
