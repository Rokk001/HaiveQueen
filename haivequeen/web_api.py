"""Authenticated web actions use the same engine functions as the owner UI."""
import hashlib
from contextlib import closing
from types import SimpleNamespace

from . import bees, config, db, filetxn, settings, dashboard_data, plan_view, question_service, hives, init, loop, owner, profile, runtime, servers, startctx, updates, web_auth, web_jobs, web_login, web_server
from . import scheduler as sch
from .force_stop import WARNING as FORCE_WARNING


def settings_path(conn, project=None):
    if not project:
        return config.config_dir() / 'config.toml'
    row = conn.execute('SELECT workspace FROM projects WHERE name=?', (project,)).fetchone()
    if not row:
        raise ValueError('Unknown project')
    from pathlib import Path
    return Path(row[0]) / '.haivequeen' / 'project.toml'


def revision(source):
    path, contents = source
    marker = b'\0missing' if contents is None else b'\0present\0' + contents
    return hashlib.sha256(path.encode('utf-8') + marker).hexdigest()


@filetxn.coordinated
def settings_read(conn, project=None):
    path = settings_path(conn, project)
    data = settings.load_raw(path)
    if not project and 'web' in data:
        data['web'] = {k: v for k, v in data['web'].items() if k != 'password'}
    return {'data': data, 'revision': revision(data.source)}


@filetxn.coordinated
def settings_save(conn, data):
    project = data.get('project')
    path = settings_path(conn, project)
    loaded = settings.load_raw(path)
    if data.get('revision') != revision(loaded.source):
        raise ValueError('Settings changed since loading; reload before saving')
    proposed = data['data']
    if not isinstance(proposed, dict):
        raise ValueError('Settings must be an object')
    proposed = dict(proposed)
    if project and data.get('place') is not None:
        place = data['place']
        if not isinstance(place, dict) or any(not isinstance(place.get(k, ''), str) for k in ('repository', 'plansFolder')):
            raise ValueError('Repository and plans folder must be paths')
        repository = place.get('repository', '').strip()
        plans_folder = place.get('plansFolder', '').strip()
        workspace = str(path.parent.parent)
        if not repository or not settings.same_dir(plans_folder or repository, workspace):
            from .i18n import tr
            raise ValueError(tr('The settings of this Project are kept in %s: enter that folder as plans folder or, when the plans are in the repository, as repository', workspace))
        if plans_folder and not settings.same_dir(repository, workspace):
            proposed['codeRepo'] = repository
        else:
            proposed.pop('codeRepo', None)
            proposed.pop('repo', None)
    if not project:
        current = web_auth.read(path)
        web = {**current, **proposed.get('web', {}), 'password': current['password']}
        web_server.validate_tls(web)
        if not isinstance(web['port'], int) or isinstance(web['port'], bool) or not 1 <= web['port'] <= 65535 or not isinstance(web['address'], str) or not web['address']:
            raise ValueError('A valid web address and port are required')
        if 'web' in proposed or current['password']:
            proposed['web'] = web
    errors = settings.check(proposed, machine=not bool(project))
    if project:
        from . import projsettings
        errors.extend(projsettings.validate_working_settings(proposed))
    if errors:
        raise ValueError('; '.join(errors))
    loaded.clear()
    loaded.update(proposed)
    if project:
        settings.save_project_file(conn, path, loaded)
    else:
        for provider in proposed.get('providers') or []:
            servers.settle(provider)
        init.register_listed(conn, proposed.get('projects'))
        settings.save_file(path, loaded)
    return {'ok': True, **settings_read(conn, project)}


@filetxn.coordinated
def settings_view(conn, project=None):
    """Derived browser form information, kept separate from editable TOML."""
    from . import cli_presets, projsettings, git_provider
    result = settings_read(conn, project)
    if project:
        workspace = str(settings_path(conn, project).parent.parent)
        repository, plans_folder = settings.apiary_place(workspace, result['data'])
        remote, _ = projsettings.derive_remote(repository, result['data'].get('remoteName'))
        result['data'] = projsettings.working_settings(result['data'])
        result['data'].pop('publishReleases', None)
        remote_url = projsettings._git(repository, 'remote', 'get-url', remote or 'origin')
        provider, errors = git_provider.discover((remote_url or '').strip())
        supported = {step: bool(provider and (provider.supports_step(step.title()) if step != 'release' else {'release_tag', 'release_state'} <= provider.capabilities)) for step in projsettings.STEPS}
        result['git_workflow'] = {'provider': provider.name if provider else '', 'supported': supported, 'errors': errors}
        branch = result['data'].get('integrationBranch') or projsettings.default_branch(repository, remote or 'origin') or 'main'
        result.update(workspace=workspace, repository=repository, place={'repository': repository, 'plansFolder': plans_folder},
                      project_defaults={'integrationBranch': branch},
                      rules=profile.rule_entries(result['data'], repository),
                      upstream=profile.upstream_state(result['data'], repository))
        result['rules'] = [{**rule, 'native_clis': profile.cli_clis(rule.get('name')) if not rule.get('upstream') else []}
                           for rule in result['rules']]
    else:
        migrated = settings.load_migrated(settings_path(conn))
        if 'web' in migrated:
            migrated['web'] = {k: v for k, v in migrated['web'].items() if k != 'password'}
        result['data'] = migrated
        offered = {}
        for provider in migrated.get('providers', []):
            key = cli_presets.key_for(provider)
            server = servers.server_of(provider)
            kept = servers.kept(server) if server else None
            offered[provider['name']] = {'models': list((kept or {}).get('models', [])) if server else cli_presets.model_list(key),
                                         'efforts': cli_presets.effort_list(key)}
        result.update(provider_models=offered, benchmark=startctx.pairs(conn),
                      build=settings.build_values(migrated), cores=settings.machine_cores())
    return result


def queen_providers(conn, project, plan):
    env = next((e for e in sch.load_projects(conn, config.load_config()) if e.name == project), None)
    if env is None:
        raise ValueError('Unknown project')
    cls = env.settings.get('queenClass', 'medium')
    current = (hives.get(conn, project, plan) or {}).get('provider')
    names = [{'name': p['name'], 'current': p['name'] == current,
              'selectable': p['name'] not in env.settings.get('excludedProviders', []) and not p.get('unavailable')}
             for p in env.providers
             if 'Queen' in p.get('capabilities', []) and (p.get('classes', {}).get(cls) or {}).get('model')]
    return env, names


def dispatch(action, data):
    with closing(db.connect(config.db_path())) as conn:
        return execute(conn, action, data)


def execute(conn, action, data):
    project, plan = data.get('project'), data.get('plan')
    if action == 'releases':
        from . import web_releases
        return web_releases.listing(conn, project)
    if action == 'releases/promote':
        from . import web_releases
        if data.get('confirmed') is not True:
            raise ValueError('Confirm publication of the selected pre-release as a full release')
        provider, working = web_releases.context(conn, project)
        return web_jobs.start('release-promotion:' + project,
                              lambda _progress: web_releases.promote(provider, working['releaseMode'], data['tag']))
    if action == 'git-login/providers':
        from . import git_provider
        return git_provider.login_options()
    if action == 'git-login/token':
        from . import git_provider
        options = next((row for row in git_provider.login_options() if row['name'] == data['provider']), None)
        if not options:
            raise ValueError('Unknown Git provider')
        if options.get('error'):
            raise ValueError(options['error'])
        if options.get('origin_required'):
            from urllib.parse import urlsplit
            origin = data.get('origin', '').rstrip('/')
            parsed = urlsplit(origin)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('Gitea requires an HTTPS origin')
            config.save_git_credential(data['provider'], data['token'], options.get('token_method', 'token'), origin=origin)
        else:
            config.save_git_credential(data['provider'], data['token'], options.get('token_method', 'token'))
        return {'ok': True}
    if action in ('git-login/start', 'git-login/poll'):
        raise ValueError('Git provider device sign-in is deferred; use an access token')
    if action == 'state':
        state = dashboard_data.snapshot(conn, project)
        for row in state.get('hives', []):
            row['stopped'] = bool(row.get('paused')) and hives.is_stopped(conn, row['apiary'], row['plan_path'])
        state['controls'] = {'orchestrator': ['Start', 'Stop gracefully', 'Force stop'], 'web': ['Stop', 'Restart'], 'force_warning': FORCE_WARNING}
        state['projects'] = db.rows(conn, 'SELECT name,workspace,colour,priority FROM projects ORDER BY name')
        return state
    if action == 'questions':
        questions = runtime.list_questions(conn, project, status='open')
        for question in questions:
            question['packages'] = question_service.plan_packages(conn, question['project'], question['plan_path'])
        return questions
    if action == 'plans':
        return plan_view.plan_rows(conn)
    if action == 'questions/answer':
        question_service.answer(conn, data['id'], data['text'], data.get('rejected', []), data.get('apiary', False))
        return {'ok': True}
    if action in ('owner/accept', 'owner/reject', 'owner/release', 'provider'):
        name = action.split('/')[-1]
        return owner.apply(conn, name, project, plan, **{k: v for k, v in data.items() if k not in ('project', 'plan')})
    if action == 'settings':
        return settings_read(conn, project)
    if action == 'settings/view':
        return settings_view(conn, project)
    if action == 'settings/save':
        return settings_save(conn, data)
    if action == 'providers':
        return [{'name': p['name'], 'logged_in': profile.has_login(p), 'ready': servers.readiness(p, profile.has_login(p))} for p in sch.get_providers(config.load_config())]
    if action == 'provider/test':
        result = servers.run_test(data['server'])
        return {'ok': True, 'result': result, 'message': servers.summary(data['server'])}
    if action == 'benchmark':
        return [p for p in startctx.pairs(conn) if not data.get('provider') or p['provider'] == data['provider']]
    if action == 'benchmark/start':
        if data.get('confirmed') is not True:
            raise ValueError('This starts the provider CLI twice per Project and uses login tokens. Confirm to continue.')
        provider = data['provider']
        owner.provider_settings(provider)
        machine = config.load_config()
        def measure(progress):
            with closing(db.connect(config.db_path())) as worker:
                return startctx.benchmark(worker, machine, provider, progress=progress)
        return web_jobs.start('benchmark:' + provider, measure)
    if action == 'jobs/status':
        return web_jobs.status(data['id'])
    if action == 'tools/install':
        from . import bzz_install
        return web_jobs.start('tools:install', lambda _progress: bzz_install.install())
    if action == 'live':
        row = runtime.get_agent(conn, project, data['agent'])
        if not row:
            raise ValueError('Unknown Agent')
        return {'lines': dashboard_data.live_lines(dashboard_data.live_path(row['project'], row['name']))}
    if action == 'login/start':
        _, machine, _ = owner.provider_settings(data['provider'])
        provider = next(p for p in sch.get_providers(machine) if p['name'] == data['provider'])
        return web_login.start(provider)
    if action == 'login/status':
        return web_login.status(data['id'])
    if action == 'login/input':
        return web_login.send(data['id'], data['text'])
    if action == 'updates':
        settled = updates.settle_install(db.last_self_update(conn))
        return {**updates.load_state(), 'restart_required': settled == 'ok'}
    if action == 'updates/check':
        return updates.check(config.load_config(), manual=data.get('automatic') is not True)
    if action == 'updates/install':
        if data.get('confirmed') is not True:
            raise ValueError(updates.TEXT_CONFIRM % data.get('version', ''))
        return updates.request_install(data['version'])
    if action == 'plan/providers':
        return queen_providers(conn, project, plan)[1]
    if action in ('plan/start', 'plan/back-to-draft', 'plan/stop-gracefully', 'plan/force-stop', 'plan/resume', 'plan/cancel', 'plan/restart-queen'):
        with loop.hive_schedule_lock():
            if not conn.execute('SELECT 1 FROM plans WHERE project=? AND path=?', (project, plan)).fetchone():
                runtime.sync_plans(conn, project)
            if action == 'plan/restart-queen':
                env, names = queen_providers(conn, project, plan)
                if data.get('provider') not in [p['name'] for p in names if p['selectable']]:
                    raise ValueError('Select a provider offering the Queen class')
                hives.requeen(conn, project, plan, data['provider'], providers=env.providers, queen_class=env.settings.get('queenClass', 'medium'), excluded=env.settings.get('excludedProviders', []))
            else:
                fn = {'plan/start': hives.start_plan, 'plan/back-to-draft': hives.back_to_draft, 'plan/stop-gracefully': hives.pause_plan, 'plan/force-stop': hives.stop_hive, 'plan/resume': hives.start_again, 'plan/cancel': hives.cancel_plan}[action]
                fn(conn, project, plan)
        return {'ok': True}
    if action == 'agent/force-stop':
        return owner.stop_agent(conn, project, data['agent'])
    if action == 'package/resume':
        bees.resume_package(conn, project, plan, data['package'])
        return {'ok': True}
    if action == 'force-stop':
        if data.get('confirmed') is not True:
            raise ValueError(FORCE_WARNING)
        from . import force_stop
        return force_stop.stop(conn)
    if action in ('start', 'stop-gracefully'):
        args = SimpleNamespace(foreground=False, rounds=0, round_minutes=0, keep_pause=False, desktop=False, no_browser=True, quiet=True, force=False)
        (loop.cmd_start if action == 'start' else loop.cmd_stop)(args, conn)
        return args.desktop_result
    raise ValueError('Unknown web action: ' + action)
