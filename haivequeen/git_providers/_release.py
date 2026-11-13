"""Shared release response validation; provider endpoints stay in their modules."""
import re


def metadata(tag, date, commit, prerelease=None):
    """The web contract uses resolved object IDs, never target branch names."""
    match = re.fullmatch(r'v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?:-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.\d+)?', tag)
    pre = bool(match and '-' in tag) if prerelease is None else bool(prerelease)
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', commit):
        raise ValueError('release tag has no verifiable commit')
    return {'tag': tag, 'version': tag, 'date': date, 'commit': commit,
            'prerelease': pre, 'full_tag': 'v' + match[1] if match and pre and '-' in tag else ''}


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
