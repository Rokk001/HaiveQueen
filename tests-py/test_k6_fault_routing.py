import unittest
from datetime import datetime
from bzztest import RoundCase
from haivequeen import db, faults

class FaultRoutingTest(RoundCase):
    def test_monitor_runs_without_haivequeen_and_routes_to_own_apiary(self):
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'first'}])
        env = self.env()
        faults.check_round(env, 0, True, now=datetime(2026, 10, 29, 12))
        faults.check_round(env, 0, True, now=datetime(2026, 10, 29, 12))
        rows = db.rows(self.conn, 'SELECT * FROM questions WHERE project=? AND plan_path=?', (env.name, faults.FAULT_PLAN))
        self.assertEqual([q['item'] for q in rows], ['no_start'])
