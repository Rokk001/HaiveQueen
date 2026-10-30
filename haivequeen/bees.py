"""Bee routing, package attempts and scoped Queen instructions."""
import json

from . import db, runtime, scheduler


# Return the next available configured class above the current one.
def next_class(providers, current, excluded):
    """Return the next class offered by a non-excluded provider, or None."""
    for name in scheduler.CLASS_NAMES[scheduler.class_index(current) + 1:]:
        if any(p['name'] not in excluded and (p.get('classes', {}).get(name) or {}).get('model') for p in providers):
            return name
    return None


# Pick a permitted free provider and class for a Bee.
def select_route(providers, free, hive_provider, package_class, capability, excluded, escalated, limited=()):
    """Pick a permitted Bee provider and class, returning None when it must wait."""
    providers = [p for p in providers if p['name'] not in excluded]
    image = 'images' in capability
    if image:
        candidates = [p for p in providers if p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])]
    elif escalated:
        candidates = providers
    else:
        candidates = [p for p in providers if p.get('endpoint') or p['name'] == hive_provider]
        candidates.sort(key=lambda p: not bool(p.get('endpoint')))
    index = scheduler.class_index(package_class)
    for cls in scheduler.CLASS_NAMES[index:]:
        matching = [p for p in candidates if (p.get('classes', {}).get(cls) or {}).get('model')]
        if not matching:
            continue
        for p in matching:
            if free.get(p['name'], 0) > 0 and p['name'] not in limited:
                return p['name'], cls
        return None
    return None


# Decide whether the package requires a Bee instead of the Queen.
def requires_bee(queen_class, package_class, parallel, codex_without_subagents):
    """Require a Bee for higher classes and eligible parallel work."""
    return scheduler.class_index(package_class) > scheduler.class_index(queen_class) or (parallel and not codex_without_subagents)


# Create the persistent per-package and class attempt table.
def ensure_attempts(conn):
    """Create persistent failure counters if absent."""
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempts (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project,plan_path,package,class))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_attempt_reports (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, attempt TEXT NOT NULL, PRIMARY KEY(project,plan_path,package,attempt))')
    conn.execute('CREATE TABLE IF NOT EXISTS bee_assignments (project TEXT NOT NULL, plan_path TEXT NOT NULL, package TEXT NOT NULL, class TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path,package))')


# Count a failed attempt, ignoring technical interruptions.
def record_attempt(conn, project, plan, package, cls, outcome, attempt=None):
    """Record one unsolved attempt and ignore interrupted ends."""
    ensure_attempts(conn)
    if outcome == 'failed':
        with db.transaction(conn):
            if attempt and conn.execute('INSERT OR IGNORE INTO bee_attempt_reports(project,plan_path,package,attempt) VALUES(?,?,?,?)', (project, plan, package, attempt)).rowcount == 0:
                return
            conn.execute('INSERT INTO bee_attempts(project,plan_path,package,class,failures) VALUES(?,?,?,?,1) ON CONFLICT(project,plan_path,package,class) DO UPDATE SET failures=failures+1', (project, plan, package, cls))


# Read failures for one package and class.
def failure_count(conn, project, plan, package, cls):
    """Read the persisted failure count for a package and class."""
    ensure_attempts(conn)
    row = conn.execute('SELECT failures FROM bee_attempts WHERE project=? AND plan_path=? AND package=? AND class=?', (project, plan, package, cls)).fetchone()
    return row[0] if row else 0


# Determine the current or next escalation class.
def next_attempt(conn, project, plan, package, cls, providers, excluded, threshold):
    """Return the current class, its next offered class, or None at the top."""
    if failure_count(conn, project, plan, package, cls) < threshold:
        return cls
    return next_class(providers, cls, excluded)


# Write only the package task, relevant paths and rules to a Bee.
def bee_prompt(name, package, task, steps, paths, rules):
    """Build a slim Bee task with numbered progress reports."""
    return ('Work only package %s: %s\nSteps:\n%s\nPaths: %s\nRules: %s\n'
            'Use your own worktree and sub-branch for code; merge locally through your Queen. '
            'Report each completed step with bzz note: %s %s: step n/%d <short text>. '
            'Check your HANDOFF file after each step; at context limit finish your current step, write a handoff, and end. '
            'Report unsolved work and failing tests to your Queen.' %
            (package, task, '\n'.join('%d. %s' % (i + 1, s) for i, s in enumerate(steps)), ', '.join(paths), '\n'.join(rules), name, package, len(steps)))


# Describe the plan's Queen and Bee workflow with configured limits.
def queen_instructions(queen_class, bee_limit, threshold, context_limit):
    """Return Queen task instructions for the configured classes and limits."""
    return ('''Your class is %s. Keep token use economical. Follow package dependencies: work one startable package yourself and run only independent packages with no shared files as subagents, at most %s Bees. A single package at your class or lower you work yourself; a higher class, including heavy, always goes to a Bee even alone. A Codex Queen may do an image package herself. If Codex exec cannot start subagents, work your class or lower sequentially and use separate Bee processes for higher classes and escalation.
Give every Bee a short female name. A Bee gets only its package task, numbered steps, needed paths and rules, never the whole plan or CLAUDE.md. Code Bees work on their own worktree and sub-branch and you merge locally. Each reports every step via bzz note. Use bzz bee start with the project, plan, package, class, name, task, numbered steps, paths, rules and the Bee worktree. Set --parallel only for packages independent of the package you work. When it returns subagent, start the Bee as your native subagent without another process. A Codex Queen without subagents supplies --no-subagents and works the returned queen task herself. Record each outcome with bzz bee result, a unique attempt name, the class that attempted it and outcome failed, interrupted or succeeded; for your own tries use your Queen class. When only a package waiting on a provider remains, use bzz bee wait with that provider, write a handoff and end. Prefer a free local provider offering the class; otherwise use your own provider. Codex is only for images or escalation, unless it is your own provider or another provider is needed because neither your provider nor the local model offers the required or a higher class. Images always use Codex; if unavailable ask the owner once when no eligible provider exists, otherwise continue independent work while waiting. Excluded providers are never used.
Count failed attempts per package and class persistently: an attempt is a Bee ending unsolved or with required tests red, or your own finished try that did not solve the package. Incompleteness by itself is never a reason to escalate. After %s failed attempts escalate to the next higher offered class; start the counter at zero in the new class. Never count an interruption, API or usage limit as failure. For image packages escalate only through Codex classes; after failures at the highest Codex class ask the owner. For other packages ask the owner after the same threshold at the highest available class. When a provider is busy or limited wait for it, never silently change it. Read each Bee result and start a replacement for the remaining package after a context handoff.
Never measure your context yourself: the token numbers of bzz agents and bzz usage are summed over all steps (Codex counts the whole context again at every step) and are far above the real context size. The Beekeeper watches your current context and writes the file HANDOFF in your run folder when it reaches %s; only when that file exists, start no new Bees, let running Bees finish, finish your current step, write a handoff and end. If only unanswered owner questions remain, continue independent work first, then handoff and wait for an answer. Preserve dependencies, branch, state and completed steps.
Before integration answer for each requirement with evidence (file, commit or test): Wurden alle Tasks aus dem Plan umgesetzt? Gibt es Anforderungen, welche nicht umgesetzt wurden? Write the answer into the state file and finish with Offene Anforderungen: 0 only when no requirement remains open. A partial requirement counts as open. Continue work while open. List owner-only play checks separately as owner questions without counting them as open: points only the owner can check in the game, and points the owner has set aside or excluded in a decision (bzz decisions list), are never counted in Offene Anforderungen; this wins over any project rule or Codex rule that says open or partial requirements block integration. If you cannot go on (rules contradict each other, a decision or a permission is missing), never end silently as unfinished: file one concrete question with bzz ask (project, plan, item, question, recommendation), write a handoff and end your final report with WAITING_FOR_OWNER. Then merge origin/<integrationBranch> into your branch, keep both sides, run the project compile check until green, register your own branch and perform the project's integration steps for your branch only. Wait for the project integration place and handoff if it is occupied.''' % (queen_class, bee_limit, threshold, context_limit))


# Set the Hive to wait for a particular Bee provider.
def wait_for_provider(conn, project, plan, provider, is_codex=None, reset_at=None):
    """Persist the provider a Hive is waiting for."""
    ensure_attempts(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS bee_provider_wait (project TEXT NOT NULL, plan_path TEXT NOT NULL, provider TEXT NOT NULL, PRIMARY KEY(project,plan_path))')
    with db.transaction(conn):
        conn.execute('INSERT INTO bee_provider_wait VALUES (?,?,?) ON CONFLICT(project,plan_path) DO UPDATE SET provider=excluded.provider', (project, plan, provider))
        conn.execute('UPDATE hives SET state=?,reset_at=? WHERE project=? AND plan_path=?', ('codex_wait' if (provider == 'codex' if is_codex is None else is_codex) else 'provider_wait', reset_at, project, plan))
        conn.execute('DELETE FROM hive_integration WHERE project=? AND plan_path=?', (project, plan))


# Wake a waiting Hive only when its provider has a slot and no limit.
def provider_available(conn, project, plan, provider, free, limited):
    """Wake the Hive when its waiting provider is usable."""
    if free <= 0 or limited:
        return False
    row = conn.execute('SELECT provider FROM bee_provider_wait WHERE project=? AND plan_path=?', (project, plan)).fetchone()
    if not row or row[0] != provider:
        return False
    with db.transaction(conn):
        conn.execute('DELETE FROM bee_provider_wait WHERE project=? AND plan_path=?', (project, plan))
        conn.execute("UPDATE hives SET state='waiting',reset_at=NULL WHERE project=? AND plan_path=?", (project, plan))
    return True


# Restrict image escalation to configured Codex providers.
def escalation_providers(env, plan, package):
    """Return the providers eligible to supply the next package class."""
    from . import routine
    if 'images' in routine.package_needs_for(env, plan, package):
        return [p for p in env.providers if p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])]
    return env.providers


# Record an attempt and file one owner question when the top class is exhausted.
def report_attempt(env, plan, package, cls, outcome, attempt=None):
    """Record an outcome and ask once after the highest class fails repeatedly."""
    record_attempt(env.conn, env.name, plan, package, cls, outcome, attempt)
    if outcome == 'succeeded':
        env.conn.execute('DELETE FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package))
    threshold = int(env.settings.get('escalateAfter', 3))
    excluded = env.settings.get('excludedProviders', [])
    if outcome == 'failed' and next_attempt(env.conn, env.name, plan, package, cls, escalation_providers(env, plan, package), excluded, threshold) is None:
        text = 'The package remains unsolved at the highest available class. How should it continue?'
        if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
            db.add_question(env.conn, env.name, plan, package, text, 'Review the failed attempts and choose the next approach.')


# Verify the Bee has a distinct worktree in the same repository.
def valid_bee_worktree(env, path, queen_cwd):
    """Check Git ownership and reject the Queen's checkout and the main checkout."""
    import subprocess
    from pathlib import Path
    path = Path(path)
    if not path.is_dir() or path.resolve() == env.code_repo.resolve() or (queen_cwd and path.resolve() == Path(queen_cwd).resolve()):
        return False
    def common(directory):
        """Return the canonical Git common directory of a checkout."""
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--path-format=absolute', '--git-common-dir'], capture_output=True, text=True, encoding='utf-8', errors='replace')
        return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    repo_common = common(env.code_repo)
    if repo_common is None or common(path) != repo_common:
        return False
    result = subprocess.run(['git', '-C', str(path), 'symbolic-ref', '--quiet', '--short', 'HEAD'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    return result.returncode == 0 and bool(result.stdout.strip())


# Check that a Queen works in a separate worktree of the project repository.
def valid_queen_worktree(env, path):
    """Reject the main checkout and directories outside the repository."""
    return valid_bee_worktree(env, path, None)


# Start a separate Bee process after validating context, memory, slots and routing.
def start_process(env, plan, package, name, cls, task, steps, paths, rules, cwd=None, terminal=None, no_subagents=False, parallel=False):
    """Start an eligible process Bee through the existing pane runner."""
    from pathlib import Path
    from . import hives, loop, routine, usage
    with loop.hive_schedule_lock():
        hive = hives.get(env.conn, env.name, plan)
        if not hive or not hives.can_start_bee(env.conn, env.name, plan, False, env.settings.get('contextLimitTokens', 300000)):
            return False
        active = runtime.list_agents(env.conn, None, 'active')
        queen = next((a for a in active if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'queen' and a.get('status') in ('running', 'starting')), None)
        if not queen:
            return False
        from . import platform as osplat
        running = [{**a, 'status': 'running'} for a in active if a.get('status') in ('running', 'starting') or (a.get('pid') and osplat.process_alive(a['pid']))]
        if sum(1 for a in running if a.get('project') == env.name and a.get('plan_path') == plan and a.get('phase') == 'bee') >= int(env.settings.get('beesPerQueen', 3)):
            return False
        package_row = next((p for p in env.packages(plan) if p['id'] == package), None)
        if not package_row:
            return False
        requested_class = package_row['cls']
        threshold = int(env.settings.get('escalateAfter', 3))
        excluded = env.settings.get('excludedProviders', [])
        need = routine.package_needs_for(env, plan, package)
        allowed = [p for p in env.providers if p['name'] not in excluded and
                   ((p.get('protocol') == 'codex' and 'images' in p.get('capabilities', [])) if 'images' in need else
                    (p.get('endpoint') or p['name'] == hive['provider']))]
        base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                           if any((p.get('classes', {}).get(name) or {}).get('model') for p in allowed)), None)
        use_other_provider = False
        if base_class is None and 'images' not in need:
            others = [p for p in env.providers if p['name'] not in excluded]
            base_class = next((name for name in scheduler.CLASS_NAMES[scheduler.class_index(requested_class):]
                               if any((p.get('classes', {}).get(name) or {}).get('model') for p in others)), None)
            use_other_provider = base_class is not None
        if base_class is None:
            text = ('No configured provider offers the images capability. How should this package continue?' if 'images' in need
                    else 'No configured provider offers the required class or a higher class. How should this package continue?')
            if not any(q.get('question') == text for q in runtime.list_questions(env.conn, env.name, plan=plan, item=package)):
                db.add_question(env.conn, env.name, plan, package, text, 'Configure an eligible provider.')
            return False
        current = base_class
        queen_class = env.settings.get('queenClass', 'medium')
        if (scheduler.class_index(requested_class) < scheduler.class_index(queen_class) and
                failure_count(env.conn, env.name, plan, package, queen_class) >= threshold):
            current = next_class(escalation_providers(env, plan, package), queen_class, excluded)
            if current is None:
                return False
        while failure_count(env.conn, env.name, plan, package, current) >= threshold:
            current = next_class(escalation_providers(env, plan, package), current, excluded)
            if current is None:
                return False
        if scheduler.class_index(cls) != scheduler.class_index(current) and not (current == base_class and scheduler.class_index(cls) == scheduler.class_index(requested_class)):
            return False
        queen_provider = scheduler.get_provider(env.providers, hive['provider'])
        image_needs_codex_bee = 'images' in need and (not queen_provider or queen_provider.get('protocol') != 'codex')
        if current == base_class and not image_needs_codex_bee and not requires_bee(env.settings.get('queenClass', 'medium'), current, parallel, no_subagents):
            return {'kind': 'queen', 'provider': hive['provider'], 'class': queen_class}
        free = scheduler.provider_free(env.providers, running)
        route_free = dict(free)
        if not no_subagents:
            route_free[hive['provider']] = max(1, route_free.get(hive['provider'], 0))
        limited = [p['name'] for p in env.providers if any(w['used_pct'] >= 100 for w in usage.limit_windows(env.conn, p))]
        ensure_attempts(env.conn)
        assigned = env.conn.execute('SELECT class,provider FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', (env.name, plan, package)).fetchone()
        if assigned and assigned[0] == current:
            provider = scheduler.get_provider(env.providers, assigned[1])
            if not provider or provider['name'] in excluded or provider['name'] in limited or route_free.get(provider['name'], 0) <= 0:
                return False
            route = (provider['name'], current)
        else:
            route = select_route(env.providers, route_free, hive['provider'], current, need, excluded, current != base_class or use_other_provider, limited)
        if not route:
            return False
        provider, selected_class = route
        env.conn.execute('INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?) ON CONFLICT(project,plan_path,package) DO UPDATE SET class=excluded.class,provider=excluded.provider', (env.name, plan, package, selected_class, provider))
        pick = scheduler.get_provider(env.providers, provider)
        if provider == hive['provider'] and not pick.get('endpoint'):
            if not no_subagents or pick.get('protocol') != 'codex':
                return {'kind': 'subagent', 'provider': provider, 'class': selected_class}
        if not hives.can_start_bee(env.conn, env.name, plan, True, env.settings.get('contextLimitTokens', 300000)) or loop.ram_gb() < loop._min_ram(env) or free.get(provider, 0) <= 0:
            return False
        path = Path(cwd or '')
        if not cwd or not valid_bee_worktree(env, path, queen.get('cwd')):
            return False
        existing = [a['name'] for a in runtime.list_agents(env.conn, env.name, 'all')]
        state = {'names': existing, 'busy': [], 'plan_status': {scheduler.norm_plan(plan): 'IN_PROGRESS'}, 'by_path': {scheduler.norm_plan(plan): {'path': plan}}}
        ctx = {'env': env, 'state': state, 'pool': loop.Pool(free, 0), 'report': [], 'dry': False, 'terminal': terminal, 'settle': 0, 'poll': 2, 'tries': 20}
        action = {'name': name, 'type': provider, 'phase': 'bee', 'plan': plan, 'package': package, 'cwd': str(path), 'prompt': bee_prompt(name, package, task, steps, paths, rules), 'class': selected_class}
        return bool(loop._do_start(ctx, action, forced_pick=pick))


# Read the branch from the 'Plan: X; branch Y; baseline Z' line of the plan's state file next to it.
def state_branch(env, plan):
    """Return the branch named in the state file beside the plan (<name>-STATE.md, with a trailing -PLAN of the name dropped), or ''."""
    import re
    from pathlib import Path
    stem = re.sub(r'-PLAN$', '', Path(plan).stem)
    for name in (Path(plan).stem + '-STATE.md', stem + '-STATE.md'):
        try:
            text = (env.workspace / Path(plan).parent / name).read_text(encoding='utf-8-sig')
        except OSError:
            continue
        found = re.search(r'(?m)^\s*Plan:[^\n]*?;\s*branch\s+([\w./-]+?)\s*;', text)
        if found:
            return found.group(1)
    return ''


# Resolve the plan's named branch and optional named worktree.
def plan_worktree(env, plan):
    """Read an unambiguous branch and worktree from the plan's explicit rules."""
    import re
    from pathlib import Path
    text = (env.workspace / plan).read_text(encoding='utf-8-sig')
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', text)
    feature = re.search(r'(?im)^\s*[-*]?\s*feature[ -]branch\s*:\s*`?([\w./-]+)`?', text)
    pair = re.search(r'(?im)\bworktree\s*`([^`]+)`\s*,?\s*branch\s*`([^`]+)`', text)
    reverse = re.search(r'(?im)(?<!sub-)\bbranch\s*`([^`]+)`\s+in\s+worktree\s*`([^`]+)`', text)
    git_line = re.search(r'(?im)^\s*[-*]?\s*git\s*:\s*branch\s*`([\w./-]+)`', text)
    named = feature or git_line
    if named:
        branch = named.group(1).rstrip('.')
        worktree = pair.group(1) if pair and pair.group(2) == branch else (reverse.group(2) if reverse and reverse.group(1) == branch else None)
    elif pair:
        worktree, branch = pair.groups()
    elif reverse:
        branch, worktree = reverse.groups()
    elif state_branch(env, plan):
        branch, worktree = state_branch(env, plan), None
    else:
        answers = runtime.list_questions(env.conn, env.name, status='answered', plan=plan, item='worktree')
        if not answers:
            return None
        lines = (answers[-1].get('answer') or '').strip().splitlines()
        if len(lines) != 2:
            return None
        branch, worktree = (line.strip() for line in lines)
        if not branch or not worktree:
            return None
    return branch, Path(worktree) if worktree else Path(__import__('haivequeen.routine', fromlist=['new_worktree']).new_worktree(env, scheduler.to_slug(branch)))


# Recognize a plan branch already owned by work outside the Beekeeper.
def foreign_plan_branch(env, plan):
    """Leave a queued or existing branch with no Hive history untouched."""
    import subprocess
    resolved = plan_worktree(env, plan)
    if not resolved:
        return False
    branch, _ = resolved
    if env.conn.execute("SELECT 1 FROM queue WHERE project=? AND branch=? AND status IN ('ready','blocked','in_run') LIMIT 1", (env.name, branch)).fetchone():
        return True
    if any(a.get('plan_path') == plan for a in runtime.list_agents(env.conn, env.name, 'all')):
        return False
    for ref in ('refs/heads/' + branch, 'refs/remotes/origin/' + branch):
        result = subprocess.run(['git', '-C', str(env.code_repo), 'show-ref', '--verify', '--quiet', ref], capture_output=True)
        if result.returncode == 0:
            return True
    return False


# Reuse or create the explicitly named branch in a separate Git worktree.
def prepare_queen_worktree(env, plan):
    """Prepare the plan branch without changing the main checkout."""
    import subprocess
    from pathlib import Path
    resolved = plan_worktree(env, plan)
    if resolved is None:
        return None
    branch, target = resolved
    git = ['git', '-C', str(env.code_repo)]
    listing = subprocess.run(git + ['worktree', 'list', '--porcelain'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if listing.returncode:
        return None
    entries = listing.stdout.replace('\r\n', '\n').split('\n\n')
    for entry in entries:
        fields = entry.splitlines()
        if 'branch refs/heads/' + branch in fields:
            directory = next((line[9:] for line in fields if line.startswith('worktree ')), '')
            if directory and Path(directory).resolve() != env.code_repo.resolve():
                return str(Path(directory))
            return None
    if target.exists() or target.resolve() == env.code_repo.resolve():
        return None
    exists = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/heads/' + branch], capture_output=True).returncode == 0
    if exists:
        args = ['worktree', 'add', str(target), branch]
    else:
        remote = subprocess.run(git + ['show-ref', '--verify', '--quiet', 'refs/remotes/origin/' + branch], capture_output=True).returncode == 0
        base = 'origin/' + (branch if remote else str(env.settings.get('integrationBranch', 'main')))
        args = ['worktree', 'add', '-b', branch, str(target), base]
    result = subprocess.run(git + args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        env.log('cannot prepare plan worktree: ' + result.stderr.strip())
        return None
    return str(target)
