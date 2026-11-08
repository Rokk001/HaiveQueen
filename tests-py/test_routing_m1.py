"""Tests for provider choice (Bee routing) with the quota reserve.

A Bee of a Queen whose provider runs Bees as embedded subagents takes a free local provider first, else stays on the Queen's provider; only when both have no
free slot it goes to another provider as for any other Bee. Every other Bee (bee mode process) is placed by normal provider choice like a Queen: class, capabilities, free
slot, best quota reserve from both usage windows; a local provider without limit comes first. A stored package assignment keeps only the class; the provider
is chosen again at every start of a process Bee. An escalated package takes, among the providers it may use, the one with the best reserve."""
import json
import os
import unittest
from unittest import mock

from bzztest import REL, RoundCase, provider, runtime
from haivequeen import bees, hives
from haivequeen import scheduler as sch


class SelectProviderReserveTests(RoundCase):
    """The normal provider choice ranks by the reserve from both usage windows."""

    def limits(self, **entries):
        path = self.base / 'limits.json'
        path.write_text(json.dumps(entries), encoding='utf-8')
        os.environ['BZZ_PROVIDER_LIMITS_FILE'] = str(path)

    def two(self):
        # "Coding" is added by load_projects for every usable provider; direct calls need it in the dict already
        return [provider('claude', 2, limit='claude', caps=('Coding',)), provider('codex', 2, limit='codex', caps=('Coding',))]

    def local(self, name='lmstudio', slots=2, limit=''):
        p = provider(name, slots, limit=limit, caps=('Coding',))
        p['endpoint'] = {'baseUrl': 'http://localhost:1'}
        return p

    # A used-up 5-hour window blocks the provider although its weekly window still has free quota.
    def test_used_up_five_hour_window_blocks_a_normal_package(self):
        limits = {'claude': {'usedPercent': 20, 'resetHours': 100, 'windows': [{'usedPercent': 100, 'resetHours': 2}]},
                  'codex': {'usedPercent': 50, 'resetHours': 100}}
        pick = sch.select_provider(self.two(), 'medium', [], {'claude': 1, 'codex': 1}, limits=limits)
        self.assertEqual(pick['name'], 'codex')

    # A passed reset counts as reset (quota 1.0), not as a nearly empty window with a huge quota.
    def test_passed_reset_counts_as_reset_not_huge(self):
        limits = {'claude': {'usedPercent': 80, 'resetHours': -5}, 'codex': {'usedPercent': 30, 'resetHours': 84}}
        pick = sch.select_provider(self.two(), 'medium', [], {'claude': 1, 'codex': 1}, limits=limits)
        self.assertEqual(pick['name'], 'codex')

    # A provider that reports only a 5-hour window counts its weekly quota as 1.0.
    def test_only_a_five_hour_window_counts_the_week_as_one(self):
        limits = {'alpha': {'windows': [{'usedPercent': 40, 'resetHours': 4}]}, 'beta': {'usedPercent': 80, 'resetHours': 40}}
        providers = [provider('alpha', 2, limit='claude', caps=('Coding',)), provider('beta', 2, limit='codex', caps=('Coding',))]
        pick = sch.select_provider(providers, 'medium', [], {'alpha': 1, 'beta': 1}, limits=limits)
        self.assertEqual(pick['name'], 'beta')

    # Reserve 0 blocks a capability package too (no new work until the window resets).
    def test_capability_package_blocked_at_reserve_zero(self):
        providers = [provider('claude', 2, limit='claude', caps=('Coding', 'Image generation')), provider('gemini', 2, limit='codex')]
        limits = {'claude': {'usedPercent': 100, 'resetHours': 2}, 'gemini': {'usedPercent': 30, 'resetHours': 84}}
        self.assertIsNone(sch.select_provider(providers, 'medium', ['Image generation'], {'claude': 1, 'gemini': 1}, limits=limits))

    # Equal reserves go to the local provider first.
    def test_equal_reserves_go_local_first(self):
        entry = {'usedPercent': 50, 'resetHours': 84}
        providers = [provider('claude', 2, limit='claude', caps=('Coding',)), self.local(limit='codex')]
        pick = sch.select_provider(providers, 'medium', [], {'claude': 1, 'lmstudio': 1}, limits={'claude': entry, 'lmstudio': dict(entry)})
        self.assertEqual(pick['name'], 'lmstudio')

    # A provider without limit (+99 %) ranks before a limited one even with the higher raw reserve. [pin: holds today]
    def test_unlimited_ranks_before_limited(self):
        providers = [provider('claude', 2, limit='claude', caps=('Coding',)), self.local()]
        pick = sch.select_provider(providers, 'medium', [], {'claude': 1, 'lmstudio': 1}, limits={'claude': {'usedPercent': 90, 'resetHours': 84}})
        self.assertEqual(pick['name'], 'lmstudio')

    # Preferred before fallback applies before the reserve. [pin: holds today]
    def test_fallback_stays_behind_preferred(self):
        providers = [provider('claude', 2, use='fallback', limit='claude', caps=('Coding',)), provider('codex', 2, limit='codex', caps=('Coding',))]
        limits = {'claude': {'usedPercent': 10, 'resetHours': 84}, 'codex': {'usedPercent': 90, 'resetHours': 84}}
        pick = sch.select_provider(providers, 'medium', [], {'claude': 1, 'codex': 1}, limits=limits)
        self.assertEqual(pick['name'], 'codex')


class SelectRouteReserveTests(RoundCase):
    """The route of an escalated package and a process Bee is chosen by the reserve."""

    def limits(self, **entries):
        path = self.base / 'limits.json'
        path.write_text(json.dumps(entries), encoding='utf-8')
        os.environ['BZZ_PROVIDER_LIMITS_FILE'] = str(path)

    # An escalated package takes, among the providers it may use, the one with the best reserve, not the first free one.
    def test_escalated_package_takes_the_best_reserve_not_list_order(self):
        self.limits(claude={'usedPercent': 80, 'resetHours': 84}, codex={'usedPercent': 30, 'resetHours': 84})
        providers = [provider('claude', 2, limit='claude'), provider('codex', 2, limit='codex')]
        route = bees.select_route(providers, {'claude': 1, 'codex': 1}, 'claude', 'medium', None, (), True)
        self.assertEqual(route[0], 'codex')

    # A process Bee of a Queen is placed by normal provider choice like a Queen, not on the Queen's provider.
    def test_process_bee_of_a_queen_is_placed_by_normal_provider_choice(self):
        self.limits(claude={'usedPercent': 30, 'resetHours': 84}, codex={'usedPercent': 60, 'resetHours': 84})
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('Ada', phase='queen', status='running', provider='codex', pid=42)
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), \
             mock.patch('haivequeen.loop._do_start', return_value=True), \
             mock.patch('haivequeen.loop.ram_gb', return_value=16.0):
            result = bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt))
        self.assertEqual(result['provider'], 'claude')

    # A stored package assignment keeps only the class; the provider is chosen again at every start of a process Bee.
    def test_stored_assignment_does_not_keep_a_process_bee_on_its_old_provider(self):
        self.limits(claude={'usedPercent': 30, 'resetHours': 84}, codex={'usedPercent': 60, 'resetHours': 84})
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'codex', '123456')
        self.agent('Ada', phase='queen', status='running', provider='codex', pid=42)
        bees.ensure_attempts(self.conn)  # creates bee_assignments before the stored assignment below
        self.conn.execute("INSERT INTO bee_assignments(project,plan_path,package,class,provider) VALUES(?,?,?,?,?)", ('testproj', REL, 'I1', 'medium', 'codex'))
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), \
             mock.patch('haivequeen.loop._do_start', return_value=True), \
             mock.patch('haivequeen.loop.ram_gb', return_value=16.0):
            result = bees.start_process(self.env(), REL, 'I1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt))
        self.assertEqual(result['provider'], 'claude')


class SubagentBeeTests(RoundCase):
    """A Bee of a Queen whose provider runs Bees as embedded subagents: free local first, else the Queen's provider, else another provider."""

    def limits(self, **entries):
        path = self.base / 'limits.json'
        path.write_text(json.dumps(entries), encoding='utf-8')
        os.environ['BZZ_PROVIDER_LIMITS_FILE'] = str(path)

    def setup_queen(self, lm_slots=2, claude_slots=2):
        # a Claude Code Queen runs her Bees as embedded subagents (the default beeMode of the claude protocol)
        self.machine['providers'][0]['beeMode'] = 'subagent'
        self.machine['providers'][0]['slots'] = claude_slots
        lm = provider('lmstudio', lm_slots)
        lm['endpoint'] = {'baseUrl': 'http://localhost:1'}
        self.machine['providers'].append(lm)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)

    def start_bee(self):
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), \
             mock.patch('haivequeen.loop._do_start', return_value=True), \
             mock.patch('haivequeen.loop.ram_gb', return_value=16.0):
            return bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt))

    # A free local provider comes first. [pin: holds today]
    def test_subagent_bee_takes_a_free_local_provider_first(self):
        self.setup_queen()
        result = self.start_bee()
        self.assertEqual((result['kind'], result['provider']), ('process', 'lmstudio'))

    # With the local full, the Bee stays on the Queen's provider while it has a free slot. [pin: holds today]
    def test_subagent_bee_stays_on_the_queen_provider_while_it_has_a_free_slot(self):
        self.setup_queen(lm_slots=0)
        result = self.start_bee()
        self.assertEqual((result['kind'], result['provider']), ('subagent', 'claude'))

    # Only when both have no free slot the Bee goes to another provider as for any other Bee. [pin: holds today]
    def test_subagent_bee_moves_as_in_c2_when_both_are_full(self):
        self.setup_queen(lm_slots=0, claude_slots=1)  # Ada takes the only claude slot
        result = self.start_bee()
        self.assertEqual((result['kind'], result['provider']), ('process', 'codex'))

    # Reserve 0 of the Queen's provider counts like no free slot. [pin: holds today]
    def test_subagent_bee_moves_away_from_the_queen_provider_at_reserve_zero(self):
        self.setup_queen(lm_slots=0)
        self.limits(claude={'usedPercent': 100, 'resetHours': 2}, codex={'usedPercent': 50, 'resetHours': 84})
        result = self.start_bee()
        self.assertEqual((result['kind'], result['provider']), ('process', 'codex'))


class QueenReserveTests(RoundCase):
    """The next Queen of a Hive is chosen by the reserve."""

    def limits(self, **entries):
        path = self.base / 'limits.json'
        path.write_text(json.dumps(entries), encoding='utf-8')
        os.environ['BZZ_PROVIDER_LIMITS_FILE'] = str(path)

    def waiting_hive(self, provider_name='claude'):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, provider_name, '123456')
        self.agent('Ada', phase='queen', status='done', provider=provider_name, pid=None, ended_at='2026-01-01T00:00:00')
        self.conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", ('testproj', REL))

    def queens(self):
        return [a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen' and a['status'] in ('running', 'starting')]

    # A Hive whose provider reaches reserve 0 gets its next Queen on another provider. [pin: holds today]
    def test_hive_whose_provider_reaches_reserve_zero_gets_next_queen_elsewhere(self):
        self.limits(claude={'usedPercent': 100, 'resetHours': 2}, codex={'usedPercent': 50, 'resetHours': 84})
        self.waiting_hive()
        self.round()
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])

    # The reserve from both windows decides between providers that are not used up.
    def test_queen_choice_uses_the_reserve_from_both_windows(self):
        self.limits(claude={'usedPercent': 20, 'resetHours': 100, 'windows': [{'usedPercent': 90, 'resetHours': 4}]}, codex={'usedPercent': 50, 'resetHours': 100})
        self.waiting_hive()
        self.round()
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])


if __name__ == "__main__":
    unittest.main()
