"""Write-only secrets in browser settings; placeholders survive unrelated edits."""
import copy

MASK = '********'


def redact(data):
    result = copy.deepcopy(data)
    for section in ('providers', 'servers'):
        for entry in result.get(section) or []:
            if entry.get('apiKey'):
                entry['apiKey'] = MASK
    if 'web' in result:
        result['web'].pop('password', None)
    return result


def restore(proposed, current, raw=None):
    result = copy.deepcopy(proposed)
    if 'web' in result and current.get('web', {}).get('password'):
        result['web']['password'] = current['web']['password']
    for section in ('providers', 'servers'):
        originals = {entry.get('name'): entry for entry in (raw or {}).get(section) or []}
        originals.update({entry.get('name'): entry for entry in current.get(section) or []})
        for entry in result.get(section) or []:
            if entry.get('apiKey') == MASK:
                original = originals.get(entry.get('name'))
                if original is None or not original.get('apiKey'):
                    raise ValueError('Re-enter the API key after renaming or copying a provider')
                entry['apiKey'] = original['apiKey']
    return result
