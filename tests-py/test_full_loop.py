"""Gap 3c: a real loop run (run_loop, several rounds) over a throw-away project, headless, with the real agent runner and a fake provider CLI.

Plan READY -> work agent -> verify agent ("Offene Anforderungen: 0") -> release agent (registers the branch with bzz ready) -> integrator agent
(dummy command: marks the queue row integrated). Nothing here touches the live runtime, a real model or a terminal.
"""
import json
import os
import stat
import sys
import unittest

from bzztest import REL, RoundCase, provider, runtime
from haivequeen import loop
from haivequeen.terminal.headless import Headless

ONE = [{"id": "P1", "title": "first"}]

# The fake provider CLI: finds the agent name in the prompt file, does what that kind of agent does and prints a Claude stream-json result.
FAKE_CLI = r'''import argparse, contextlib, io, json, os, sys
sys.path.insert(0, os.environ["FAKE_TOOLS"])
from haivequeen import cli, config, db, runtime
name = os.environ["BZZ_AGENT"]
conn = db.connect(config.db_path())
plan = os.environ["FAKE_PLAN"]
with contextlib.redirect_stdout(io.StringIO()):
    ready = argparse.Namespace(project="testproj", branch="feature/dummy", tip="abc1234", plan=plan, note="")
    if cli.cmd_ready(ready, conn) != 0:
        raise RuntimeError("ready failed")
    acquire = argparse.Namespace(action="acquire", project="testproj", plan=plan, owner_pid=None)
    if cli.cmd_integration(acquire, conn) != 0:
        raise RuntimeError("integration acquire failed")
    runtime.queue_upsert(conn, "testproj", "feature/dummy", status="integrated")
    acquire.action = "release"
    if cli.cmd_integration(acquire, conn) != 0:
        raise RuntimeError("integration release failed")
conn.close()
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(name + "\n")
for ev in ({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": "working"}]}},
           {"type": "result", "is_error": False, "result": "Integrated.", "usage": {"input_tokens": 10, "output_tokens": 5}}):
    print(json.dumps(ev))
'''


class FullLoopTest(RoundCase):
    """One real run_loop with a Queen integrating a plan."""

    def setUp(self):
        """Fake CLI as the only provider, headless terminal, a READY plan with one package."""
        super().setUp()
        os.environ.pop("BZZ_TEST_NOSTART", None)
        script = self.base / "fake_cli.py"
        script.write_text(FAKE_CLI, encoding="utf-8")
        if sys.platform == "win32":
            cmd = self.base / "fake-claude.cmd"
            cmd.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), encoding="utf-8")
        else:
            cmd = self.base / "fake-claude.sh"
            cmd.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding="utf-8")
            cmd.chmod(cmd.stat().st_mode | stat.S_IEXEC)
        self.log_file = self.base / "agents-run.log"
        os.environ.update({"FAKE_TOOLS": str(loop.TOOLS_DIR), "FAKE_LOG": str(self.log_file), "FAKE_PLAN": REL})
        cfg = self.base / "cfg"
        cfg.mkdir(exist_ok=True)
        toml = ("[[providers]]\nname = 'claude'\ncommand = '%s'\nargs = []\n"
                "[providers.classes.medium]\nmodel = 'claude-sonnet-5-5'\neffort = 'medium'\n" % str(cmd).replace(chr(92), "/"))
        (cfg / "config.toml").write_text(toml, encoding="utf-8")   # the agent runner reads the providers from this file
        prov = provider("claude", 3)
        prov["command"] = str(cmd)
        prov["args"] = []
        self.machine["providers"] = [prov]
        self.plan("READY", ONE)
        self.seed()
        self.term = Headless(self.base / "panes")

    def tearDown(self):
        """Close leftover panes and drop the fake-CLI variables."""
        for p in self.term.list_panes("HaiveQueen"):
            if p.alive:
                self.term.close_pane(p.id)
        for k in ("FAKE_TOOLS", "FAKE_LOG", "FAKE_PLAN"):
            os.environ.pop(k, None)
        super().tearDown()

    # Verify that a Queen starts and integrates her own plan.
    def test_plan_to_integrated(self):
        """READY plan: one Queen integrates her branch and the plan becomes DONE."""
        round_kwargs = {"terminal": self.term, "free_ram": lambda: 16.0, "settle": 0, "poll": 0.2, "tries": 150,
                        "brain_runner": lambda argv, prompt, cwd, timeout: (1, "", "no real brain in tests", False)}
        code = loop.run_loop(self.conn, self.machine, rounds=8, round_minutes=0.05, wait_seconds=0.3, round_kwargs=round_kwargs)
        self.assertEqual(code, 0)
        ran = self.log_file.read_text(encoding="utf-8").split() if self.log_file.is_file() else []
        self.assertEqual(len(ran), 1, "the plan should have one Queen: %s\n%s" % (ran, self.log()))
        self.assertEqual(runtime.list_plans(self.conn, "testproj")[0]["status"], "DONE")
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM queue")]
        self.assertEqual([r["status"] for r in rows], ["integrated"], json.dumps(rows, default=str))
        left = [a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["status"] in ("running", "starting")]
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
