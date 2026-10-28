"""Gap 3c: a real loop run (run_loop, several rounds) over a throw-away project, headless, with the real agent runner and a fake provider CLI.

Plan READY -> work agent -> verify agent ("Offene Anforderungen: 0") -> release agent (registers the branch with orch ready) -> integrator agent
(dummy command: marks the queue row integrated). Nothing here touches the live runtime, a real model or a terminal.
"""
import json
import os
import stat
import sys
import unittest

from orchtest import REL, RoundCase, provider, runtime
from orchlib import loop
from orchlib.terminal.headless import Headless

ONE = [{"id": "P1", "title": "first"}]

# The fake provider CLI: finds the agent name in the prompt file, does what that kind of agent does and prints a Claude stream-json result.
FAKE_CLI = r'''import json, os, re, sys
sys.path.insert(0, os.environ["FAKE_TOOLS"])
from orchlib import config, db, runtime
prompt = sys.argv[sys.argv.index("-p") + 1]
text = open(re.search(r"Read the file (.+?) completely", prompt).group(1), encoding="utf-8-sig").read()
name = re.search(r"You are the agent (\S+)", text).group(1)
conn = db.connect(config.db_path())
result = "Done."
if name.startswith("INTEGRATOR"):
    for row in db.queue_rows(conn, "testproj"):
        if row["status"] == "ready":
            runtime.queue_upsert(conn, "testproj", row["branch"], status="integrated", note="dummy integration")
    result = "Integrated."
elif "verify" in name:
    result = "All requirements checked.\nOffene Anforderungen: 0"
elif name.endswith("-release"):
    db.add_ready(conn, "testproj", "feature/dummy", "abc1234", os.environ["FAKE_PLAN"], "Kompiliertest: Exit 0")
    result = "Released."
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(name + "\n")
for ev in ({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": "working"}]}},
           {"type": "result", "is_error": False, "result": result, "usage": {"input_tokens": 10, "output_tokens": 5}}):
    print(json.dumps(ev))
'''


class FullLoopTest(RoundCase):
    """One real run_loop over the phases of a plan."""

    def setUp(self):
        """Fake CLI as the only provider, headless terminal, a READY plan with one package."""
        super().setUp()
        os.environ.pop("ORCH_TEST_NOSTART", None)
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
        for p in self.term.list_panes("Orchestrator"):
            if p.alive:
                self.term.close_pane(p.id)
        for k in ("FAKE_TOOLS", "FAKE_LOG", "FAKE_PLAN"):
            os.environ.pop(k, None)
        super().tearDown()

    def test_plan_to_integrated(self):
        """READY plan: work, verify, release and integrator agents run one after the other; the queue row ends 'integrated'."""
        round_kwargs = {"terminal": self.term, "free_ram": lambda: 16.0, "settle": 0, "poll": 0.2, "tries": 150,
                        "brain_runner": lambda argv, prompt, cwd, timeout: (1, "", "no real brain in tests", False)}
        code = loop.run_loop(self.conn, self.machine, rounds=8, round_minutes=0.05, wait_seconds=0.3, round_kwargs=round_kwargs)
        self.assertEqual(code, 0)
        ran = self.log_file.read_text(encoding="utf-8").split() if self.log_file.is_file() else []
        kinds = ["INTEGRATOR" if n.startswith("INTEGRATOR") else "verify" if "verify" in n else "release" if n.endswith("-release") else "work" for n in ran]
        for k in ("work", "verify", "release", "INTEGRATOR"):
            self.assertIn(k, kinds, "no %s agent ran; agents: %s\n%s" % (k, ran, self.log()))
        self.assertLess(kinds.index("work"), kinds.index("verify"))
        self.assertLess(kinds.index("verify"), kinds.index("release"))
        self.assertLess(kinds.index("release"), kinds.index("INTEGRATOR"))
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM queue")]
        self.assertEqual([r["status"] for r in rows], ["integrated"], json.dumps(rows, default=str))
        left = [a["name"] for a in runtime.list_agents(self.conn, "testproj") if a["status"] in ("running", "starting")]
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main()
