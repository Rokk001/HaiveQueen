import unittest
from types import SimpleNamespace
from unittest import mock

from haivequeen import orchestrator


class ReadyOrder(unittest.TestCase):
    def test_round_visits_ready_plans_in_queue_order(self):
        conn = mock.Mock()
        conn.execute.return_value.fetchall.return_value = [("second", 2), ("first", 1)]
        env = SimpleNamespace(name="project", log=lambda message: None)
        plans = [{"path": "second"}, {"path": "first"}]
        with mock.patch.object(orchestrator.integration, "retire_legacy_steps"), \
             mock.patch.object(orchestrator.runtime, "list_plans", return_value=plans), \
             mock.patch.object(orchestrator, "advance") as advance:
            orchestrator.run_round(conn, env, 10000)
        self.assertEqual([call.args[2] for call in advance.call_args_list], ["first", "second"])
