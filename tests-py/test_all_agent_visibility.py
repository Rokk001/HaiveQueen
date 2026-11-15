"""Every active role must remain visible and counted, without duplicate fix rows."""
import json
from haivequeen import runtime
from test_o3_dashboard import DashboardCase
from test_o1_orchestrator import REL


class AllAgentVisibility(DashboardCase):
    def add_agent(self, name, phase, status='running', plan=REL):
        runtime.upsert_agent(self.conn, 'testproj', name, dict(
            plan=plan, phase=phase, provider='claude', status=status,
            startedAt='2026-01-01T00:00:00', model='model', effort='medium'))

    def test_all_roles_appear_and_match_busy_and_provider_counts(self):
        self.ready_plan()
        roles = ['queen', 'bee', 'fix', 'verify', 'acceptance', 'question', 'future-role']
        for role in roles:
            self.add_agent(role, role, status='starting' if role == 'verify' else 'running')
        runtime.kv_set(self.conn, 'testproj', 'package-diagnosis:' + json.dumps([REL, 'switch']),
                       json.dumps(dict(phase='running', agent='fix')))
        self.add_agent('finished', 'fix', 'done')
        snap = self.snap()
        rows = [r for r in snap['bees'] if r.get('agent')]
        self.assertCountEqual([r['agent'] for r in rows], roles)
        self.assertTrue(all(r['plan_path'] == REL for r in rows))
        self.assertEqual(next(r for r in rows if r['agent'] == 'fix')['task'], 'Diagnosis: switch')
        self.assertEqual(snap['tiles']['bees'], len(roles))
        self.assertEqual(next(h for h in snap['hives'] if h['plan_path'] == REL)['bees_running'], len(roles) - 1)
        self.assertEqual(len(snap['overlays']['bees']['rows']), len(roles))
        self.assertEqual(next(p for p in snap['limits'] if p['name'] == 'Claude')['slots']['used'], len(roles))

    def test_diagnosis_without_queen_has_plan_and_row(self):
        self.ready_plan()
        self.add_agent('review', 'fix')
        self.conn.execute('DELETE FROM hives')
        self.conn.commit()
        snap = self.snap()
        self.assertIn(REL, [h['plan_path'] for h in snap['hives']])
        self.assertEqual([r['agent'] for r in snap['bees'] if r.get('agent')], ['review'])
        self.assertEqual(snap['tiles']['bees'], 1)

    def test_integration_fix_counted_once_and_in_busy_details(self):
        self.pipeline({'Merge': 'done', 'Check': 'red'}, 'Check', 'fix',
                      dict(agent='repair', reason='step', step='Check', error='failed'))
        self.add_agent('repair', 'fix')
        self.add_agent('diagnosis', 'fix')
        snap = self.snap()
        self.assertCountEqual([r.get('agent') for r in snap['bees'] if r.get('agent')], ['repair', 'diagnosis'])
        self.assertEqual(snap['tiles']['bees'], 2)
        self.assertEqual(len(snap['overlays']['bees']['rows']), 2)

    def test_active_agent_on_done_plan_remains_visible(self):
        self.ready_plan()
        self.conn.execute("UPDATE plans SET status='DONE' WHERE path=?", (REL,))
        self.conn.commit()
        self.add_agent('final-check', 'verify')
        snap = self.snap()
        self.assertIn(REL, [h['plan_path'] for h in snap['hives']])
        self.assertEqual(snap['tiles']['bees'], 1)

    def test_embedded_agent_without_live_queen_stays_visible_and_counted(self):
        from haivequeen import bees
        self.ready_plan()
        bees.record_embedded_start(self.conn, 'testproj', REL, 'P1', 'sub', 'medium', 'claude')
        snap = self.snap()
        self.assertEqual(len([r for r in snap['bees'] if r.get('embedded')]), 1)
        self.assertEqual(snap['tiles']['bees'], 1)
        self.assertEqual(len(snap['overlays']['bees']['rows']), 1)
        self.assertEqual(next(p for p in snap['limits'] if p['name'] == 'Claude')['slots']['used'], 1)

    def test_disabled_provider_still_displays_occupied_slots(self):
        from haivequeen import overview
        self.add_agent('review', 'fix')
        cfg = {'providers': [dict(name='claude', enabled=False, slots=3)]}
        rows = overview.provider_lines(self.conn, cfg, runtime.list_agents(self.conn, None, 'all'))
        self.assertEqual(rows[0]['used_slots'], 1)
        self.assertFalse(rows[0]['next'])
