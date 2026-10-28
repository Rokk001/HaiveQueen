"""Tests of orchlib.agent_runner: helpers, stream parsing for Claude and Codex (fake CLIs), tokens, HANDOFF, final report,
build jobs, the second-pane guard and the pane wrapper. Everything runs in a temp runtime (temp database, own runs folder)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from orchlib import agent_runner as ar  # noqa: E402
from orchlib import db, runtime  # noqa: E402

FAKE_CLI = '''import json, os, sys
rec = {"argv": sys.argv[1:], "build_jobs": os.environ.get("BUILD_JOBS"), "cwd": os.getcwd()}
with open(os.environ["FAKE_RECORD"], "w", encoding="utf-8") as f:
    json.dump(rec, f)
with open(os.environ["FAKE_STREAM"], encoding="utf-8") as f:
    sys.stdout.write(f.read())
sys.stdout.flush()
sys.stderr.write("fake stderr line\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''


def toml_str(value):
    """Return a TOML literal string for a path or text."""
    return "'" + str(value) + "'"


def claude_stream(ctx_big=False):
    """Return a fake Claude stream-json: text, a commit through Bash, its result, an edit, a rate limit event and the result."""
    big = 310000 if ctx_big else 1200
    events = [
        {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 1050, "output_tokens": 20},
                                          "content": [{"type": "text", "text": "Starting the work now."}]}},
        {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 1050, "output_tokens": 20},
                                          "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git commit -m \"Add the thing\""}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[branch abc1234] Add the thing\n 1 file changed"}]}},
        {"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": big - 10, "output_tokens": 30},
                                          "content": [{"type": "tool_use", "id": "t2", "name": "Write", "input": {"file_path": "src/a.txt"}}]}},
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.4, "resetsAt": 1900000000}, "seven_day": {"utilization": 0.2, "resetsAt": 1900100000}}}},
        {"type": "result", "is_error": False, "result": "Open requirements: 0", "usage": {"input_tokens": 500, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 400, "output_tokens": 55}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\nnot json at all\n"


def codex_stream():
    """Return a fake Codex exec --json stream."""
    events = [
        {"type": "thread.started", "thread_id": "thr-123"},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "git push origin x", "aggregated_output": "done\nEXIT=0", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "git commit -m 'Fix it'", "aggregated_output": "[x deadbee] Fix it", "exit_code": 1}},
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "src/b.txt"}]}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}},
        {"type": "turn.completed", "usage": {"input_tokens": 900, "output_tokens": 40}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


class HelperTest(unittest.TestCase):
    """Pure helper functions."""

    def test_text_helpers(self):
        """short, model_label, model_short."""
        self.assertEqual(ar.short("a   b\n c", 3), "a b...")
        self.assertEqual(ar.model_label("claude-sonnet-5-5"), "Sonnet 5.5")
        self.assertEqual(ar.model_label("gpt-6.1-sol"), "GPT-6.1-Sol")
        self.assertEqual(ar.model_short("claude", "claude-sonnet-5-5", "medium"), "Son 5.5 med.")
        self.assertEqual(ar.model_short("codex", "gpt-6.1-sol", "medium"), "Sol 6.1 med.")

    def test_class_model_fallback(self):
        """A class the entry does not list falls back to the nearest lower, else the nearest higher class."""
        prov = {"classes": {"medium": {"model": "m", "effort": "e"}}}
        self.assertEqual(ar.class_model(prov, "heavy"), ("m", "e"))
        self.assertEqual(ar.class_model(prov, "light"), ("m", "e"))
        self.assertEqual(ar.class_model({"classes": {}}, "medium"), ("", ""))
        self.assertEqual(ar.class_index("bogus"), 1)

    def test_classify(self):
        """Milestone commands: commit, push, compile test, check, ready, file write; others are not milestones."""
        self.assertEqual(ar.classify_cmd('git commit -m "Add a thing"'), {"kind": "commit", "label": "Commit: Add a thing"})
        self.assertEqual(ar.classify_cmd("git commit -m 'Other thing'")["label"], "Commit: Other thing")
        self.assertEqual(ar.classify_cmd("git commit")["label"], "Commit")
        self.assertEqual(ar.classify_cmd("git push origin x")["kind"], "push")
        self.assertEqual(ar.classify_cmd("pwsh scripts/compile-check-changed.ps1")["label"], "Compile test")
        self.assertEqual(ar.classify_cmd("python check-leaks.py")["label"], "Check check-leaks.py")
        self.assertEqual(ar.classify_cmd("python -I orch.py ready --branch x")["kind"], "ready")
        self.assertEqual(ar.classify_cmd("echo hi >> QUEUE.md", "QUEUE")["kind"], "ready")
        self.assertIsNone(ar.classify_cmd("echo hi >> QUEUE.md"))
        edit = ar.classify_cmd("Set-Content -Path 'src\\x.txt' -Value 1")
        self.assertEqual(edit, {"kind": "edit", "label": "changed: src\\x.txt"})
        self.assertEqual(ar.classify_cmd("sed -i 's/a/b/' file.cpp")["label"], "changed: file.cpp")
        self.assertIsNone(ar.classify_cmd("ls -la"))

    def test_build_job_count(self):
        """The scenarios of the build job test: first build, RAM limit, core limit, fixed, configured use."""
        def bj(**extra):
            raw = {"envVar": "BUILD_JOBS", "coreShare": "2/3", "reserveMB": 1024, "perJobMB": "auto", "firstJobs": 2}
            raw.update(extra)
            return ar.build_jobs_cfg(raw)
        d = ar.build_jobs_cfg({"envVar": "X"})
        self.assertTrue(abs(d["coreShare"] - 2 / 3) < 1e-9 and d["reserveMB"] == 1024 and d["firstJobs"] == 2 and d["perJobMB"] is None and d["fixed"] is None)
        self.assertEqual(ar.build_job_count(bj(), 12, 32000, None), (2, "first"))
        self.assertEqual(ar.build_job_count(bj(firstJobs=10), 6, 32000, None)[0], 4)
        self.assertEqual(ar.build_job_count(bj(), 12, 4096, 1000), (3, "ram"))
        self.assertEqual(ar.build_job_count(bj(), 12, 1500, 1000)[0], 1)
        self.assertEqual(ar.build_job_count(bj(), 10, 64000, 500), (7, "cores"))
        self.assertEqual(ar.build_job_count(bj(fixed=5), 4, 1200, 2000), (5, "fixed"))
        self.assertEqual(ar.build_job_count(bj(perJobMB=2000), 12, 6144, 100)[0], 2)
        self.assertEqual(ar.build_job_count(bj(), 12, 3072, 1000)[0], 2)
        self.assertEqual(ar.build_job_count(bj(reserveMB=2048), 12, 3072, 1000)[0], 1)
        self.assertEqual(ar.build_use_mb(8000, 5000, 3), 1000)
        self.assertEqual(ar.build_use_mb(3000, 4000, 2), 0)
        self.assertIsNone(ar.build_jobs_cfg(None))
        self.assertEqual(ar.build_jobs_cfg(0)["fixed"], None)
        self.assertEqual(ar.build_jobs_cfg(4)["fixed"], 4)

    def test_args_and_endpoint(self):
        """Argument templates are filled; an endpoint adds the model_provider arguments."""
        prov = {"name": "local", "args": ["-m", "{model}", "-C", "{cwd}", "{unknown}"], "endpoint": {"baseUrl": "http://x/v1", "id": "loc"}}
        got = ar.build_args(prov, {"model": "mm", "cwd": "/w"})
        self.assertEqual(got[:5], ["-m", "mm", "-C", "/w", "{unknown}"])
        self.assertIn("model_providers.loc.base_url=http://x/v1", got)
        self.assertEqual(ar.endpoint_args({"name": "n", "endpoint": None}), [])

    def test_phase_rules(self):
        """Phase rule sections are copied verbatim; a missing section raises; without phaseRules AGENTS.md is used."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "RULES.md").write_text("# Top\n\n## Build\nuse jobs\n```\n## not a heading\n```\nmore\n\n## Style\nspaces\n", encoding="utf-8")
            out = ws / "rules.md"
            pcfg = {"phaseRules": {"work": {"main": ["## Build", "## Style"]}, "verify": {"main": ["## Nope"]}}, "ruleSources": {"main": "RULES.md"}}
            self.assertEqual(ar.write_phase_rules(pcfg, str(ws), "work", out), 2)
            text = out.read_text(encoding="utf-8")
            self.assertIn("## Build\nuse jobs\n```\n## not a heading\n```\nmore", text)
            self.assertIn("## Style\nspaces", text)
            with self.assertRaises(ValueError):
                ar.write_phase_rules(pcfg, str(ws), "verify", out)
            with self.assertRaises(ValueError):
                ar.write_phase_rules(pcfg, str(ws), "release", out)
            (ws / "AGENTS.md").write_text("# Agents\nrule 1\n", encoding="utf-8")
            self.assertEqual(ar.write_phase_rules({}, str(ws), "work", out), 1)
            self.assertIn("rule 1", out.read_text(encoding="utf-8"))

    def test_codex_tokens(self):
        """Live tokens come from the last token_count line of the session log."""
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026" / "10" / "28"
            day.mkdir(parents=True)
            line1 = json.dumps({"payload": {"info": {"last_token_usage": {"input_tokens": 5}, "total_token_usage": {"input_tokens": 6, "output_tokens": 7}}}, "type": "token_count"}, separators=(",", ":"))
            line2 = json.dumps({"type": "token_count", "payload": {"info": {"last_token_usage": {"input_tokens": 50}, "total_token_usage": {"input_tokens": 60, "output_tokens": 70}}}}, separators=(",", ":"))
            (day / "rollout-thr-9.jsonl").write_text("garbage\n" + line1 + "\n" + line2 + "\n", encoding="utf-8")
            self.assertEqual(ar.get_codex_tokens("thr-9", tmp), {"ctx": 50, "tin": 60, "tout": 70})
            self.assertIsNone(ar.get_codex_tokens("other", tmp))
            self.assertIsNone(ar.get_codex_tokens("", tmp))


class PushStateTest(unittest.TestCase):
    """push_state of a Codex run takes the totals from the session log."""

    def test_push_state_reads_session_log(self):
        """The row gets the context and totals of the session log of the thread."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ORCH_DATA_DIR"] = str(Path(tmp) / "data")
            try:
                conn = db.connect(Path(tmp) / "data" / "o.db")
                runtime.upsert_agent(conn, "p", "X-1", {"status": "running"})
                line = json.dumps({"type": "token_count", "payload": {"info": {"last_token_usage": {"input_tokens": 11}, "total_token_usage": {"input_tokens": 22, "output_tokens": 33}}}}, separators=(",", ":"))
                (Path(tmp) / "rollout-t1.jsonl").write_text(line + chr(10), encoding="utf-8")
                run = ar.AgentRun(conn, "p", "X-1", "codex", "codex", tmp, Path(tmp) / "run", sessions=tmp)
                (Path(tmp) / "run").mkdir()
                self.assertIsNone(run.tin)
                run.thread = "t1"
                run.push_state(True)
                row = runtime.get_agent(conn, "p", "X-1")
                self.assertEqual((row["ctxTokens"], row["tokens_in"], row["tokens_out"]), (11, 22, 33))
                conn.close()
            finally:
                os.environ.pop("ORCH_DATA_DIR", None)


class RunnerBase(unittest.TestCase):
    """Test runtime: temp config, data and runs folders, fake claude and codex CLIs."""

    def setUp(self):
        """Create the runtime, the database with a project and the fake CLIs."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.env = dict(os.environ)
        self.env.update({"ORCH_CONFIG_DIR": str(base / "cfg"), "ORCH_DATA_DIR": str(base / "data"), "ORCH_LOG_DIR": str(base / "logs"),
                         "PYTHONPATH": str(ROOT), "ORCH_NO_HEADER": "1", "ORCH_CODEX_SESSIONS": str(base / "sessions"),
                         "FAKE_RECORD": str(base / "record.json"), "FAKE_STREAM": str(base / "stream.txt")})
        self.env.pop("BUILD_JOBS", None)
        self.runs = base / "runs"
        self.ws = base / "ws"
        self.ws.mkdir()
        (self.ws / "AGENTS.md").write_text("# Agents\nbe nice\n", encoding="utf-8")
        script = base / "fake_cli.py"
        script.write_text(FAKE_CLI, encoding="utf-8")
        self.fake = {}
        for name in ("claude", "codex"):
            if sys.platform == "win32":
                path = base / ("fake-%s.cmd" % name)
                path.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), encoding="utf-8")
            else:
                path = base / ("fake-%s.sh" % name)
                path.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding="utf-8")
                path.chmod(path.stat().st_mode | stat.S_IEXEC)
            self.fake[name] = path
        self.write_config()
        os.environ.update({k: self.env[k] for k in ("ORCH_CONFIG_DIR", "ORCH_DATA_DIR", "ORCH_LOG_DIR")})
        self.conn = db.connect(base / "data" / "orchestrator.db")
        db.upsert_project(self.conn, "demo", str(self.ws), "FF8800", 0)
        prompt = base / "prompt.md"
        prompt.write_text("# Header\nTASK\nDo the demo task now\n", encoding="utf-8")
        self.prompt = prompt

    def tearDown(self):
        """Close the database and remove the runtime."""
        self.conn.close()
        for k in ("ORCH_CONFIG_DIR", "ORCH_DATA_DIR", "ORCH_LOG_DIR"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def write_config(self, extra=""):
        """Write config.toml with fake claude and codex providers."""
        cfg = self.base / "cfg"
        cfg.mkdir(exist_ok=True)
        text = (extra + "\n[[providers]]\nname = 'claude'\ncommand = %s\nargs = ['--model', '{model}', '--effort', '{effort}', '--append-system-prompt-file', '{rules}']\n"
                "[providers.classes.medium]\nmodel = 'claude-sonnet-5-5'\neffort = 'medium'\n"
                "[providers.classes.heavy]\nmodel = 'claude-opus-5-5'\neffort = 'high'\n"
                "[[providers]]\nname = 'codex'\ncommand = %s\nargs = ['exec', '--json', '-m', '{model}', '-C', '{cwd}']\n"
                "[providers.classes.medium]\nmodel = 'gpt-6.1-sol'\neffort = 'medium'\n") % (toml_str(self.fake["claude"]), toml_str(self.fake["codex"]))
        (cfg / "config.toml").write_text(text, encoding="utf-8")

    def prepare(self, name, provider="claude", **fields):
        """Create the agent row like the scheduler does before the pane starts."""
        row = {"status": "starting", "class": "medium", "person": "Max", "color": "4FA8FF"}
        row.update(fields)
        runtime.upsert_agent(self.conn, "demo", name, row)

    def run_runner(self, name, provider, stream, extra_args=(), exit_code=0, phase="work", env=None, wrap=False):
        """Run `python -m orchlib.agent_runner` with a fake stream; returns the completed process."""
        (self.base / "stream.txt").write_text(stream, encoding="utf-8")
        e = dict(self.env)
        e["FAKE_EXIT"] = str(exit_code)
        e.update(env or {})
        cmd = [sys.executable, "-m", "orchlib.agent_runner"] + (["--wrap"] if wrap else []) + [
            "--project", "demo", "--name", name, "--provider", provider, "--plan", "plans/Demo-PLAN.md", "--package", "P1", "--phase", phase,
            "--cwd", str(self.ws), "--prompt-file", str(self.prompt), "--runs-dir", str(self.runs)] + list(extra_args)
        return subprocess.run(cmd, cwd=str(ROOT), env=e, capture_output=True, text=True, encoding="utf-8", timeout=120)

    def row(self, name):
        """Return the agent row."""
        return runtime.get_agent(self.conn, "demo", name)

    def record(self):
        """Return what the fake CLI recorded (arguments, build jobs variable, working directory)."""
        return json.loads((self.base / "record.json").read_text(encoding="utf-8"))


class ClaudeRunTest(RunnerBase):
    """A full run with a fake Claude stream."""

    def test_claude_done(self):
        """Tokens, context, milestones, final report, usage windows and the report prompt of a finished Claude agent."""
        self.prepare("A-1")
        r = self.run_runner("A-1", "claude", claude_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self.row("A-1")
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual((row["tokens_in"], row["tokens_out"]), (1000, 55))   # the result event totals replace the per-message sums
        self.assertEqual(row["ctxTokens"], 1200)
        self.assertEqual(row["provider"], "claude")
        self.assertEqual(row["model"], "claude-sonnet-5-5")
        self.assertEqual(row["modelShort"], "Son 5.5 med.")
        self.assertTrue(row["ended_at"])
        run_dir = self.runs / "A-1"
        self.assertEqual((run_dir / "final.txt").read_text(encoding="utf-8"), "Open requirements: 0")
        ms = (run_dir / "milestones.txt").read_text(encoding="utf-8")
        self.assertIn("Commit: Add the thing (abc1234) - OK", ms)
        self.assertIn("changed: src/a.txt", ms)
        self.assertIn("Starting the work now.", ms)
        self.assertIn("End: done (exit 0)", ms)
        self.assertEqual(len((run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()), 7)   # raw stream incl. the non-JSON line
        self.assertIn("fake stderr line", (run_dir / "stderr.txt").read_text(encoding="utf-8"))
        usage = json.loads(runtime.kv_get(self.conn, "*", "usage-claude"))
        self.assertEqual(usage["fiveHour"]["utilization"], 0.4)
        self.assertFalse((run_dir / "HANDOFF").exists())
        rec = self.record()
        argv = rec["argv"]
        self.assertEqual(argv[0], "-p")
        self.assertIn("orch.py", argv[1])
        self.assertIn("done --agent %s --result" % row["id"], argv[1])
        self.assertNotIn('"', argv[1])
        self.assertEqual(argv[2:6], ["--model", "claude-sonnet-5-5", "--effort", "medium"])
        self.assertEqual(argv[6], "--append-system-prompt-file")
        self.assertIn("be nice", Path(argv[7]).read_text(encoding="utf-8"))
        self.assertEqual(os.path.normcase(rec["cwd"]), os.path.normcase(str(self.ws)))

    def test_task_from_packages_and_prompt(self):
        """The task text is the package row; without a package row it is the first line after TASK in the prompt file."""
        self.prepare("A-2")
        self.run_runner("A-2", "claude", claude_stream())
        self.assertEqual(self.row("A-2")["task"], "Do the demo task now")
        self.conn.execute("INSERT INTO plans (project, path, title, status) VALUES ('demo','plans/Demo-PLAN.md','Demo','READY')")
        self.conn.execute("INSERT INTO packages (project, plan_path, id, title, phase, locked) VALUES ('demo','plans/Demo-PLAN.md','P1','Build the thing','work',0)")
        self.prepare("A-3")
        self.run_runner("A-3", "claude", claude_stream())
        self.assertEqual(self.row("A-3")["task"], "P1: Build the thing")
        self.prepare("A-4")
        self.run_runner("A-4", "claude", claude_stream(), phase="verify")
        self.assertEqual(self.row("A-4")["task"], "Verify package P1, completeness check")

    def test_handoff_flag(self):
        """A context of 300k or more writes the HANDOFF flag file; the agent still finishes normally."""
        self.prepare("A-5")
        r = self.run_runner("A-5", "claude", claude_stream(ctx_big=True))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.row("A-5")["status"], "done")
        flag = self.runs / "A-5" / "HANDOFF"
        self.assertTrue(flag.is_file())
        self.assertIn("context 310000", flag.read_text(encoding="utf-8"))

    def test_error_result_fails(self):
        """A result event with is_error makes the agent failed, even with exit code 0."""
        self.prepare("B-1")
        stream = json.dumps({"type": "result", "is_error": True, "result": "boom", "usage": {"input_tokens": 1, "output_tokens": 2}}) + "\n"
        self.run_runner("B-1", "claude", stream)
        row = self.row("B-1")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["last_line"].startswith("no result event, exit 0"))   # as in the original: the last line is replaced when the run is not ok

    def test_no_result_event(self):
        """Without a result event the agent is failed with exit code and stderr in the last line."""
        self.prepare("B-2")
        self.run_runner("B-2", "claude", "", exit_code=3)
        row = self.row("B-2")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["exit_code"], 3)
        self.assertIn("no result event, exit 3", row["last_line"])
        self.assertIn("fake stderr line", row["last_line"])

    def test_exit_code_after_done(self):
        """A failing hook after the result (exit code not 0) keeps the agent done and records a warning."""
        self.prepare("B-3")
        self.run_runner("B-3", "claude", claude_stream(), exit_code=1)
        row = self.row("B-3")
        self.assertEqual((row["status"], row["exit_code"]), ("done", 1))
        warnings = [e for e in runtime.list_events(self.conn, "demo", kind="warning") if e["agent"] == "B-3"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Note: exit code 1", (self.runs / "B-3" / "milestones.txt").read_text(encoding="utf-8"))

    def test_runner_error(self):
        """A CLI that cannot be started gives status failed, exit code 99 and a runner error line."""
        self.prepare("B-4")
        self.write_config()
        cfg = (self.base / "cfg" / "config.toml").read_text(encoding="utf-8").replace(toml_str(self.fake["claude"]), "'/no/such/cli'")
        (self.base / "cfg" / "config.toml").write_text(cfg, encoding="utf-8")
        self.run_runner("B-4", "claude", "")
        row = self.row("B-4")
        self.assertEqual((row["status"], row["exit_code"]), ("failed", 99))
        self.assertTrue(row["last_line"].startswith("runner error"))

    def test_unknown_provider(self):
        """A provider that is not in config.toml stops the runner before anything starts."""
        r = self.run_runner("B-5", "nope", "")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("provider 'nope' is not in config.toml", r.stderr)

    def test_second_pane_guard(self):
        """A relaunched pane whose agent runs already (live pid) ends without starting the task again."""
        self.prepare("C-1", status="running", pid=os.getpid())
        r = self.run_runner("C-1", "claude", claude_stream())
        self.assertEqual(r.returncode, 0)
        self.assertIn("already runs", r.stdout)
        self.assertFalse((self.base / "record.json").exists())
        self.assertEqual(self.row("C-1")["status"], "running")

    def test_fake_agent(self):
        """ORCH_FAKE_AGENT (tests only) runs without a model call and ends done."""
        self.prepare("C-2")
        self.run_runner("C-2", "claude", "", env={"ORCH_FAKE_AGENT": "1", "ORCH_FAKE_DELAY": "0"})
        row = self.row("C-2")
        self.assertEqual((row["status"], row["tokens_in"], row["tokens_out"]), ("done", 32000, 960))
        self.assertFalse((self.base / "record.json").exists())

    def test_unclassified_class_escalates_model(self):
        """The class of the agent row selects the model of the provider entry."""
        self.prepare("C-3", **{"class": "heavy"})
        self.run_runner("C-3", "claude", claude_stream())
        self.assertEqual(self.row("C-3")["model"], "claude-opus-5-5")
        self.assertEqual(self.record()["argv"][3], "claude-opus-5-5")
        self.prepare("C-4")
        self.run_runner("C-4", "claude", claude_stream(), extra_args=["--model", "my-model", "--effort", "low"])
        self.assertEqual(self.row("C-4")["model"], "my-model")


class CodexRunTest(RunnerBase):
    """A full run with a fake Codex stream."""

    def test_codex_done(self):
        """Codex tokens are a dash until the session log exists; totals come from the log; commands become milestones."""
        day = self.base / "sessions" / "2026"
        day.mkdir(parents=True)
        line = json.dumps({"type": "token_count", "payload": {"info": {"last_token_usage": {"input_tokens": 777}, "total_token_usage": {"input_tokens": 950, "output_tokens": 45}}}}, separators=(",", ":"))
        (day / "rollout-thr-123.jsonl").write_text(line + "\n", encoding="utf-8")
        self.prepare("D-1", provider="codex")
        r = self.run_runner("D-1", "codex", codex_stream())
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self.row("D-1")
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["type"], "codex")
        self.assertEqual(row["ctxTokens"], 777)
        self.assertEqual((row["tokens_in"], row["tokens_out"]), (900, 40))   # the final write uses the totals of the turn.completed event
        ms = (self.runs / "D-1" / "milestones.txt").read_text(encoding="utf-8")
        self.assertIn("Push - Exit 0: EXIT=0", ms)
        self.assertIn("Commit: Fix it (deadbee) - Exit 1", ms)
        self.assertIn("changed: src/b.txt", ms)
        self.assertEqual((self.runs / "D-1" / "final.txt").read_text(encoding="utf-8"), "OK")
        argv = self.record()["argv"]
        self.assertEqual(argv[:6], ["exec", "--json", "-m", "gpt-6.1-sol", "-C", str(self.ws)])
        self.assertIn("orch.py", argv[6])   # the prompt is the last argument
        self.assertEqual(len(argv), 7)

    def test_codex_no_session_log(self):
        """Without a session log the totals of the turn.completed event are used."""
        self.prepare("D-2", provider="codex")
        self.run_runner("D-2", "codex", codex_stream())
        row = self.row("D-2")
        self.assertEqual((row["tokens_in"], row["tokens_out"]), (900, 40))

    def test_codex_error(self):
        """turn.failed / error events become a red error line; without a completed turn the agent is failed."""
        self.prepare("D-3", provider="codex")
        stream = json.dumps({"type": "error", "message": "bad"}) + "\n"
        self.run_runner("D-3", "codex", stream)
        row = self.row("D-3")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["last_line"].startswith("error:"))


class BuildJobsTest(RunnerBase):
    """Build jobs variable and measurement for the integrate and release phases."""

    def test_build_jobs(self):
        """An integrator agent gets the job count as variable; the measurement is stored; a work agent gets none."""
        self.write_config("buildJobs = 3\nramReserveMb = 2048\n")
        self.prepare("E-1")
        self.run_runner("E-1", "claude", claude_stream(), phase="integrate", env={"ORCH_SAMPLE_SEC": "0.2"})
        row = self.row("E-1")
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["buildJobs"], 3)
        self.assertIn("buildUseMB", row)
        self.assertEqual(self.record()["build_jobs"], "3")
        self.assertTrue((self.runs / "E-1" / "build-samples.txt").read_text(encoding="utf-8").strip())
        meas = self.conn.execute("SELECT jobs FROM build_measurements WHERE project='demo'").fetchall()
        self.assertEqual([m[0] for m in meas], [3])
        kinds = [e["kind"] for e in runtime.list_events(self.conn, "demo")]
        self.assertIn("build", kinds)
        self.prepare("E-2")
        self.run_runner("E-2", "claude", claude_stream(), phase="work")
        self.assertIsNone(self.record()["build_jobs"])
        self.assertNotIn("buildJobs", self.row("E-2"))


class PaneWrapTest(RunnerBase):
    """The pane wrapper keeps the pane for the delay and always exits 0."""

    def test_wrap_runs_child_and_exits_zero(self):
        """The wrapper runs the agent as a child, sets paneClosed and exits 0."""
        self.write_config("paneCloseDelaySeconds = 0\n")
        self.prepare("F-1")
        r = self.run_runner("F-1", "claude", claude_stream(), wrap=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pane closes in 0 s", r.stdout)
        row = self.row("F-1")
        self.assertEqual(row["status"], "done")
        self.assertTrue(row["paneClosed"])

    def test_wrap_exits_zero_when_child_fails(self):
        """A failing child (unknown provider) does not leave the pane open: exit code is still 0."""
        self.write_config("paneCloseDelaySeconds = 0\n")
        self.prepare("F-2")
        r = self.run_runner("F-2", "nope", "", wrap=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("pane closes in 0 s", r.stdout)
        self.assertTrue(self.row("F-2")["paneClosed"])


if __name__ == "__main__":
    unittest.main()
