import json
from pathlib import Path
import unittest
from unittest import mock
from haivequeen import bees


class BeePolicyTests(unittest.TestCase):
    # Prepare providers for Bee routing tests.
    def setUp(self):
        self.providers = [
            {'name': 'cloud', 'protocol': 'claude', 'classes': {'medium': {'model': 'm'}, 'heavy': {'model': 'h'}}, 'capabilities': [], 'endpoint': None},
            {'name': 'local', 'protocol': 'claude', 'classes': {'medium': {'model': 'l'}}, 'capabilities': [], 'endpoint': 'http://localhost'},
            {'name': 'images', 'protocol': 'codex', 'classes': {'medium': {'model': 'i'}}, 'capabilities': ['images'], 'endpoint': None},
        ]
        self.free = {'cloud': 2, 'local': 1, 'images': 1}

    # Check local bee preferred when available.
    def test_local_bee_preferred_when_available(self):
        result = bees.select_route(self.providers, self.free, 'cloud', 'medium', [], [], False)
        self.assertEqual(result, ('local', 'medium'))

    # Check hive provider used when local busy.
    def test_hive_provider_used_when_local_busy(self):
        self.free['local'] = 0
        self.assertEqual(bees.select_route(self.providers, self.free, 'cloud', 'medium', [], [], False), ('cloud', 'medium'))

    def test_bee_falls_back_to_another_coding_provider_when_queen_full(self):
        from bzztest import provider
        providers = [provider('claude', 1), provider('codex', 2)]
        from haivequeen import scheduler
        providers = scheduler.get_providers({'providers': providers})
        self.assertEqual(bees.select_route(providers, {'claude': 0, 'codex': 2}, 'claude', 'medium', [], [], False), ('codex', 'medium'))
        self.assertIsNone(bees.select_route(providers, {'claude': 0, 'codex': 0}, 'claude', 'medium', [], [], False))

    # Check image waits if codex busy.
    def test_image_waits_if_codex_busy(self):
        self.free['images'] = 0
        self.assertIsNone(bees.select_route(self.providers, self.free, 'cloud', 'medium', ['images'], [], False))

    # Check image uses codex.
    @mock.patch("haivequeen.mcp_setup.status", return_value={"available": ["Image generation", "3D modelling"]})
    def test_image_uses_codex(self, verified_setup):
        self.assertEqual(bees.select_route(self.providers, self.free, 'cloud', 'medium', ['images'], [], False), ('images', 'medium'))

    # Check escalation skips unavailable classes.
    def test_escalation_skips_unavailable_classes(self):
        self.assertEqual(bees.next_class(self.providers, 'medium', []), 'heavy')
        self.assertIsNone(bees.next_class(self.providers, 'heavy', []))

    # Check excluded provider cannot supply escalation.
    def test_excluded_provider_cannot_supply_escalation(self):
        self.assertIsNone(bees.next_class(self.providers, 'medium', ['cloud']))

    # Check bee prompt is package only.
    def test_bee_prompt_is_package_only(self):
        prompt = bees.bee_prompt('Ada', 'P1', 'Implement P1', ['one', 'two'], ['source.py'], ['work rule'])
        self.assertIn('Ada P1: step n/2', prompt)
        self.assertIn('source.py', prompt)
        self.assertNotIn('CLAUDE.md', prompt)
        self.assertNotIn('other package', prompt)

    # Check queen prompt includes completeness and handoff.
    def test_queen_prompt_includes_completeness_and_handoff(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000)
        for text in ('Open requirements:', '300000', '3', 'integration', 'subagent'):
            self.assertIn(text.lower(), prompt.lower())

    # The Bee mode of a provider defaults by protocol and rejects unknown values.
    def test_bee_mode_defaults_and_validation(self):
        from haivequeen import scheduler
        got = {p['name']: p['beeMode'] for p in scheduler.get_providers({'providers': [
            {'name': 'a', 'protocol': 'claude'}, {'name': 'b', 'protocol': 'codex'}, {'name': 'c', 'protocol': 'claude', 'beeMode': 'process'}]})}
        self.assertEqual(got, {'a': 'subagent', 'b': 'process', 'c': 'process'})
        with self.assertRaises(ValueError):
            scheduler.get_providers({'providers': [{'name': 'x', 'protocol': 'claude', 'beeMode': 'agents'}]})

    # Check attempts survive reopen and ignore interruption.
    def test_attempts_survive_reopen_and_ignore_interruption(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'attempts.sqlite')
            conn = sqlite3.connect(path)
            bees.ensure_attempts(conn)
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'baseline', ['open'])
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'attempt', ['open'])
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'interrupted')
            conn.close()
            conn = sqlite3.connect(path)
            self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P1', 'medium'), 1)
            self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P1', 'heavy'), 0)
            conn.close()

    # Check escalated bee can use other configured provider.
    def test_escalated_bee_can_use_other_configured_provider(self):
        self.assertEqual(bees.select_route(self.providers, self.free, 'images', 'heavy', [], [], True), ('cloud', 'heavy'))

    # Check excluded codex is never selected for images.
    def test_excluded_codex_is_never_selected_for_images(self):
        self.assertIsNone(bees.select_route(self.providers, self.free, 'cloud', 'medium', ['images'], ['images'], False))

    # Check prompt instructs dependencies and higher classes.
    def test_prompt_instructs_dependencies_and_higher_classes(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for word in ('dependenc', 'every package goes to an agent', 'local', 'image', 'escalat', '3', 'handoff', 'returned cwd', 'without a tool call', 'in the foreground', 'never run an agent in the background', 'from the plan and package names, never the agent name'):
            self.assertIn(word, prompt)

    # A repeated report of the same attempt does not increment its failure count.
    def test_repeated_attempt_report_is_counted_once(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'baseline', ['open'])
        bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'bee-1', ['open'])
        bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'bee-1', ['open'])
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P1', 'medium'), 1)
        conn.close()

    # Check three failed attempts escalate and highest asks owner.
    def test_three_failed_attempts_escalate_and_highest_asks_owner(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.ensure_attempts(conn)
        for i in range(4):
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'medium-%s' % i, ['open'])
        self.assertEqual(bees.next_attempt(conn, 'p', 'plan', 'P1', 'medium', self.providers, [], 3), 'heavy')
        for i in range(4):
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'heavy', 'failed', 'heavy-%s' % i, ['open'])
        self.assertIsNone(bees.next_attempt(conn, 'p', 'plan', 'P1', 'heavy', self.providers, [], 3))
        conn.close()

    # Check interrupted attempt does not escalate.
    def test_interrupted_attempt_does_not_escalate(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.ensure_attempts(conn)
        for _ in range(3):
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'interrupted')
        self.assertEqual(bees.next_attempt(conn, 'p', 'plan', 'P1', 'medium', self.providers, [], 3), 'medium')
        conn.close()

    # Check queen prompt defines context handoff and waits for running bees.
    def test_queen_prompt_defines_context_handoff_and_waits_for_running_bees(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('no new', 'running agents', 'handoff', 'finish', '300000'):
            self.assertIn(text, prompt)

    # Check queen prompt requires completeness evidence before integrating.
    def test_queen_prompt_requires_completeness_evidence_before_integrating(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('requirement', 'evidence', 'open requirements: 0', 'integrat'):
            self.assertIn(text, prompt)

    # Check bee prompt requires step reports and own worktree.
    def test_bee_prompt_requires_step_reports_and_own_worktree(self):
        prompt = bees.bee_prompt('Ada', 'P1', 'Implement P1', ['one', 'two'], ['source.py'], ['work rule']).lower()
        for text in ('bzz note', 'worktree', 'sub-branch', 'step n/2'):
            self.assertIn(text, prompt)

    # Check next higher available class is used before escalation.
    def test_next_higher_available_class_is_used_before_escalation(self):
        self.providers[0]['classes'].pop('medium')
        self.providers[1]['classes'].pop('medium')
        self.assertEqual(bees.select_route(self.providers, self.free, 'cloud', 'medium', [], [], False), ('cloud', 'heavy'))

    # Check queen prompt requests minimal package rules not full plan.
    def test_queen_prompt_requests_minimal_package_rules_not_full_plan(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('only', 'package', 'rules', 'token'):
            self.assertIn(text, prompt)

    # Check queen prompt requires owner wait and continuing independent work.
    def test_queen_prompt_requires_owner_wait_and_continuing_independent_work(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('owner', 'independent', 'handoff'):
            self.assertIn(text, prompt)

    # Check queen prompt uses configured bee limit and attempts.
    def test_queen_prompt_uses_configured_bee_limit_and_attempts(self):
        prompt = bees.queen_instructions('light', 7, 4, 123456).lower()
        for text in ('light', '7', '4', '123456'):
            self.assertIn(text, prompt)

    # Check no codex for regular work before escalation.
    # With the Queen's provider and the local one full, regular work goes to another provider with a free slot, and waits when none has one.
    def test_regular_work_moves_to_another_provider_when_queen_and_local_full(self):
        self.free['cloud'] = 0
        self.free['local'] = 0
        self.assertEqual(bees.select_route(self.providers, self.free, 'cloud', 'medium', [], [], False), ('images', 'medium'))
        self.free['images'] = 0
        self.assertIsNone(bees.select_route(self.providers, self.free, 'cloud', 'medium', [], [], False))

    # Check codex fallback under codex queen.
    def test_codex_fallback_under_codex_queen(self):
        self.assertEqual(bees.select_route(self.providers, self.free, 'images', 'medium', [], [], False), ('local', 'medium'))
        self.free['local'] = 0
        self.assertEqual(bees.select_route(self.providers, self.free, 'images', 'medium', [], [], False), ('images', 'medium'))

    # Check image capability is not overridden by escalation.
    def test_image_capability_is_not_overridden_by_escalation(self):
        self.free['images'] = 0
        self.assertIsNone(bees.select_route(self.providers, self.free, 'cloud', 'medium', ['images'], [], True))


from bzztest import RoundCase, runtime


class QueenIntegrationTests(RoundCase):
    def setUp(self):
        super().setUp()
        import subprocess
        subprocess.run(['git', '-C', str(self.repo), 'init', '-b', 'main'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '--allow-empty', '-m', 'initial'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), 'remote', 'add', 'origin', str(self.repo)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), 'fetch', 'origin', 'main'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), 'branch', 'plan-example'], check=True, capture_output=True)

    def plan(self, *args, **kwargs):
        kwargs.setdefault('branch', 'plan-example')
        kwargs.setdefault('worktree', str(self.base / 'plan-worktree'))
        return super().plan(*args, **kwargs)

    def authorize_retry(self, env, current='medium', selected='heavy'):
        from haivequeen import package_diagnosis as review
        from bzztest import REL
        bees.prepare_agent_worktree(env, REL, 'P1')
        scope = review.scope(self.conn, 'testproj', REL, 'P1', review.current_commit(env, REL, 'P1'))
        report = {'kind': 'technical', **{k: scope[k] for k in ('commit', 'failure_log')},
                  'reviewed_decisions': [], 'evidence': ['caller.py:10 incorrect argument'],
                  'analysis': 'The wrong caller was repaired.', 'next_action': 'Fix the actual caller.',
                  'why_new': 'The actual failing caller is now identified.', 'repair_class': selected,
                  'class_reason': 'The repair spans multiple interfaces.'}
        path = self.base / 'diagnosis.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        review.accept(self.conn, 'testproj', REL, 'P1', path, scope, current, ['light', 'medium', 'heavy'])

    # Configure a separate local provider for process Bee tests.
    def add_local_provider(self):
        from bzztest import provider
        local = provider('local', 2, proto='codex')
        local['endpoint'] = {'baseUrl': 'http://localhost'}
        self.machine['providers'] = list(self.machine['providers']) + [local]

    # Check actual queen receives package and escalation rules.
    def test_actual_queen_receives_package_and_escalation_rules(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First', 'class': 'medium'}, {'id': 'P2', 'title': 'Second', 'class': 'heavy'}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        prompt = (self.env().run_dir(queen['name']) / 'prompt.md').read_text(encoding='utf-8-sig').lower()
        for text in ('escalat', 'dependencies', 'agents', 'open requirements: 0'):
            self.assertIn(text, prompt)

    # Check actual queen starts in separate worktree.
    def test_actual_queen_starts_in_separate_worktree(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        self.assertNotEqual(str(self.env().code_repo), queen['cwd'])



    # Check waiting for codex is persistent until slot and limit clear.
    def test_waiting_for_codex_is_persistent_until_slot_and_limit_clear(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        bees.wait_for_provider(self.conn, 'testproj', REL, 'codex')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'codex_wait')
        self.assertFalse(bees.provider_available(self.conn, 'testproj', REL, 'codex', 0, False))
        self.assertFalse(bees.provider_available(self.conn, 'testproj', REL, 'codex', 1, True))
        self.assertTrue(bees.provider_available(self.conn, 'testproj', REL, 'codex', 1, False))
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'waiting')

    # A missing provider stops only its package: the Hive may wait only when no other package can start.
    def test_a_package_for_a_missing_provider_does_not_stop_the_hive(self):
        from bzztest import REL
        images = {'name': 'codex', 'capabilities': ['images']}
        self.plan('READY', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}, {'id': 'W1', 'title': 'Window'},
                            {'id': 'FINAL', 'title': 'Final', 'depends': ['all']}])
        self.assertEqual(bees.runnable_packages(self.env(), REL, images), ['W1'])
        bees.set_package_state(self.conn, 'testproj', REL, 'W1', 'implemented')
        bees.set_package_state(self.conn, 'testproj', REL, 'I1', 'waiting', 'Bea', 'provider')
        self.assertEqual(bees.runnable_packages(self.env(), REL, images), [])

    # Check a capability that two providers offer does not stop the Hive when one of them is missing.
    @mock.patch("haivequeen.mcp_setup.status", return_value={"available": ["Image generation", "3D modelling"]})
    def test_a_shared_capability_runs_on_the_other_provider(self, verified_setup):
        from bzztest import REL, provider
        first, second = provider('claude', 2), provider('codex', 1, proto='codex')
        first['capabilities'], second['capabilities'] = ['blender'], ['blender', 'images']
        self.machine['providers'] = [first, second]
        self.plan('READY', [{'id': 'B1', 'title': 'Model', 'capability': 'blender'}, {'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        env = self.env()
        codex = next(p for p in env.providers if p['name'] == 'codex')
        self.assertEqual(bees.runnable_packages(env, REL, codex), ['B1'])

    # Check process bee refuses start when paused.
    def test_process_bee_refuses_start_when_paused(self):
        self.add_local_provider()
        from haivequeen import hives, runstate
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        runstate.pause(self.conn)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', [], [], [])
        self.assertFalse(result)
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    # Check process bee never starts after context limit.
    def test_process_bee_never_starts_after_context_limit(self):
        self.add_local_provider()
        from haivequeen import hives
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', ctxTokens=300000)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', [], [], [])
        self.assertFalse(result)
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    # Check bee process survives normal queen end.
    def test_bee_process_survives_normal_queen_end(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='done', pid=None)
        self.agent('Ada', phase='bee', status='running', provider='codex', pid=42)
        self.round(alive=lambda pid: pid == 42)
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'Ada')['status'], 'running')

    # Check bee process uses package class and rule subset.
    def test_bee_process_uses_package_class_and_rule_subset(self):
        self.add_local_provider()
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', pid=42)
        from unittest import mock
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'do first', ['one'], ['source.py'], ['work rule'], cwd=str(bee_wt))
        self.assertTrue(result)
        row = runtime.get_agent(self.conn, 'testproj', 'Ada')
        self.assertEqual(row['phase'], 'bee')
        self.assertEqual(row['class'], 'heavy')
        prompt = (self.env().run_dir('Ada') / 'prompt.md').read_text(encoding='utf-8-sig')
        self.assertIn('do first', prompt)
        self.assertNotIn('CLAUDE.md', prompt)

    # Exhaustion requires diagnosis, not a generic owner decision.
    def test_highest_class_failures_queue_one_diagnosis(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        for i in range(5):
            bees.report_attempt(self.env(), REL, 'P1', 'heavy', 'failed', 'attempt-%s' % i, ['open'])
        questions = runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1')
        self.assertEqual(questions, [])
        from haivequeen import package_diagnosis
        self.assertEqual(package_diagnosis._load(self.conn, 'testproj', REL, 'P1')['phase'], 'pending')




    # Start a separate Bee through the real CLI handler and retain its selected class.
    def test_queen_starts_bee_through_cli(self):
        self.add_local_provider()
        from unittest import mock
        from haivequeen import cli, hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', pid=42)
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        args = cli.build_parser().parse_args(['bee', 'start', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'heavy', '--name', 'Ada', '--task', 'first', '--steps', '["one"]', '--paths', '["a.py"]', '--rules', '["work"]', '--cwd', str(bee_wt)])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0)
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'Ada')['phase'], 'bee')


    # The real round starts its Queen in the newly prepared plan branch worktree.
    def test_round_starts_queen_in_real_named_worktree(self):
        import subprocess
        from pathlib import Path
        from haivequeen import loop
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        with (self.repo / REL).open('a', encoding='utf-8') as plan:
            plan.write('\n- Feature branch: plan-example, created from origin/main.\n')
        def git(*args):
            """Run Git in the temporary project repository."""
            return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        git('init', '-b', 'main')
        (self.repo / 'tracked.txt').write_text('start', encoding='utf-8')
        git('add', 'tracked.txt')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'initial')
        git('fetch', 'origin', 'main')
        runtime.kv_set(self.conn, 'testproj', bees.TAKEOVER_KEY + REL, 'plan-example')
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        self.assertNotEqual(Path(queen['cwd']).resolve(), self.repo.resolve())
        branch = subprocess.run(['git', '-C', queen['cwd'], 'branch', '--show-current'], check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, 'plan-example')

    # A provider reset wakes the Beekeeper without waiting for the periodic round.
    def test_provider_reset_changes_wake_marker(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        bees.wait_for_provider(self.conn, 'testproj', REL, 'codex', reset_at='2999-01-01T00:00:00')
        before = hives.wake_marker(self.conn, self.envs())
        self.conn.execute('UPDATE hives SET reset_at=? WHERE project=? AND plan_path=?', ('2000-01-01T00:00:00', 'testproj', REL))
        after = hives.wake_marker(self.conn, self.envs())
        self.assertNotEqual(before, after)
        self.assertFalse(before[4])
        self.assertTrue(after[4])

    # The scheduling round keeps a Hive waiting while Codex is occupied and only Codex offers the package's capability, then resumes it.
    def test_round_resumes_codex_wait_only_after_slot_is_free(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        bees.wait_for_provider(self.conn, 'testproj', REL, 'codex')
        self.agent('bee-a', phase='bee', provider='codex', type='codex', status='running', pid=41)
        self.agent('bee-b', phase='bee', provider='codex', type='codex', status='running', pid=42)
        self.round(alive=lambda pid: pid in (41, 42))
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'codex_wait')
        self.agent('bee-b', phase='bee', provider='codex', type='codex', status='done', pid=None)
        self.round(alive=lambda pid: pid == 41)
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'running')
        self.assertTrue(any(a['phase'] == 'queen' and a['status'] == 'running' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    def test_embedded_bees_count_toward_queen_limit(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}, {'id': 'P2', 'title': 'Second'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        env.settings['beesPerQueen'] = 1
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'Ada', 'medium', 'claude')
        self.assertFalse(bees.start_process(env, REL, 'P2', 'Bea', 'medium', 'task', ['one'], [], []))
        self.assertIn('already has', bees.LAST_REFUSAL)

    # Reuse the first package worktree across native retries and escalation.
    def test_bee_attempts_reuse_package_worktree(self):
        import subprocess
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        first = self.base / 'first'
        other = self.base / 'other'
        def git(*args):
            """Run Git against the temporary project repository."""
            subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)
        git('init', '-b', 'main')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
        git('worktree', 'add', '-b', 'plan-example-p1', str(first))
        (first / 'uncommitted.txt').write_text('keep', encoding='utf-8')
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], cwd=str(first))
            self.assertEqual((result['kind'], result['cwd']), ('subagent', str(first)))
            self.assertFalse(bees.start_process(env, REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(other)))   # Ada's end is not confirmed yet
            bees.report_attempt(env, REL, 'P1', 'medium', 'interrupted', 'Ada')
            result = bees.start_process(env, REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(other))
            self.assertEqual(Path(result['cwd']), first)
            for i in range(4):
                bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'try-%s' % i,
                                    open_requirements=['unfinished'], error='test failed')
            self.authorize_retry(env)
            result = bees.start_process(env, REL, 'P1', 'Cora', 'heavy', 'task', ['one'], [], [], cwd=str(other))
            self.assertEqual(Path(result['cwd']), first)
        self.assertEqual((first / 'uncommitted.txt').read_text(encoding='utf-8'), 'keep')

    # Existing Bee history retains a package worktree after upgrading.
    def test_retry_recovers_existing_bee_worktree_from_history(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        first = self.base / 'first'
        import subprocess
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'add', '-b', 'legacy-agent', str(first)], check=True, capture_output=True)
        self.agent('old', phase='bee', package='P1', status='done', cwd=str(first))
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], cwd=str(self.base / 'other'))
        self.assertEqual(Path(result['cwd']), first)

    # A process retry receives and reports the first attempt's worktree.
    def test_process_bee_retry_uses_existing_worktree(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        first = Path(bees.prepare_agent_worktree(self.env(), REL, 'P1')[0])
        other = self.base / 'other'
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), mock.patch('haivequeen.loop._do_start', return_value=True) as start:
            bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(first))
            result = bees.start_process(self.env(), REL, 'P1', 'Bea', 'heavy', 'task', ['one'], [], [], cwd=str(other))
        self.assertEqual(Path(result['cwd']), first)
        self.assertEqual(start.call_args.args[1]['cwd'], str(first))

    # The Queen never works a package herself: even one on her own class goes to a Bee.
    def test_every_package_goes_to_a_bee(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [])
        self.assertEqual((result['kind'], result['class']), ('subagent', 'medium'))
        row = self.conn.execute('SELECT name, class, ended_at FROM embedded_bees').fetchone()   # the window shows the embedded Bee until it reports
        self.assertEqual((row['name'], row['class'], row['ended_at']), ('Ada', 'medium', None))
        bees.report_attempt(self.env(), REL, 'P1', 'medium', 'succeeded', 'Ada')
        self.assertIsNotNone(self.conn.execute('SELECT ended_at FROM embedded_bees').fetchone()['ended_at'])

    def test_full_queen_provider_moves_assigned_bee_to_process_provider(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][0]['slots'] = 1
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?)', ('testproj', REL, 'P1', 'medium', 'claude'))
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), mock.patch('haivequeen.loop._do_start', return_value=True):
            result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], cwd=str(wt))
        self.assertEqual((result['kind'], result['provider']), ('process', 'codex'))
        self.assertEqual(self.conn.execute('SELECT provider FROM bee_assignments WHERE package=?', ('P1',)).fetchone()[0], 'codex')

    def test_raise_to_queen_class_is_logged(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'light'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        for i in range(4):
            bees.report_attempt(env, REL, 'P1', 'light', 'failed', 'attempt-%s' % i, ['open'])
        bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [])
        self.assertIn('P1', self.log())
        self.assertIn('diagnosis', self.log())

    # The provider setting beeMode process runs the Bee of the Queen's own provider as a separate agent.
    def test_bee_mode_process_starts_a_separate_agent(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][0]['beeMode'] = 'process'
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], cwd=str(bee_wt))
        self.assertEqual((result['kind'], result['provider']), ('process', 'claude'))
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'Ada')['phase'], 'bee')

    # A Codex Queen without subagents cannot start a Bee without a free slot and is told why.
    def test_codex_queen_fallback_needs_a_slot_and_names_the_reason(self):
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][1]['slots'] = 1
        self.machine['providers'][0]['slots'] = 0
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='running', provider='codex', type='codex', pid=42)
        self.assertFalse(bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], no_subagents=True))
        self.assertTrue(bees.LAST_REFUSAL)
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    # A lower class cannot override the package class in the plan.
    def test_bee_start_refuses_lower_class_than_plan(self):
        from haivequeen import hives
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.assertFalse(bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], cwd=str(self.wt)))
        self.assertIsNone(runtime.get_agent(self.conn, 'testproj', 'Ada'))

    # An unsuccessful Queen escalates above her own class, not above a lower package class.
    def test_queen_failure_escalates_above_queen_class(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'light'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        for i in range(4):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'queen-%s' % i, ['open'])
        self.authorize_retry(env)
        result = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [])
        self.assertEqual((result['kind'], result['class']), ('subagent', 'heavy'))

    # If neither the Hive nor local provider offers the class, use another configured provider.
    def test_other_provider_when_hive_and_local_cannot_offer_class(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][1]['classes'].pop('heavy')
        self.add_local_provider()
        self.machine['providers'][2]['classes'].pop('heavy')
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='running', provider='codex', type='codex', pid=42)
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt), no_subagents=True)
        self.assertTrue(result)
        row = runtime.get_agent(self.conn, 'testproj', 'Ada')
        self.assertEqual((row['provider'], row['class']), ('claude', 'heavy'))

    # Three recorded failures route the next real Bee start to a provider offering heavy.
    def test_escalation_starts_heavy_bee_on_other_provider(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][0]['classes'].pop('heavy')
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        for i in range(4):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'attempt-%s' % i, ['open'])
        self.authorize_retry(env)
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt))
        self.assertTrue(result)
        row = runtime.get_agent(self.conn, 'testproj', 'Ada')
        self.assertEqual((row['provider'], row['class']), ('codex', 'heavy'))

    # A package already escalated to heavy keeps that class when its failure counters read zero again.
    def test_stored_escalation_survives_reset_counters(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments VALUES (?,?,?,?,?)', ('testproj', REL, 'P1', 'heavy', 'claude'))
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [])
        self.assertEqual((result['kind'], result['class']), ('subagent', 'heavy'))

    # A refused start says why instead of only reporting that it waits.
    def test_refused_start_names_the_reason(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.assertFalse(bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], []))
        self.assertIn('class heavy was requested', bees.LAST_REFUSAL)

    # The stored provider of an interrupted Bee at its limit does not keep the Bee waiting: it moves to the provider it starts on.
    def test_interrupted_bee_moves_from_limited_assigned_provider(self):
        from unittest import mock
        from haivequeen import hives
        from bzztest import REL
        self.add_local_provider()
        self.machine['providers'][-1].pop('endpoint')  # A Codex subscription has a derived limit source
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments VALUES (?,?,?,?,?)', ('testproj', REL, 'P1', 'heavy', 'local'))
        env = self.env()
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        limits = self.base / 'limits.json'
        limits.write_text('{"local": {"usedPercent": 100, "resetHours": 24}, "claude": {"usedPercent": 0, "resetHours": 24}}', encoding='utf-8')
        with mock.patch.dict('os.environ', {'BZZ_PROVIDER_LIMITS_FILE': str(limits)}), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt))
        self.assertEqual((result['kind'], result['provider']), ('subagent', 'claude'))
        self.assertEqual(self.conn.execute('SELECT provider FROM bee_assignments WHERE package=?', ('P1',)).fetchone()[0], 'claude')

    # A stored provider that is no longer allowed for the package is replaced instead of blocking the Bee.
    def test_stale_codex_assignment_is_routed_again(self):
        from unittest import mock
        from haivequeen import hives, usage
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments VALUES (?,?,?,?,?)', ('testproj', REL, 'P1', 'heavy', 'codex'))
        env = self.env()
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch.object(usage, 'limit_windows', return_value=[]), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            self.assertTrue(bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt)))
        self.assertNotEqual(runtime.get_agent(self.conn, 'testproj', 'Ada')['provider'], 'codex')
        self.assertNotEqual(self.conn.execute('SELECT provider FROM bee_assignments WHERE package=?', ('P1',)).fetchone()[0], 'codex')

    # A plan-named branch with a foreign ready row is not adopted by a new Hive.
    def test_foreign_ready_branch_is_left_untouched(self):
        from unittest import mock
        from haivequeen import loop
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        with (self.repo / REL).open('a', encoding='utf-8') as plan:
            plan.write('\n- Feature branch: external-work.\n')
        runtime.queue_upsert(self.conn, 'testproj', 'external-work', tip='abc', status='ready')
        with mock.patch('haivequeen.bees.prepare_queen_worktree', return_value=str(self.wt)):
            loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertFalse(any(a['phase'] == 'queen' for a in runtime.list_agents(self.conn, 'testproj', 'all')))
        self.assertEqual(self.conn.execute("SELECT status FROM queue WHERE branch='external-work'").fetchone()[0], 'ready')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM hives WHERE project=? AND plan_path=?', ('testproj', REL)).fetchone())

    # A missing image-capable provider creates just one owner question.
    def test_image_without_provider_asks_once(self):
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'] = self.machine['providers'][:1]
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'Image', 'class': 'medium', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        env = self.env()
        for _ in range(2):
            self.assertFalse(bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'image', ['one'], [], [], cwd=str(self.wt)))
        questions = runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1')
        self.assertEqual(len(questions), 1)
        from haivequeen import db
        db.answer_question(self.conn, questions[0]['id'], 'Later', 'owner')
        self.assertFalse(bees.start_process(env, REL, 'P1', 'Ada', 'medium', 'image', ['one'], [], [], cwd=str(self.wt)))
        self.assertEqual(len(runtime.list_questions(self.conn, 'testproj', plan=REL, item='P1')), 1)

    # An image package also needs diagnosis at its highest available class.
    def test_image_escalation_stops_at_highest_codex_class(self):
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][1]['classes'].pop('heavy')
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'Image', 'class': 'medium', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        env = self.env()
        for i in range(4):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'attempt-%s' % i, ['open'])
        self.assertEqual(runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1'), [])
        from haivequeen import package_diagnosis
        self.assertEqual(package_diagnosis._load(self.conn, 'testproj', REL, 'P1')['phase'], 'pending')
        self.assertIsNone(bees.next_attempt(self.conn, 'testproj', REL, 'P1', 'medium', bees.escalation_providers(env, REL, 'P1'), [], 3))

    # A separate Bee process cannot start below the configured free-RAM minimum.
    def test_process_bee_requires_free_ram(self):
        from unittest import mock
        from haivequeen import hives, loop
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        with mock.patch.object(loop, 'ram_gb', return_value=0):
            self.assertFalse(bees.start_process(self.env(), REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(self.wt)))
        self.assertIsNone(runtime.get_agent(self.conn, 'testproj', 'Ada'))

    # A replacement Queen receives a name different from her predecessor after a long rest.
    def test_replacement_queen_does_not_reuse_predecessor_name_after_long_rest(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('old-queen', phase='queen', status='done', person='Ada', ended=180, pid=None)
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen' and a['status'] == 'running')
        self.assertNotEqual(queen['person'], 'Ada')

    # A Bee note identifies its Queen or its own process in the event history.
    def test_bee_note_keeps_agent_identity(self):
        from unittest import mock
        from haivequeen import cli
        args = cli.build_parser().parse_args(['note', '--project', 'testproj', '--text', 'Ada P1: step 1/2 work'])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}):
            self.assertEqual(cli.cmd_note(args, self.conn), 0)
        row = self.conn.execute("SELECT agent,text FROM events WHERE kind='note' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row['agent'], row['text']), ('queen', 'Ada P1: step 1/2 work'))



class NativeBeePauseTests(RoundCase):
    # Pause policy needs no real Git repository or worktree preparation.
    def setUp(self):
        super().setUp()
        self.mock_agent_worktrees()

    # A running native Bee keeps its provider slot during a pause; new starts are refused.
    def test_native_bee_takes_a_slot_and_continues_during_pause(self):
        from haivequeen import hives, runstate, scheduler
        from bzztest import REL
        self.machine['providers'][0]['slots'] = 3
        self.plan('READY', [{'id': 'P1', 'title': 'First'}, {'id': 'P2', 'title': 'Second'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [])
        self.assertTrue(result, bees.LAST_REFUSAL)
        self.assertEqual(result['kind'], 'subagent')
        self.assertEqual(scheduler.embedded_slots(self.conn), {'claude': 1})
        runstate.pause(self.conn)
        self.round(alive=lambda pid: pid == 42)
        self.assertEqual(scheduler.embedded_slots(self.conn), {'claude': 1})
        row = self.conn.execute('SELECT name, ended_at FROM embedded_bees WHERE package=?', ('P1',)).fetchone()
        self.assertEqual((row['name'], row['ended_at']), ('Ada', None))
        self.assertFalse(bees.start_process(self.env(), REL, 'P2', 'Bea', 'medium', 'task', [], [], []))
        self.assertIn('paused', bees.LAST_REFUSAL)
        self.assertEqual(scheduler.embedded_slots(self.conn), {'claude': 1})
        self.assertEqual(self.ag('queen')['status'], 'running')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM embedded_bees').fetchone()[0], 1)
        runstate.resume(self.conn)
        resumed = bees.start_process(self.env(), REL, 'P2', 'Bea', 'medium', 'task', [], [], [])
        self.assertTrue(resumed, bees.LAST_REFUSAL)
        self.assertEqual(resumed['kind'], 'subagent')
        self.assertEqual(scheduler.embedded_slots(self.conn), {'claude': 2})
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, 'testproj', 'all')))


# Check actual preparation of a plan-named Git branch and worktree.
class QueenWorktreeTests(unittest.TestCase):
    # Start a plan branch in its own worktree and reuse it.
    def test_prepare_named_worktree(self):
        import subprocess
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            # Run Git inside the temporary test repository.
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init', '-b', 'main')
            (repo / 'tracked.txt').write_text('start', encoding='utf-8')
            git('add', 'tracked.txt')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'initial')
            git('remote', 'add', 'origin', str(repo))
            git('fetch', 'origin', 'main')
            (repo / 'plan.md').write_text('+++\nstatus = "QUEUED"\nbranch = "plan-example"\nworktree = %s\n+++\n# Plan\n' % json.dumps(str(root / 'wt-plan-example')), encoding='utf-8')
            env = SimpleNamespace(workspace=repo, code_repo=repo, settings={'integrationBranch': 'main', 'worktreePrefix': 'wt'}, log=lambda text: None)
            worktree = bees.prepare_queen_worktree(env, 'plan.md')
            self.assertNotEqual(Path(worktree).resolve(), repo.resolve())
            self.assertEqual(subprocess.run(['git', '-C', worktree, 'branch', '--show-current'], check=True, capture_output=True, text=True).stdout.strip(), 'plan-example')
            self.assertEqual(bees.prepare_queen_worktree(env, 'plan.md'), worktree)
            bee_wt = root / 'bee-worktree'
            git('worktree', 'add', '-b', 'plan-example-p1', str(bee_wt), 'plan-example')
            self.assertTrue(bees.valid_bee_worktree(env, bee_wt, worktree))
            self.assertFalse(bees.valid_bee_worktree(env, Path(worktree), worktree))
            self.assertFalse(bees.valid_bee_worktree(env, repo, worktree))



from test_agent_runner import RunnerBase, claude_stream


# Verify the separate Bee runner does not load full project rules.
class BeeRunnerTests(RunnerBase):
    # A Bee starts the provider with settings sources disabled and an empty rule file.
    def test_bee_runner_loads_only_supplied_task_rules(self):
        self.prepare('Ada')
        result = self.run_runner('Ada', 'claude', claude_stream(), phase='bee')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.row('Ada')['status'], 'done')
        args = self.record()['argv']
        self.assertIn('--setting-sources', args)
        self.assertEqual((self.runs / 'Ada' / 'rules.md').read_text(encoding='utf-8'), '')   # no repository with instruction files here: nothing to load

    # Bee context exhaustion writes its handoff signal for the current package.
    def test_bee_context_limit_sets_handoff(self):
        self.prepare('Ada')
        result = self.run_runner('Ada', 'claude', claude_stream(ctx_big=True), phase='bee')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runs / 'Ada' / 'HANDOFF').exists())

# Verify that only repeated package failure signals count.
class AttemptSignalTests(unittest.TestCase):
    # Check repeated requirements and errors count only once per attempt.
    def test_signals_and_no_signal(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'a', ['one'], 'error 1')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 0)
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'b', ['one'], 'error 1')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 1)
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'c', ['two'], 'error 2')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 1)
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'd', ['three'], 'error 2')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 2)
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'd', ['three'], 'error 2')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 2)

    # Escalation and interruptions do not carry signals into another attempt.
    def test_class_reset_and_interruption(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'a', ['one'], 'error')
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'interrupted', 'b', ['one'], 'error')
        bees.record_attempt(conn, 'p', 'plan', 'P', 'heavy', 'failed', 'c', ['one'], 'error')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 0)
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'heavy'), 0)
        bees.record_attempt(conn, 'p', 'plan', 'P', 'heavy', 'failed', 'd', ['one'], '')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'heavy'), 1)

    # The Queen is instructed to check both concrete signals.
    def test_prompt_names_signals(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        self.assertIn('previous attempt', prompt)
        self.assertIn('same last error', prompt)
        self.assertIn('open requirements', prompt)
    # A replacement Queen retries a vanished Bee at the same class and provider.
    def test_prompt_retries_vanished_bee(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('after a queen restart', 'agent process vanished', 'same class; bzz agent start chooses its provider again', 'interruption is never a failure'):
            self.assertIn(text, prompt)
    # Missing signal data does not turn an unsolved outcome into a failure.
    def test_missing_signals_do_not_count(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.record_attempt(conn, 'p', 'plan', 'P', 'medium', 'failed', 'old')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P', 'medium'), 0)
        self.assertEqual(conn.execute('SELECT class,counted FROM bee_attempt_signals WHERE attempt=?', ('old',)).fetchone(), ('medium', 0))
