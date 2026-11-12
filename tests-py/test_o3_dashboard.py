"""O3 (O-7): the dashboard shows the Orchestrator: "Orch." and the step chain in the plan table, the Orchestrator row and the indented fix agent row
in the Queens-and-agents table with its header count, and the "Orch." lines in the log."""
import sys
from datetime import datetime

import test_o1_orchestrator as o1
from haivequeen import gui_data, orchestrator, runtime

SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}


class DashboardCase(o1.OrchCase):
    """Plans in the pipeline, read through the dashboard snapshot."""

    def setUp(self):
        self.check, self.build, self.deploy = "echo c", "echo b", "echo d"
        super().setUp()

    def snap(self):
        return gui_data.snapshot(self.conn, now=datetime.now(), sysinfo=SYS)

    def hive(self, rel=o1.REL):
        return next(h for h in self.snap()["hives"] if h["plan_path"] == rel)

    def pipeline(self, steps, step, phase="pipeline", fix=None):
        """Put the stored pipeline state of the plan into the given condition."""
        self.ready_plan()
        state = orchestrator._new_state(self.conn, self.env(), o1.REL, None)
        state.update(phase=phase, step=step, steps=steps, fix=fix)
        orchestrator.save(self.conn, "testproj", o1.REL, state)
        return state


class PlanTable(DashboardCase):
    def test_chain_and_counter(self):
        self.pipeline({"Merge": "done", "Check": "done", "Build": "running"}, "Build")
        h = self.hive()
        self.assertEqual(h["queen"], "Orch.")
        self.assertEqual(h["orch"]["chain"], [("Merge", "done"), ("Check", "done"), ("Build", "running"), ("Push", "todo"), ("Deploy", "todo")])
        self.assertEqual(h["status"], "Integration 3/5")

    def test_red_step_and_fix_agent_running(self):
        self.pipeline({"Merge": "done", "Check": "red"}, "Check", "fix", {"agent": "x", "reason": "step", "step": "Check", "error": "boom"})
        h = self.hive()
        self.assertEqual(h["orch"]["chain"][1], ("Check", "red"))
        self.assertEqual(h["status"], "Fix agent running")

    def test_skipped_step_is_not_shown_and_not_counted(self):
        self.build = ""
        self.project_toml()
        self.pipeline({"Merge": "done", "Check": "running"}, "Check")
        h = self.hive()
        self.assertEqual([s for s, _ in h["orch"]["chain"]], ["Merge", "Check", "Push", "Deploy"])
        self.assertEqual(h["status"], "Integration 2/4")

    def test_plan_outside_the_pipeline_has_no_orch(self):
        self.ready_plan()
        h = self.hive()
        self.assertNotEqual(h["queen"], "Orch.")
        self.assertIsNone(h.get("orch"))


class AgentTable(DashboardCase):
    def test_header_count_and_rows(self):
        self.pipeline({"Merge": "done", "Check": "red"}, "Check", "fix", {"agent": "Nora__fix", "reason": "step", "step": "Check", "error": "boom"})
        runtime.upsert_agent(self.conn, "testproj", "Nora__fix", {"plan": o1.REL, "phase": "fix", "provider": "claude", "status": "running",
                                                                  "startedAt": "2026-01-01T00:00:00", "person": "Nora"})
        s = self.snap()
        self.assertEqual(s["tiles"]["orchestrators"], 1)
        rows = s["bees"]
        orch = [r for r in rows if r["kind"] == "orch"]
        self.assertEqual(len(orch), 1)
        self.assertEqual(orch[0]["name"], "Orchestrator")
        self.assertEqual(orch[0]["task"], "Integration: Check red")
        self.assertEqual(orch[0]["status"], "waiting")
        fix = [r for r in rows if r["kind"] == "fix"]
        self.assertEqual(len(fix), 1)
        self.assertEqual(fix[0]["name"], "Nora")
        self.assertEqual(fix[0]["task"], "Fix: Check red")
        self.assertEqual(fix[0]["status"], "running")
        self.assertEqual(rows.index(fix[0]), rows.index(orch[0]) + 1)

    def test_running_step_row(self):
        self.pipeline({"Merge": "running"}, "Merge")
        orch = [r for r in self.snap()["bees"] if r["kind"] == "orch"][0]
        self.assertEqual((orch["task"], orch["status"]), ("Integration: Merge running", "running"))


class LogLines(DashboardCase):
    def test_orch_tag(self):
        orchestrator._log(self.env(), "PLAN: Merge started (1/5)")
        line = self.snap()["log"][-1]
        self.assertEqual(line[1], "Orch.")
        self.assertIn("Merge started (1/5)", line[2])

    def test_pipeline_writes_the_lines(self):
        self.ready_plan()
        self.round()
        texts = [l[2] for l in self.snap()["log"] if l[1] == "Orch."]
        self.assertTrue(any("Queen done, integration taken over" in t for t in texts))
        self.assertTrue(any("Check started (2/5)" in t for t in texts))
        self.assertTrue(any("Deploy done (5/5)" in t for t in texts))
