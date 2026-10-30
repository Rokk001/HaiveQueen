import unittest
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

    # Check image waits if codex busy.
    def test_image_waits_if_codex_busy(self):
        self.free['images'] = 0
        self.assertIsNone(bees.select_route(self.providers, self.free, 'cloud', 'medium', ['images'], [], False))

    # Check image uses codex.
    def test_image_uses_codex(self):
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
        for text in ('Offene Anforderungen:', '300000', '3', 'integration', 'subagent'):
            self.assertIn(text.lower(), prompt.lower())

    # Check package above queen class is bee even if alone.
    def test_package_above_queen_class_is_bee_even_if_alone(self):
        self.assertTrue(bees.requires_bee('medium', 'heavy', False, False))
        self.assertFalse(bees.requires_bee('medium', 'medium', False, False))
        self.assertTrue(bees.requires_bee('medium', 'medium', True, False))

    # Check codex without subagents works own class.
    def test_codex_without_subagents_works_own_class(self):
        self.assertFalse(bees.requires_bee('medium', 'medium', True, True))
        self.assertTrue(bees.requires_bee('medium', 'heavy', False, True))

    # Check attempts survive reopen and ignore interruption.
    def test_attempts_survive_reopen_and_ignore_interruption(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'attempts.sqlite')
            conn = sqlite3.connect(path)
            bees.ensure_attempts(conn)
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed')
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
        for word in ('dependenc', 'heavy', 'local', 'image', 'escalat', '3', 'handoff'):
            self.assertIn(word, prompt)

    # A repeated report of the same attempt does not increment its failure count.
    def test_repeated_attempt_report_is_counted_once(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'bee-1')
        bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed', 'bee-1')
        self.assertEqual(bees.failure_count(conn, 'p', 'plan', 'P1', 'medium'), 1)
        conn.close()

    # Check three failed attempts escalate and highest asks owner.
    def test_three_failed_attempts_escalate_and_highest_asks_owner(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        bees.ensure_attempts(conn)
        for _ in range(3):
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'medium', 'failed')
        self.assertEqual(bees.next_attempt(conn, 'p', 'plan', 'P1', 'medium', self.providers, [], 3), 'heavy')
        for _ in range(3):
            bees.record_attempt(conn, 'p', 'plan', 'P1', 'heavy', 'failed')
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
        for text in ('no new', 'running bees', 'handoff', 'finish', '300000'):
            self.assertIn(text, prompt)

    # Check queen prompt requires completeness evidence before integrating.
    def test_queen_prompt_requires_completeness_evidence_before_integrating(self):
        prompt = bees.queen_instructions('medium', 3, 3, 300000).lower()
        for text in ('requirement', 'evidence', 'offene anforderungen: 0', 'integrat'):
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
    def test_no_codex_for_regular_work_before_escalation(self):
        self.free['cloud'] = 0
        self.free['local'] = 0
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
        for text in ('escalat', 'dependencies', 'bees', 'offene anforderungen: 0'):
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

    # Check process bee refuses start when paused.
    def test_process_bee_refuses_start_when_paused(self):
        self.add_local_provider()
        from haivequeen import hives, runstate
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        runstate.pause(self.conn)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', [], [], [], parallel=True)
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

    # Check highest class failures open one owner question.
    def test_highest_class_failures_open_one_owner_question(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        for _ in range(3):
            bees.report_attempt(self.env(), REL, 'P1', 'heavy', 'failed')
        bees.report_attempt(self.env(), REL, 'P1', 'heavy', 'failed')
        questions = runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1')
        self.assertEqual(len(questions), 1)




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
        git('remote', 'add', 'origin', str(self.repo))
        git('fetch', 'origin', 'main')
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        self.assertNotEqual(Path(queen['cwd']).resolve(), self.repo.resolve())
        branch = subprocess.run(['git', '-C', queen['cwd'], 'branch', '--show-current'], check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, 'plan-example')

    # Missing branch information never starts a Queen in the main checkout.
    def test_unknown_worktree_waits_for_owner_answer(self):
        from haivequeen import hives, loop, db
        from bzztest import REL
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        loop.run_round(self.conn, self.machine, self.envs(), free_ram=lambda: 16.0, settle=0)
        self.assertFalse(any(a['phase'] == 'queen' for a in runtime.list_agents(self.conn, 'testproj', 'all')))
        questions = runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='worktree')
        self.assertEqual(len(questions), 1)
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')
        db.answer_question(self.conn, questions[0]['id'], 'feature/test\n' + str(self.wt), 'owner')
        self.assertEqual(bees.plan_worktree(self.env(), REL), ('feature/test', self.wt))

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

    # The scheduling round keeps a Hive waiting while Codex is occupied, then resumes it.
    def test_round_resumes_codex_wait_only_after_slot_is_free(self):
        from haivequeen import hives
        from bzztest import REL
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
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

    # A native subagent consumes no additional provider process slot and can start while paused.
    def test_native_bee_needs_no_slot_and_continues_during_pause(self):
        from haivequeen import hives, runstate
        from bzztest import REL
        self.machine['providers'][0]['slots'] = 1
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        runstate.pause(self.conn)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], parallel=True)
        self.assertEqual(result['kind'], 'subagent')
        self.assertFalse(any(a['phase'] == 'bee' for a in runtime.list_agents(self.conn, 'testproj', 'all')))

    # A Codex Queen without subagents works her own class even with no extra slot.
    def test_codex_queen_fallback_does_not_need_an_extra_slot(self):
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][1]['slots'] = 1
        self.add_local_provider()
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('queen', phase='queen', status='running', provider='codex', type='codex', pid=42)
        result = bees.start_process(self.env(), REL, 'P1', 'Ada', 'medium', 'task', ['one'], [], [], no_subagents=True)
        self.assertEqual(result['kind'], 'queen')
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
        for i in range(3):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'queen-%s' % i)
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
        for i in range(3):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'attempt-%s' % i)
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            result = bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt))
        self.assertTrue(result)
        row = runtime.get_agent(self.conn, 'testproj', 'Ada')
        self.assertEqual((row['provider'], row['class']), ('codex', 'heavy'))

    # A limited Bee provider remains assigned even when another provider is free.
    def test_interrupted_bee_does_not_switch_provider(self):
        from unittest import mock
        from haivequeen import hives, usage
        from bzztest import REL
        self.add_local_provider()
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'heavy'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        bees.ensure_attempts(self.conn)
        self.conn.execute('INSERT INTO bee_assignments VALUES (?,?,?,?,?)', ('testproj', REL, 'P1', 'heavy', 'local'))
        env = self.env()
        bee_wt = self.base / 'bee-wt'
        bee_wt.mkdir()
        limited = lambda conn, provider: [{'used_pct': 100}] if provider['name'] == 'local' else []
        with mock.patch.object(usage, 'limit_windows', side_effect=limited), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            self.assertFalse(bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt)))
        with mock.patch.object(usage, 'limit_windows', return_value=[]), mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True):
            self.assertTrue(bees.start_process(env, REL, 'P1', 'Ada', 'heavy', 'task', ['one'], [], [], cwd=str(bee_wt)))
        self.assertEqual(runtime.get_agent(self.conn, 'testproj', 'Ada')['provider'], 'local')

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

    # An image package asks the owner after the highest Codex class fails.
    def test_image_escalation_stops_at_highest_codex_class(self):
        from haivequeen import hives
        from bzztest import REL
        self.machine['providers'][1]['classes'].pop('heavy')
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'Image', 'class': 'medium', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        env = self.env()
        for i in range(3):
            bees.report_attempt(env, REL, 'P1', 'medium', 'failed', 'attempt-%s' % i)
        self.assertEqual(len(runtime.list_questions(self.conn, 'testproj', status='open', plan=REL, item='P1')), 1)
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

    # The configured integration prompt receives this Hive's plan path.
    def test_queen_integration_prompt_resolves_own_plan(self):
        from bzztest import REL
        self.machine['prompts'] = {'integrator': 'Integrate only {plan} in {project} on {integrationBranch}.'}
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen')
        prompt = (self.env().run_dir(queen['name']) / 'prompt.md').read_text(encoding='utf-8-sig')
        self.assertIn('Integrate only %s in testproj on main.' % REL, prompt)
        self.assertNotIn('{plan}', prompt)

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

    # Migration pauses only the four plans explicitly listed by its source.
    def test_migration_does_not_pause_unlisted_in_progress_plan(self):
        from haivequeen import runstate
        hq = self.add_project('hq')
        targets = ('OLD-ONE', 'OLD-TWO', 'OLD-THREE', 'OLD-FOUR')
        for tag in (*targets, 'UNRELATED'):
            rel = 'docs/plans/%s/%s-PLAN.md' % (tag, tag)
            self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel=rel)
            self.agent('old-' + tag, project='hq', plan_path=rel, phase='work', status='done')
        source = 'docs/plans/MIGRATE/MIGRATE-PLAN.md'
        self.plan('DRAFT', [], project='hq', rel=source)
        with (hq / source).open('a', encoding='utf-8') as plan:
            plan.write('\n- D25 Migration: HaiveQueen plans in work (OLD-ONE, OLD-TWO, OLD-THREE, OLD-FOUR) stay paused.\n')
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        rows = {row['path']: row['status'] for row in runtime.list_plans(self.conn, 'hq')}
        for tag in targets:
            self.assertEqual(rows['docs/plans/%s/%s-PLAN.md' % (tag, tag)], 'PAUSED')
        self.assertEqual(rows['docs/plans/UNRELATED/UNRELATED-PLAN.md'], 'IN_PROGRESS')

    # Explicit migration targets are paused even when old agent rows are absent.
    def test_migration_uses_named_targets_without_agent_history(self):
        from haivequeen import runstate
        hq = self.add_project('hq')
        for tag in ('OLD-ONE', 'OLD-TWO', 'OLD-THREE', 'OLD-FOUR'):
            self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel='docs/plans/%s/%s-PLAN.md' % (tag, tag))
        source = 'docs/plans/MIGRATE/MIGRATE-PLAN.md'
        self.plan('DRAFT', [], project='hq', rel=source)
        with (hq / source).open('a', encoding='utf-8') as plan:
            plan.write('\n- D25 Migration: HaiveQueen plans in work (OLD-ONE, OLD-TWO, OLD-THREE, OLD-FOUR) stay paused.\n')
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        targets = [row for row in runtime.list_plans(self.conn, 'hq') if '/OLD-' in row['path']]
        self.assertEqual(len(targets), 4)
        self.assertTrue(all(row['status'] == 'PAUSED' for row in targets))

    # An unavailable registered project must still migrate when it becomes available.
    def test_unavailable_hq_is_migrated_when_workspace_returns(self):
        from haivequeen import runstate
        hq = self.add_project('hq')
        targets = ('OLD-ONE', 'OLD-TWO', 'OLD-THREE', 'OLD-FOUR')
        for tag in targets:
            rel = 'docs/plans/%s/%s-PLAN.md' % (tag, tag)
            self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel=rel)
            self.agent('old-' + tag, project='hq', plan_path=rel, phase='work', status='done')
        source = 'docs/plans/MIGRATE/MIGRATE-PLAN.md'
        self.plan('DRAFT', [], project='hq', rel=source)
        with (hq / source).open('a', encoding='utf-8') as plan:
            plan.write('\n- D25 Migration: HaiveQueen plans in work (OLD-ONE, OLD-TWO, OLD-THREE, OLD-FOUR) stay paused.\n')
        self.conn.execute('UPDATE projects SET workspace=? WHERE name=?', (str(self.base / 'unavailable'), 'hq'))
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'armed')
        self.conn.execute('UPDATE projects SET workspace=? WHERE name=?', (str(hq), 'hq'))
        self.round()
        rows = {row['path']: row['status'] for row in runtime.list_plans(self.conn, 'hq')}
        for tag in targets:
            self.assertEqual(rows['docs/plans/%s/%s-PLAN.md' % (tag, tag)], 'PAUSED')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'done')

    # Available projects migrate only once while another registered workspace is unavailable.
    def test_available_project_migration_is_not_repeated_while_hq_is_missing(self):
        from haivequeen import runstate
        from bzztest import REL
        self.add_project('hq')
        self.conn.execute('UPDATE projects SET workspace=? WHERE name=?', (str(self.base / 'unavailable'), 'hq'))
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        runtime.set_package(self.conn, 'testproj', REL, 'P1', locked=True, reason='old lock')
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        self.assertFalse(runtime.list_packages(self.conn, 'testproj', REL)[0]['locked'])
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'armed')
        runtime.set_package(self.conn, 'testproj', REL, 'P1', locked=True, reason='new lock')
        self.round()
        self.assertTrue(runtime.list_packages(self.conn, 'testproj', REL)[0]['locked'])

    # A missing non-tool project also has its old locks lifted on its return.
    def test_unavailable_other_project_receives_deferred_lock_migration(self):
        from haivequeen import runstate
        other = self.add_project('otherproject')
        rel = 'docs/plans/OLD/OLD-PLAN.md'
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='otherproject', rel=rel)
        runtime.set_package(self.conn, 'otherproject', rel, 'P1', locked=True, reason='old lock')
        self.conn.execute('UPDATE projects SET workspace=? WHERE name=?', (str(self.base / 'unavailable'), 'otherproject'))
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'armed')
        self.assertTrue(runtime.list_packages(self.conn, 'otherproject', rel)[0]['locked'])
        self.conn.execute('UPDATE projects SET workspace=? WHERE name=?', (str(other), 'otherproject'))
        self.round()
        self.assertFalse(runtime.list_packages(self.conn, 'otherproject', rel)[0]['locked'])
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'done')

    # A missing migration list blocks only that project and preserves independent work.
    def test_unclear_hq_migration_does_not_stop_other_project(self):
        from haivequeen import runstate
        self.add_project('hq')
        rel = 'docs/plans/UNRELATED/UNRELATED-PLAN.md'
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel=rel)
        self.agent('old-hq', project='hq', plan_path=rel, phase='work', status='done')
        runtime.set_package(self.conn, 'hq', rel, 'P1', locked=True, reason='old lock')
        self.plan('READY', [{'id': 'P1', 'title': 'Independent'}])
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'armed')
        self.assertTrue(runtime.list_packages(self.conn, 'hq', rel)[0]['locked'])
        self.assertFalse(any(a['phase'] == 'queen' for a in runtime.list_agents(self.conn, 'hq', 'all')))
        self.assertTrue(any(a['phase'] == 'queen' and a['status'] == 'running' for a in runtime.list_agents(self.conn, 'testproj', 'all')))
        for tag in ('OLD-ONE', 'OLD-TWO', 'OLD-THREE', 'OLD-FOUR'):
            self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel='docs/plans/%s/%s-PLAN.md' % (tag, tag))
        source = 'docs/plans/MIGRATE/MIGRATE-PLAN.md'
        self.plan('DRAFT', [], project='hq', rel=source)
        with (self.base / 'repo-hq' / source).open('a', encoding='utf-8') as plan:
            plan.write('\n- D25 Migration: HaiveQueen plans in work (OLD-ONE, OLD-TWO, OLD-THREE, OLD-FOUR) stay paused.\n')
        self.round()
        rows = {row['path']: row['status'] for row in runtime.list_plans(self.conn, 'hq')}
        for tag in ('OLD-ONE', 'OLD-TWO', 'OLD-THREE', 'OLD-FOUR'):
            self.assertEqual(rows['docs/plans/%s/%s-PLAN.md' % (tag, tag)], 'PAUSED')
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'done')

    # Migration without a unique source leaves plans, locks and marker untouched.
    def test_missing_migration_list_does_not_change_state(self):
        from haivequeen import runstate
        self.add_project('hq')
        rel = 'docs/plans/UNRELATED/UNRELATED-PLAN.md'
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}], project='hq', rel=rel)
        self.agent('old', project='hq', plan_path=rel, phase='work', status='done')
        runtime.set_package(self.conn, 'hq', rel, 'P1', locked=True, reason='old lock')
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        result = self.round()
        self.assertIsNone(result['skipped'])
        self.assertEqual(result['projects']['hq']['report'], ['migration target list missing or ambiguous'])
        self.assertEqual(runtime.list_plans(self.conn, 'hq')[0]['status'], 'IN_PROGRESS')
        self.assertTrue(runtime.list_packages(self.conn, 'hq', rel)[0]['locked'])
        self.assertEqual(runtime.kv_get(self.conn, '*', 'hives-migration'), 'armed')

    # A Bee note identifies its Queen or its own process in the event history.
    def test_bee_note_keeps_agent_identity(self):
        from unittest import mock
        from haivequeen import cli
        args = cli.build_parser().parse_args(['note', '--project', 'testproj', '--text', 'Ada P1: step 1/2 work'])
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}):
            self.assertEqual(cli.cmd_note(args, self.conn), 0)
        row = self.conn.execute("SELECT agent,text FROM events WHERE kind='note' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((row['agent'], row['text']), ('queen', 'Ada P1: step 1/2 work'))



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
            (repo / 'plan.md').write_text('# Plan\n- Feature branch: plan-example, created from origin/main.\n', encoding='utf-8')
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
        self.assertEqual((self.runs / 'Ada' / 'rules.md').read_text(encoding='utf-8'), '')

    # Bee context exhaustion writes its handoff signal for the current package.
    def test_bee_context_limit_sets_handoff(self):
        self.prepare('Ada')
        result = self.run_runner('Ada', 'claude', claude_stream(ctx_big=True), phase='bee')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.runs / 'Ada' / 'HANDOFF').exists())
