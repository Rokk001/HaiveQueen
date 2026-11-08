"""Scheduling fix: every Bee holds a slot of its provider , a Bee goes to another provider when the Queen's is full,
a new Queen of an existing Hive chooses her provider by the normal provider choice, and the texts describe it."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from bzztest import REL, RoundCase, provider, runtime
from haivequeen import bees, hives, i18n, interrupt, overview, roles
from haivequeen import scheduler as sch

ROOT = Path(__file__).resolve().parent.parent


class SlotAndProviderChoiceTests(RoundCase):
    """Database-backed rounds and Bee starts."""

    def limits(self, **used):
        """Make the quota reserve of the providers known: name -> used percent, all with the same reset."""
        path = self.base / 'limits.json'
        path.write_text(json.dumps({name: {'usedPercent': pct, 'resetHours': 100} for name, pct in used.items()}), encoding='utf-8')
        os.environ['BZZ_PROVIDER_LIMITS_FILE'] = str(path)

    def queens(self, status=('running', 'starting')):
        """The Queen rows of the test project in one of these states."""
        return [a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['phase'] == 'queen' and a['status'] in status]

    # A claude Queen with two subagent Bees holds three claude slots in the round, so a new Hive's Queen goes elsewhere.
    def test_subagent_bees_take_slots_in_the_round(self):
        self.machine['providers'][0]['slots'] = 3
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        for n in (1, 2):
            bees.record_embedded_start(self.conn, 'testproj', REL, 'P%d' % n, 'Bee%d' % n, 'medium', 'claude', queen='Ada')
        other = 'docs/plans/other/OTHER-PLAN.md'
        self.plan('READY', [{'id': 'Q1', 'title': 'Other'}], rel=other)
        self.round(alive=lambda pid: True)
        started = [a for a in self.queens() if a['plan_path'] == other]
        self.assertEqual([a['provider'] for a in started], ['codex'])
        env = self.env()
        rows = [{**a, 'status': 'running'} for a in runtime.list_agents(self.conn, None, 'active')]
        self.assertEqual(sch.provider_free(env.providers, rows, self.conn)['claude'], 0)
        line = next(l for l in overview.provider_lines(self.conn, self.machine, rows) if l['name'] == 'claude')
        self.assertEqual(line['used_slots'], 3)

    # A Queen needs one free slot of her own to start; the owner question provider-slots is not asked.
    def test_queen_starts_with_one_free_slot(self):
        self.machine['providers'] = [provider('claude', 1)]
        self.machine['providers'][0]['beeMode'] = 'process'
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        self.assertEqual([a['provider'] for a in self.queens()], ['claude'])
        self.assertFalse(runtime.list_questions(self.conn, 'testproj', item='provider-slots'))

    # A waiting Bee of a running Queen gets the last free slot before a new Queen.
    def test_waiting_bee_gets_last_slot_before_new_queen(self):
        self.machine['providers'][0]['slots'] = 2
        self.machine['providers'][1]['slots'] = 0
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        other = 'docs/plans/other/OTHER-PLAN.md'
        self.plan('READY', [{'id': 'Q1', 'title': 'Other'}], rel=other)
        self.round(alive=lambda pid: True)
        self.assertFalse([a for a in self.queens() if a['plan_path'] == other])
        self.assertIn('P1', (self.env().run_dir('Ada') / bees.NOTICE_FILE).read_text(encoding='utf-8'))
        self.assertIn('no provider with a free slot', runtime.kv_get(self.conn, 'testproj', 'hive-blocker:' + other))

    # With no free slot on any provider the Bee waits.
    def test_bee_waits_when_no_provider_has_a_free_slot(self):
        self.machine['providers'][0]['slots'] = 1
        self.machine['providers'][1]['slots'] = 1
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        self.agent('Other', phase='queen', status='running', provider='codex', pid=43, plan_path='docs/plans/x/X-PLAN.md')
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), mock.patch('haivequeen.loop._do_start', return_value=True) as start:
            self.assertFalse(bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt)))
        start.assert_not_called()
        self.assertIn('no provider with a free slot', bees.LAST_REFUSAL)
        self.assertFalse(bees.unconfirmed_embedded(self.conn, 'testproj', REL))

    # A subagent Bee on the Queen's own provider needs a free slot of it.
    def test_subagent_bee_takes_a_free_slot_of_the_queen_provider(self):
        self.machine['providers'][0]['slots'] = 2
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First', 'class': 'medium'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        result = bees.start_process(self.env(), REL, 'P1', 'Bea', 'medium', 'task', ['one'], [], [])
        self.assertEqual((result['kind'], result['provider']), ('subagent', 'claude'))
        rows = [{**a, 'status': 'running'} for a in runtime.list_agents(self.conn, None, 'active')]
        self.assertEqual(sch.provider_free(self.env().providers, rows, self.conn)['claude'], 0)

    # A new Queen of an existing Hive goes to codex when codex has the higher reserve and a free slot.
    def test_new_queen_of_existing_hive_chooses_by_reserve(self):
        self.limits(claude=69, codex=56)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='done', provider='claude', pid=None, ended_at='2026-01-01T00:00:00')
        hives.get(self.conn, 'testproj', REL)
        self.conn.execute("UPDATE hives SET state='waiting' WHERE project=? AND plan_path=?", ('testproj', REL))
        self.round()
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['provider'], 'codex')

    # A running Queen is never moved or ended, even when another provider has the higher reserve.
    def test_running_queen_stays_on_her_provider(self):
        self.limits(claude=90, codex=10)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running' WHERE project=? AND plan_path=?", ('testproj', REL))
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        self.round(alive=lambda pid: True)
        self.assertEqual([(a['name'], a['provider']) for a in self.queens()], [('Ada', 'claude')])
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['provider'], 'claude')

    # A Queen restarted after a vanished run keeps her class and chooses her provider by reserve.
    def test_vanished_queen_keeps_class_and_chooses_by_reserve(self):
        self.machine['retryWaitMinutes'] = [0]
        self.limits(claude=80, codex=10)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running' WHERE project=? AND plan_path=?", ('testproj', REL))
        self.agent('Ada', phase='queen', status='failed', provider='claude', pid=None)
        runtime.upsert_agent(self.conn, 'testproj', 'Ada', {'class': 'heavy', 'last_line': 'process gone after orchestrator restart', 'ended_at': '2026-01-01T00:00:00'})
        self.assertEqual(interrupt.kind_of(runtime.get_agent(self.conn, 'testproj', 'Ada')), interrupt.VANISHED)
        self.round()
        new = [a for a in self.queens() if a['name'] != 'Ada']
        self.assertEqual([(a['provider'], a['class']) for a in new], [('codex', 'heavy')])

    # A Hive that waited for a provider does not bind the next Queen to that provider.
    def test_provider_wait_does_not_bind_next_queen(self):
        self.limits(claude=80, codex=10)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='done', provider='claude', pid=None, ended_at='2026-01-01T00:00:00')
        bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'provider_wait')
        self.round()
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])

    # A Hive waiting for a full provider wakes when another provider can take its waiting package and seat the next Queen.
    def test_provider_wait_wakes_on_another_free_provider(self):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='done', provider='claude', pid=None, ended_at='2026-01-01T00:00:00')
        self.agent('bee-a', phase='bee', provider='claude', status='running', pid=41)
        self.agent('bee-b', phase='bee', provider='claude', status='running', pid=42)
        bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'waiting', 'Ada', 'provider')
        bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
        self.round(alive=lambda pid: pid in (41, 42))
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])
        self.assertFalse(self.conn.execute('SELECT 1 FROM bee_provider_wait WHERE project=? AND plan_path=?', ('testproj', REL)).fetchone())

    # The next Queen may start with one slot; her Bee takes its slot later.
    def test_provider_wait_wakes_with_one_slot_elsewhere(self):
        self.machine['providers'][1]['slots'] = 1
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='done', provider='claude', pid=None, ended_at='2026-01-01T00:00:00')
        self.agent('bee-a', phase='bee', provider='claude', status='running', pid=41)
        self.agent('bee-b', phase='bee', provider='claude', status='running', pid=42)
        bees.wait_for_provider(self.conn, 'testproj', REL, 'claude')
        self.round(alive=lambda pid: pid in (41, 42))
        self.assertEqual([a['provider'] for a in self.queens()], ['codex'])

    # A package that needs a capability only the awaited provider has keeps the Hive waiting although another provider is free.
    def test_provider_wait_stays_for_a_capability_only_it_has(self):
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='done', provider='claude', pid=None, ended_at='2026-01-01T00:00:00')
        self.agent('bee-a', phase='bee', provider='codex', type='codex', status='running', pid=41)
        self.agent('bee-b', phase='bee', provider='codex', type='codex', status='running', pid=42)
        bees.wait_for_provider(self.conn, 'testproj', REL, 'codex')
        self.round(alive=lambda pid: pid in (41, 42))
        self.assertFalse(self.queens())
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'codex_wait')

    def third_images_provider(self):
        """Add a third provider that offers images like codex, after codex in the list."""
        self.machine['providers'].append(provider('gemini', 2, caps=('images',), limit='codex', proto='codex'))

    # The Queen's provider is full and two providers offer the needed capability: the Bee goes to the one with the higher reserve, not the first free one.
    def test_bee_with_capability_goes_to_higher_reserve_provider(self):
        self.third_images_provider()
        self.machine['providers'][0]['slots'] = 1
        self.limits(claude=0, codex=90, gemini=10)
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'class': 'medium', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)
        wt = self.base / 'bee-wt'
        wt.mkdir()
        with mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), mock.patch('haivequeen.loop._do_start', return_value=True):
            bees.start_process(self.env(), REL, 'I1', 'Bea', 'medium', 'task', ['one'], [], [], cwd=str(wt))
        row = self.conn.execute('SELECT provider FROM bee_assignments WHERE project=? AND plan_path=? AND package=?', ('testproj', REL, 'I1')).fetchone()
        self.assertEqual(row[0] if row else bees.LAST_REFUSAL, 'gemini')

    # With the awaited provider full, the provider named for a waiting capability package is the one with the higher reserve.
    def test_ready_check_names_higher_reserve_capable_provider(self):
        self.third_images_provider()
        self.limits(claude=0, codex=90, gemini=10)
        self.plan('IN_PROGRESS', [{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        other = bees.other_provider_for_waiting(self.env(), REL, 'claude', {'claude': 0, 'codex': 2, 'gemini': 2})
        self.assertEqual(other, 'gemini')

    def other_ready_plan(self):
        """A second plan that wants a new Queen in the round; returns its path."""
        other = 'docs/plans/other/OTHER-PLAN.md'
        self.plan('READY', [{'id': 'Q1', 'title': 'Other'}], rel=other)
        return other

    def running_queen(self, packages):
        """The test plan in progress with `packages` and its running claude Queen Ada."""
        self.plan('IN_PROGRESS', packages)
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('Ada', phase='queen', status='running', provider='claude', pid=42)

    # A package whose Bee waits for a provider keeps its slot reserved before a new Queen, and the Queen is told to start it again.
    def test_bee_waiting_for_provider_keeps_its_slot_before_new_queen(self):
        self.machine['providers'][0]['slots'] = 2
        self.machine['providers'][1]['slots'] = 0
        self.running_queen([{'id': 'P1', 'title': 'First'}])
        bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'waiting', 'Ada', 'provider')
        other = self.other_ready_plan()
        self.round(alive=lambda pid: True)
        self.assertFalse([a for a in self.queens() if a['plan_path'] == other])
        self.assertIn('P1', (self.env().run_dir('Ada') / bees.NOTICE_FILE).read_text(encoding='utf-8'))

    # A package that waits for an answer, a build or the owner reserves no slot: the new Queen starts.
    def test_package_waiting_for_an_answer_reserves_no_slot(self):
        self.machine['providers'][0]['slots'] = 2
        self.machine['providers'][1]['slots'] = 0
        self.running_queen([{'id': 'P1', 'title': 'First'}])
        bees.set_package_state(self.conn, 'testproj', REL, 'P1', 'waiting', 'Ada', 'question:1')
        other = self.other_ready_plan()
        self.round(alive=lambda pid: True)
        self.assertEqual([a['provider'] for a in self.queens() if a['plan_path'] == other], ['claude'])

    # A capability package reserves the slot of the provider that offers the capability, not the Queen's own: the new Queen takes the other slot.
    def test_reservation_takes_the_capable_provider_slot(self):
        self.machine['providers'][0]['slots'] = 2
        self.machine['providers'][1]['slots'] = 1
        self.running_queen([{'id': 'I1', 'title': 'Images', 'capability': 'images'}])
        other = self.other_ready_plan()
        self.round(alive=lambda pid: True)
        self.assertEqual([a['provider'] for a in self.queens() if a['plan_path'] == other], ['claude'])
        self.assertIn('I1', (self.env().run_dir('Ada') / bees.NOTICE_FILE).read_text(encoding='utf-8'))

    # A package of a class only another provider offers reserves that provider's slot: the new Queen takes the Queen provider's slot.
    def test_reservation_takes_a_slot_of_a_provider_with_the_class(self):
        self.machine['providers'] = [provider('claude', 2, classes=('light', 'medium')), provider('codex', 1, proto='codex')]
        self.running_queen([{'id': 'P1', 'title': 'Heavy', 'class': 'heavy'}])
        other = self.other_ready_plan()
        self.round(alive=lambda pid: True)
        self.assertEqual([a['provider'] for a in self.queens() if a['plan_path'] == other], ['claude'])
        self.assertIn('P1', (self.env().run_dir('Ada') / bees.NOTICE_FILE).read_text(encoding='utf-8'))

    # An explicit owner choice 'restart the Queen on provider X' binds the Hive to X until the owner changes it.
    def test_owner_restart_choice_binds_the_hive(self):
        self.limits(claude=10, codex=80)
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        hives.requeen(self.conn, 'testproj', REL, provider='codex')
        self.round()
        first = self.queens()
        self.assertEqual([a['provider'] for a in first], ['codex'])
        self.finish(first[0]['name'], final='Handoff')
        runtime.upsert_agent(self.conn, 'testproj', first[0]['name'], {'pid': None})
        self.round()
        second = [a for a in self.queens() if a['name'] != first[0]['name']]
        self.assertEqual([a['provider'] for a in second], ['codex'])   # the binding outlives the first restart
        self.finish(second[0]['name'], final='Handoff')
        runtime.upsert_agent(self.conn, 'testproj', second[0]['name'], {'pid': None})
        hives.requeen(self.conn, 'testproj', REL)   # a plain restart: the owner lifts the binding, the normal choice decides
        self.round()
        third = [a for a in self.queens() if a['name'] not in (first[0]['name'], second[0]['name'])]
        self.assertEqual([a['provider'] for a in third], ['claude'])


class SlotTextTests(unittest.TestCase):
    """README, owner question texts and the Queen role text describe the slot and routing rules."""

    def texts(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        queen = roles.role_text('queen')
        german = '\n'.join('%s\n%s' % (k, v) for k, v in i18n.GERMAN.items()) if hasattr(i18n, 'GERMAN') else ''
        return readme, queen, german

    def test_texts_no_longer_promise_free_subagents_or_fixed_providers(self):
        readme, queen, german = self.texts()
        for text in (readme, queen, german):
            self.assertNotIn('no extra slot', text)
            self.assertNotIn('need no slot of their own', text)
            self.assertNotIn('keinen eigenen Slot', text)
        self.assertNotIn('never silently change it', queen)
        self.assertNotIn('restart its package with the same class and provider', queen)
        self.assertNotIn('Queen plus a Bee process', readme)
        self.assertNotIn('restart the Queen (on the same provider', readme)

    def test_texts_describe_slots_and_provider_choice(self):
        readme, queen, _ = self.texts()
        self.assertIn('every Bee takes one slot', readme)
        self.assertIn('every Bee takes one slot', queen)
        self.assertIn('another provider', queen)
        self.assertIn('chooses its provider by the normal provider choice', readme)


if __name__ == '__main__':
    unittest.main()
