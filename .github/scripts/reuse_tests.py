"""Reuse a successful test workflow for the exact pushed commit, never a partial run."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def previous_success(request, commit, current_run):
    page = 1
    while True:
        query = urlencode({'head_sha': commit, 'event': 'push', 'per_page': 100, 'page': page})
        rows = request('/actions/workflows/test.yml/runs?' + query)['workflow_runs']
        matches = [r['id'] for r in rows if r['id'] < current_run and r.get('head_sha') == commit
                   and r.get('event') == 'push' and r.get('status') == 'completed'
                   and r.get('conclusion') == 'success']
        if matches:
            return max(matches)
        if len(rows) < 100:
            return None
        page += 1


def reused_run(env, request):
    if env['GITHUB_EVENT_NAME'] != 'push' or int(env['GITHUB_RUN_ATTEMPT']) > 1:
        return None  # A manual dispatch/rerun is an explicit request to verify again.
    return previous_success(request, env['GITHUB_SHA'], int(env['GITHUB_RUN_ID']))


def main():
    env = os.environ
    def request(path):
        url = env['GITHUB_API_URL'] + '/repos/' + env['GITHUB_REPOSITORY'] + path
        req = Request(url, headers={'Authorization': 'Bearer ' + env['GH_TOKEN'],
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'})
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    run = reused_run(env, request)
    with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write('run=' + ('false' if run else 'true') + '\n')
    message = ('Reusing successful tests for this exact commit: ' + env['GITHUB_SERVER_URL'] + '/' +
               env['GITHUB_REPOSITORY'] + '/actions/runs/' + str(run) if run else
               'No reusable successful test run; the complete platform matrix will run.')
    with Path(env['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
        summary.write(message + '\n')
    print(message)


if __name__ == '__main__':
    main()
