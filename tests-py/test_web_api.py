import unittest
from types import SimpleNamespace
from unittest import mock

from bzztest import REL, RoundCase
from haivequeen import bees, db, dashboard_data, hives, loop, owner, runtime, startctx, updates, web_api, web_jobs, web_login


class WebApiTests(RoundCase):
    def test_workflow_saved_reloaded_and_empty_field_does_not_disable_step(self):
        from haivequeen import projsettings
        view = web_api.settings_view(self.conn, 'testproj')
        data = projsettings.select_working_way(view['data'], 'git')
        data = projsettings.select_step_mode(data, 'check', 'local')
        data.update(checkEnabled=True, compileCheck='check')
        web_api.settings_save(self.conn, {'project': 'testproj', 'revision': view['revision'], 'data': data})
        loaded = web_api.settings_view(self.conn, 'testproj')
        self.assertEqual(loaded['data']['wayOfWorking'], 'custom')
        self.assertTrue(loaded['data']['checkEnabled'])
        self.assertEqual(loaded['data']['checkMode'], 'local')
        loaded['data']['compileCheck'] = ''
        with self.assertRaisesRegex(ValueError, 'compileCheck is required'):
            web_api.settings_save(self.conn, {'project': 'testproj', 'revision': loaded['revision'], 'data': loaded['data']})
        self.assertTrue(web_api.settings_read(self.conn, 'testproj')['data']['checkEnabled'])

    def test_project_workflow_view_migrates_legacy_and_reports_capabilities(self):
        from haivequeen import git_provider, projsettings
        provider = SimpleNamespace(name='example', capabilities={'release_tag', 'release_state'},
                                   supports_step=lambda step: step != 'Build')
        with mock.patch.object(projsettings, '_git', return_value='https://example.invalid/owner/repo'), \
             mock.patch.object(git_provider, 'discover', return_value=(provider, [])):
            view = web_api.settings_view(self.conn, 'testproj')
        self.assertEqual(view['data']['wayOfWorking'], 'local')
        self.assertNotIn('publishReleases', view['data'])
        self.assertEqual(view['git_workflow']['provider'], 'example')
        self.assertFalse(view['git_workflow']['supported']['build'])
        self.assertTrue(view['git_workflow']['supported']['release'])
        with mock.patch.object(git_provider, 'discover', return_value=(None, [])):
            view = web_api.settings_view(self.conn, 'testproj')
        self.assertEqual(view['git_workflow']['provider'], '')
        self.assertFalse(any(view['git_workflow']['supported'].values()))

    def test_azure_workflow_disables_release_but_keeps_git_and_pipeline(self):
        from haivequeen import git_provider, projsettings
        from haivequeen.git_providers import azure
        provider = git_provider.Provider('azure', azure, ('org', 'project', 'repo'))
        with mock.patch.object(projsettings, '_git', return_value='https://dev.azure.com/org/project/_git/repo'), \
             mock.patch.object(git_provider, 'discover', return_value=(provider, [])):
            supported = web_api.settings_view(self.conn, 'testproj')['git_workflow']['supported']
        self.assertTrue(supported['merge'])
        self.assertTrue(supported['check'])
        self.assertFalse(supported['release'])

    def test_step_logs_are_isolated_by_plan_and_step(self):
        from haivequeen import orchestrator
        self.plan()
        orchestrator.save_step_log(self.conn, 'testproj', REL, 'Merge', 0, 'merge output')
        orchestrator.save_step_log(self.conn, 'testproj', REL, 'Check', 1, 'check output')
        orchestrator.save(self.conn, 'testproj', REL, {'phase':'fix','steps':{'Merge':'done','Check':'red'},'fix':{'step':'Check','error':'check failed'}})
        args={'project':'testproj','plan':REL}
        merge=web_api.execute(self.conn,'plan/step-log',{**args,'step':'Merge'})
        check=web_api.execute(self.conn,'plan/step-log',{**args,'step':'Check'})
        self.assertEqual(merge['output'],'merge output')
        self.assertEqual(merge['error'],'')
        self.assertEqual(check['output'],'check output')
        self.assertEqual(check['error'],'check failed')
        self.assertEqual(check['exit_code'],1)
        self.assertEqual(web_api.execute(self.conn,'plan/step-log',{**args,'step':'Build'})['output'],'')
        for change in ({'step':'unknown'},{'project':'other'},{'plan':'other.md'}):
            with self.assertRaises(ValueError):
                web_api.execute(self.conn,'plan/step-log',{**args,'step':'Merge',**change})


    def test_plan_detail_reads_registered_plan_and_rejects_other_paths(self):
        from pathlib import Path
        self.plan()
        workspace = Path(self.conn.execute("SELECT workspace FROM projects WHERE name='testproj'").fetchone()[0])
        expected = (workspace / REL).read_text(encoding='utf-8-sig')
        result = web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':REL})
        self.assertEqual(result['text'], expected)
        self.assertEqual(result['plan']['plan_path'], REL)
        for path in ('../private.txt', str(self.base / 'private.txt'), 'missing.md'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                web_api.execute(self.conn, 'plan/detail', {'project':'testproj','plan':path})



    def test_snapshot_reports_when_paused_plan_has_fully_stopped(self):
        row = {'apiary': 'testproj', 'plan_path': REL, 'paused': True}
        with mock.patch.object(dashboard_data, 'snapshot', return_value={'hives': [row]}), \
             mock.patch.object(hives, 'is_stopped', side_effect=[False, True]):
            self.assertFalse(web_api.execute(self.conn, 'state', {})['hives'][0]['stopped'])
            self.assertTrue(web_api.execute(self.conn, 'state', {})['hives'][0]['stopped'])

    def test_snapshot_uses_shared_dashboard_and_only_named_controls(self):
        with mock.patch.object(dashboard_data, 'snapshot', return_value={'hives': []}) as snapshot:
            result = web_api.execute(self.conn, 'state', {})
        snapshot.assert_called_once_with(self.conn, None)
        self.assertEqual(result['controls']['web'], ['Stop', 'Restart'])
        self.assertEqual(result['controls']['orchestrator'], ['Start', 'Stop gracefully', 'Force stop'])

    def test_owner_and_provider_actions_share_checked_engine(self):
        for name in ('accept', 'reject', 'release'):
            with self.subTest(name=name), mock.patch.object(owner, 'apply', return_value={'ok': True}) as apply:
                self.assertTrue(web_api.execute(self.conn, 'owner/' + name, {'project': 'testproj', 'plan': REL, 'text': 'reviewed'})['ok'])
                apply.assert_called_once_with(self.conn, name, 'testproj', REL, text='reviewed')
        with mock.patch.object(owner, 'apply', return_value={'ok': True}) as apply:
            web_api.execute(self.conn, 'provider', {'provider': 'codex', 'provider_action': 'enable'})
            apply.assert_called_once_with(self.conn, 'provider', None, None, provider='codex', provider_action='enable')

    def test_answer_uses_same_question_function(self):
        with mock.patch('haivequeen.question_service.answer') as answer:
            web_api.execute(self.conn, 'questions/answer', {'id': 3, 'text': 'Proceed', 'rejected': ['P1']})
        answer.assert_called_once_with(self.conn, 3, 'Proceed', ['P1'], False)

    def test_start_from_browser_does_not_open_a_browser_on_server(self):
        with mock.patch.object(loop, 'cmd_start') as start:
            start.side_effect = lambda a, c: setattr(a, 'desktop_result', {'ok': True})
            self.assertTrue(web_api.execute(self.conn, 'start', {})['ok'])
        self.assertTrue(start.call_args.args[0].no_browser)
        self.assertTrue(start.call_args.args[0].quiet)

    def test_force_requires_explicit_boolean_confirmation(self):
        with mock.patch('haivequeen.force_stop.stop', return_value={'ok': True}) as stop:
            for value in (None, False, 'true', 1):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Unfinished work'):
                    web_api.execute(self.conn, 'force-stop', {'confirmed': value})
            stop.assert_not_called()
            self.assertTrue(web_api.execute(self.conn, 'force-stop', {'confirmed': True})['ok'])
            stop.assert_called_once_with(self.conn)

    def test_plan_and_agent_actions_use_engine_functions(self):
        for action, module, name in [('stop-gracefully', hives, 'pause_plan'), ('force-stop', hives, 'stop_hive'), ('resume', hives, 'start_again'), ('cancel', hives, 'cancel_plan')]:
            with self.subTest(action=action), mock.patch.object(module, name) as call:
                web_api.execute(self.conn, 'plan/' + action, {'project': 'testproj', 'plan': REL})
                call.assert_called_once_with(self.conn, 'testproj', REL)
        with mock.patch.object(owner, 'stop_agent', return_value={'ok': True}) as stop:
            web_api.execute(self.conn, 'agent/force-stop', {'project': 'testproj', 'agent': 'worker'})
            stop.assert_called_once_with(self.conn, 'testproj', 'worker')
        with mock.patch.object(bees, 'resume_package') as resume:
            web_api.execute(self.conn, 'package/resume', {'project': 'testproj', 'plan': REL, 'package': 'P1'})
            resume.assert_called_once_with(self.conn, 'testproj', REL, 'P1')

    def test_unknown_and_global_pause_resume_restart_are_refused(self):
        for action in ('pause', 'resume', 'restart', 'dequeen', 'missing'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Unknown web action'):
                web_api.execute(self.conn, action, {})

    def test_settings_refuse_stale_revision_and_unknown_project(self):
        loaded = web_api.execute(self.conn, 'settings', {})
        first = web_api.execute(self.conn, 'settings/save', {**loaded, 'data': {'slots': 3}})
        self.assertEqual(first['data']['slots'], 3)
        with self.assertRaisesRegex(ValueError, 'changed since loading'):
            web_api.execute(self.conn, 'settings/save', {**loaded, 'data': {'slots': 7}})
        self.assertEqual(web_api.execute(self.conn, 'settings', {})['data']['slots'], 3)
        with self.assertRaisesRegex(ValueError, 'Unknown project'):
            web_api.execute(self.conn, 'settings', {'project': '../private'})

    def test_update_and_provider_login_actions(self):
        with mock.patch.object(updates, 'check', return_value={'available': True}) as check:
            self.assertTrue(web_api.execute(self.conn, 'updates/check', {})['available'])
            self.assertTrue(check.call_args.kwargs['manual'])
        with mock.patch.object(updates, 'request_install', return_value={'ok': True}) as install:
            with self.assertRaises(ValueError):
                web_api.execute(self.conn, 'updates/install', {'version': 'v1'})
            install.assert_not_called()
            web_api.execute(self.conn, 'updates/install', {'version': 'v1', 'confirmed': True})
            install.assert_called_once_with('v1')
        with mock.patch.object(web_login, 'send', return_value={'ok': True}) as send:
            web_api.execute(self.conn, 'login/input', {'id': 'run', 'text': 'code'})
            send.assert_called_once_with('run', 'code')

    def test_live_view_uses_registered_agent_not_arbitrary_path(self):
        with self.assertRaisesRegex(ValueError, 'Unknown Agent'):
            web_api.execute(self.conn, 'live', {'project': 'testproj', 'agent': '../../private'})

    def test_restart_queen_requires_named_eligible_provider_and_marks_current(self):
        self.plan('RUNNING')
        providers = [{'name': 'current', 'capabilities': ['Queen'], 'classes': {'medium': {'model': 'model'}}},
                     {'name': 'alternate', 'capabilities': ['Queen'], 'classes': {'medium': {'model': 'model'}}},
                     {'name': 'worker', 'capabilities': [], 'classes': {'medium': {'model': 'model'}}}]
        env = SimpleNamespace(name='testproj', settings={}, providers=providers)
        with mock.patch.object(web_api.sch, 'load_projects', return_value=[env]), mock.patch.object(hives, 'get', return_value={'provider': 'current'}), mock.patch.object(hives, 'requeen') as restart:
            choices = web_api.execute(self.conn, 'plan/providers', {'project': 'testproj', 'plan': REL})
            self.assertEqual(choices, [{'name': 'current', 'current': True, 'selectable': True}, {'name': 'alternate', 'current': False, 'selectable': True}])
            with self.assertRaisesRegex(ValueError, 'Select a provider'):
                web_api.execute(self.conn, 'plan/restart-queen', {'project': 'testproj', 'plan': REL})
            restart.assert_not_called()
            web_api.execute(self.conn, 'plan/restart-queen', {'project': 'testproj', 'plan': REL, 'provider': 'alternate'})
            self.assertEqual(restart.call_args.args[:4], (self.conn, 'testproj', REL, 'alternate'))

    def test_queen_menu_includes_blocked_providers_and_rejects_their_selection(self):
        self.plan('RUNNING')
        providers = [
            {'name': name, 'capabilities': ['Queen'], 'classes': {'heavy': {'model': 'model'}},
             'unavailable': name in ('offline', 'both')}
            for name in ('allowed', 'excluded', 'offline', 'both')
        ] + [
            {'name': 'worker', 'capabilities': [], 'classes': {'heavy': {'model': 'model'}}},
            {'name': 'wrong-class', 'capabilities': ['Queen'], 'classes': {'medium': {'model': 'model'}}},
            {'name': 'empty-model', 'capabilities': ['Queen'], 'classes': {'heavy': {'model': ''}}},
        ]
        env = SimpleNamespace(name='testproj', settings={'queenClass': 'heavy',
                              'excludedProviders': ['excluded', 'both']}, providers=providers)
        data = {'project': 'testproj', 'plan': REL}
        for current in ('excluded', 'offline', 'both'):
            with self.subTest(current=current), \
                 mock.patch.object(web_api.sch, 'load_projects', return_value=[env]), \
                 mock.patch.object(hives, 'get', return_value={'provider': current}), \
                 mock.patch.object(hives, 'requeen') as restart:
                choices = web_api.execute(self.conn, 'plan/providers', data)
                self.assertEqual(choices, [
                    {'name': name, 'current': name == current, 'selectable': name == 'allowed'}
                    for name in ('allowed', 'excluded', 'offline', 'both')
                ])
                for name in ('excluded', 'offline', 'both', 'worker', 'wrong-class', 'empty-model', 'unknown'):
                    with self.subTest(selection=name), self.assertRaisesRegex(ValueError, 'Select a provider'):
                        web_api.execute(self.conn, 'plan/restart-queen', {**data, 'provider': name})
                restart.assert_not_called()
                web_api.execute(self.conn, 'plan/restart-queen', {**data, 'provider': 'allowed'})
                self.assertEqual(restart.call_args.args[:4], (self.conn, 'testproj', REL, 'allowed'))

    def test_benchmark_requires_confirmation_and_exposes_progress_job(self):
        with mock.patch.object(web_jobs, 'start', return_value={'id': 'job'}) as start, mock.patch.object(owner, 'provider_settings'):
            with self.assertRaisesRegex(ValueError, 'uses login tokens'):
                web_api.execute(self.conn, 'benchmark/start', {'provider': 'codex'})
            start.assert_not_called()
            self.assertEqual(web_api.execute(self.conn, 'benchmark/start', {'provider': 'codex', 'confirmed': True})['id'], 'job')
            self.assertEqual(start.call_args.args[0], 'benchmark:codex')

    def test_settings_hide_and_preserve_password_hash(self):
        from haivequeen import config, web_auth
        path = config.config_dir() / 'config.toml'
        web_auth.setup(path, 'operator', 'secret', '127.0.0.1', 8484, '', '')
        old = web_auth.read(path)['password']
        loaded = web_api.execute(self.conn, 'settings', {})
        self.assertNotIn('password', loaded['data']['web'])
        loaded['data']['web']['port'] = 8485
        web_api.execute(self.conn, 'settings/save', loaded)
        self.assertEqual(web_auth.read(path)['password'], old)
        self.assertEqual(web_auth.read(path)['port'], 8485)

    def test_settings_revision_distinguishes_missing_from_empty_file(self):
        from haivequeen import config
        loaded = web_api.execute(self.conn, 'settings', {})
        (config.config_dir() / 'config.toml').write_text('', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed since loading'):
            web_api.execute(self.conn, 'settings/save', loaded)

    def test_update_status_settles_finished_install(self):
        with mock.patch.object(updates, 'settle_install', return_value='ok') as settle, mock.patch.object(db, 'last_self_update', return_value={'result': 'ok'}), mock.patch.object(updates, 'load_state', return_value={}):
            self.assertTrue(web_api.execute(self.conn, 'updates', {})['restart_required'])
        settle.assert_called_once_with({'result': 'ok'})
