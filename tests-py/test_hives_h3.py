"""Dashboard coverage for Hive display and owner controls."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from datetime import datetime

from haivequeen import dashboard, db, hives, runtime

SYS = {'cores': 4, 'free_mb': 4000, 'total_mb': 8000, 'host': 'test', 'compiler_count': None}

class HiveDashboardTest(unittest.TestCase):
    """Display persistent Hives and their owner actions."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / 'db')
        db.upsert_project(self.conn, 'alpha', None, 'cyan', 1)
        hives.create(self.conn, 'alpha', 'A-PLAN.md', 'claude', 'red')
        hives.create(self.conn, 'alpha', 'B-PLAN.md', 'codex', 'green')
        runtime.upsert_agent(self.conn, 'alpha', 'q1', {'plan': 'A-PLAN.md', 'phase': 'queen', 'status': 'running', 'person': 'Ada'})
        runtime.upsert_agent(self.conn, 'alpha', 'bee1', {'plan': 'A-PLAN.md', 'phase': 'bee', 'status': 'running', 'person': 'Bea', 'task': 'Build'})
        db.add_event(self.conn, 'alpha', 'note', 'Bea H2: step 2/5 Build')
    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
    def text(self, color=False):
        return dashboard.render(self.conn, width=100, height=100, now=datetime.now(), cfg={}, sysinfo=SYS, color=color)
    def test_hive_frames_and_bee_progress(self):
        text = self.text(True)
        self.assertIn('Beekeeper', text)
        self.assertIn('A-PLAN', text)
        self.assertIn('Ada', text)
        self.assertIn('Bea Build 2/5', text)
        self.assertIn('\x1b[91m', text)
        self.assertIn('\x1b[92m', text)
    def test_waiting_states(self):
        for state, expected in [('resting', 'ruht'), ('queenless', 'weisellos'), ('integration_wait', 'wartet auf Integration'), ('answer', 'wartet auf Antwort'), ('owner_check', 'wartet auf Owner-Prüfung'), ('codex_wait', 'wartet auf Codex')]:
            with self.subTest(state=state):
                self.conn.execute('UPDATE hives SET state=?,reset_at=? WHERE plan_path=?', (state, '2026-11-01T12:00:00', 'A-PLAN.md'))
                text = self.text()
                self.assertIn(expected, text)
                if state == 'resting': self.assertIn('12:00', text)
    def test_dashboard_owner_confirmation_and_provider_refusal(self):
        dashboard.select_hive(self.conn, 'alpha', 0)
        with mock.patch('haivequeen.dashboard.hives.dequeen') as end:
            dashboard.handle_key(self.conn, 'x', 'alpha', ['alpha'], now=10)
            end.assert_not_called()
            dashboard.handle_key(self.conn, 'x', 'alpha', ['alpha'], now=11)
            end.assert_called_once()
        with mock.patch('haivequeen.dashboard.hives.requeen', side_effect=ValueError('provider does not offer the Queen class')):
            dashboard.handle_key(self.conn, 'r', 'alpha', ['alpha'], now=20)
            dashboard.handle_key(self.conn, 'r', 'alpha', ['alpha'], now=21)
            self.assertIn('provider does not offer the Queen class', self.text())

    def test_embedded_bees_follow_queen_notes(self):
        self.conn.execute("INSERT INTO packages(project,plan_path,id) VALUES ('alpha','A-PLAN.md','P1')")
        self.conn.execute("INSERT INTO packages(project,plan_path,id) VALUES ('alpha','B-PLAN.md','P1')")
        db.add_event(self.conn, 'alpha', 'note', 'Cleo P1: step 1/3 Draft', agent='q1')
        self.assertIn('Cleo Draft 1/3', self.text())
        db.add_event(self.conn, 'alpha', 'note', 'Cleo P1: step 3/3 Done', agent='q1')
        self.assertNotIn('Cleo Done 3/3', self.text())
