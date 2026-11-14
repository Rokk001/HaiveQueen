"""Shared helpers of the round tests: a throw-away runtime (database, settings, logs below a temp folder), dummy projects and plans, a provider list.

Nothing here touches a real project: every test builds its own folders, BZZ_TEST_NOSTART starts no real agent.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from haivequeen import config, db, loop, runtime, servers, worktrees  # noqa: E402
from haivequeen import scheduler as sch  # noqa: E402

ENV_KEYS = ("BZZ_DATA_DIR", "BZZ_CONFIG_DIR", "BZZ_LOG_DIR", "BZZ_RUNTIME_OVERRIDE", "BZZ_TEST_NOSTART", "BZZ_PROVIDER_LIMITS_FILE", "BZZ_HOST")
REL = "docs/plans/dummy/DUMMY-PLAN.md"


def integrated_tip(repo):
    """Create a real commit contained in the dummy project's main branch."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("add", "-A")
    git("commit", "-qm", "integrated plan")
    return git("rev-parse", "HEAD")


def provider(name, slots, use="preferred", caps=(), limit="", classes=("light", "medium", "heavy"), proto="claude", build=True):
    """One provider entry as config.toml has it; the models are called <name>-<class>. It has the old build entry unless build=False, so its Queen may do the Build step."""
    p = {"name": name, "protocol": proto, "command": proto, "args": ["--model", "{model}"], "slots": slots, "use": use,
         "capabilities": list(dict.fromkeys(list(caps) + (["build"] if build else []))), "classes": {c: {"model": "%s-%s" % (name, c), "effort": "medium"} for c in classes}}
    if limit:
        p["limit"] = {"source": limit}
    return p


def classic_providers():
    """The two classic providers: claude (haiku/sonnet/opus, build) and codex (images, build), 2 slots each; their Queens may do Build because of the old build entry."""
    return [
        {"name": "claude", "protocol": "claude", "command": "claude", "args": ["--model", "{model}"], "slots": 2, "use": "preferred",
         "capabilities": ["build"], "limit": {"source": "claude"},
         "classes": {"light": {"model": "claude-haiku-5-5", "effort": "medium"}, "medium": {"model": "claude-sonnet-5-5", "effort": "medium"},
                     "heavy": {"model": "claude-opus-5-5", "effort": "medium"}}},
        {"name": "codex", "protocol": "codex", "command": "codex", "args": ["exec", "-m", "{model}"], "slots": 2, "use": "preferred",
         "capabilities": ["images", "build"], "limit": {"source": "codex"},
         "classes": {"light": {"model": "gpt-6-luna", "effort": "medium"}, "medium": {"model": "gpt-6.1-terra", "effort": "medium"},
                     "heavy": {"model": "gpt-6.1-sol", "effort": "medium"}}},
    ]


def minutes_ago(n):
    """ISO time n minutes ago (the format of the database)."""
    return (datetime.now() - timedelta(minutes=n)).isoformat(timespec="seconds")


def write_plan(path, status, packages=(), title="Dummy plan", branch="", worktree=""):
    """Write a plan file with a TOML header; an empty status writes a plan without any header. Packages: dicts id, title, class, agent, capability, depends."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not status:
        path.write_text("# %s\nNo header here.\n" % title, encoding="utf-8")
        return
    t = '+++\nstatus = "%s"\n' % status
    if branch:
        t += 'branch = %s\n' % json.dumps(branch)
    if worktree:
        t += 'worktree = %s\n' % json.dumps(str(worktree))
    for p in packages:
        t += '[[packages]]\nid = "%s"\ntitle = "%s"\n' % (p["id"], p.get("title", ""))
        for k in ("class", "agent", "capability", "verify"):
            if p.get(k):
                t += '%s = "%s"\n' % (k, p[k])
        t += "depends_on = [%s]\n" % ", ".join('"%s"' % d for d in p.get("depends", []) if d)
        if p.get("requirements"):
            t += "requirements = [%s]\n" % ", ".join('"%s"' % r for r in p["requirements"])
    path.write_text(t + "+++\n# %s\n" % title, encoding="utf-8")


class RoundCase(unittest.TestCase):
    """Base class: temp runtime, one dummy project `testproj`, helpers to build state and run rounds."""

    providers = None      # provider list of the machine settings (default: classic_providers)
    shared_logs = True    # one log folder for all projects (single-project tests); False = <data>/logs/<project>
    settings = None       # extra machine/project settings

    def setUp(self):
        """Create the temp runtime, the database and the dummy project."""
        from unittest import mock
        # Round tests mock process launches; web service lifecycle has its own real-process tests.
        patcher = mock.patch('haivequeen.web_service.start', return_value={'ok': True, 'url': 'http://127.0.0.1:8484'})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch("haivequeen.platform.process_inaccessible", return_value=False)   # the tests use made-up PIDs that may belong to real system processes
        patcher.start()
        self.addCleanup(patcher.stop)
        answers = {"reachable": True, "models": [], "interfaces": {cli: {"ok": True} for cli in servers.INTERFACES}}   # the own server of a provider (also a migrated endpoint) answers in these tests
        patcher = mock.patch.object(servers, "kept", lambda server: dict(answers, fingerprint=servers.fingerprint(server)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["BZZ_DATA_DIR"] = str(self.base / "data")
        os.environ["BZZ_CONFIG_DIR"] = str(self.base / "cfg")
        if self.shared_logs:
            os.environ["BZZ_LOG_DIR"] = str(self.base / "logs")
        else:
            os.environ.pop("BZZ_LOG_DIR", None)
        os.environ["BZZ_RUNTIME_OVERRIDE"] = str(self.base)
        os.environ["BZZ_TEST_NOSTART"] = "1"
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = ""
        os.environ["BZZ_HOST"] = "testhost"
        self.conn = db.connect(config.db_path())
        self.machine = {"providers": self.providers if self.providers is not None else classic_providers(), "slots": 2, "buildJobs": 0,
                        "ramReserveMb": 2048, "projects": []}
        self.machine.update(self.settings or {})
        load_config = config.load_config
        def machine_config():
            """bzz commands called in the test read the same machine settings as its rounds, unless the test wrote a config.toml (their Queen's provider must exist)."""
            return load_config() if (config.config_dir() / "config.toml").exists() else config._merge(config.DEFAULT_CONFIG, self.machine)
        patcher = mock.patch("haivequeen.config.load_config", machine_config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.wt = self.base / "wt"
        self.wt.mkdir()
        self.repo = self.add_project("testproj")

    def tearDown(self):
        """Restore the environment and remove the temp folder."""
        worktrees.wait_cleanups(30)   # a cleanup of a round runs in a thread of its own and uses the database
        self.conn.close()
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def add_project(self, name, priority=0, extra=""):
        """Create a workspace folder with project.toml and register the project; returns the workspace path."""
        ws = self.base / ("repo-" + name if name != "testproj" else "repo")
        (ws / ".haivequeen").mkdir(parents=True, exist_ok=True)
        (ws / ".haivequeen" / "project.toml").write_text('name = "%s"\npriority = %d\nplanGlobs = ["docs/plans/**/*.md"]\n%s' % (name, priority, extra), encoding="utf-8")
        db.upsert_project(self.conn, name, str(ws), "white", priority)
        return ws

    def envs(self):
        """Project environments, freshly loaded."""
        return sch.load_projects(self.conn, self.machine)

    def mock_agent_worktrees(self):
        """Isolate Git preparation and audit in routing/rule tests; B2 tests use real Git."""
        from unittest import mock
        folder = self.base / 'assigned-agent-worktree'
        folder.mkdir(exist_ok=True)
        for patcher in (mock.patch('haivequeen.bees.prepare_agent_worktree', return_value=(str(folder), 'plan-p1')),
                        mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True),
                        mock.patch('haivequeen.branchguard.prepare', return_value={}),
                        mock.patch('haivequeen.branchguard.violation', return_value='')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, name="testproj"):
        """The environment of one project."""
        return next(e for e in self.envs() if e.name == name)

    def plan(self, status="IN_PROGRESS", packages=(), project="testproj", rel=REL, title="Dummy plan", branch="", worktree=""):
        """Write the dummy plan of a project and read it into the database."""
        ws = self.repo if project == "testproj" else self.base / ("repo-" + project)
        write_plan(ws / rel, status, packages, title, branch, worktree)
        runtime.sync_plans(self.conn, project, str(ws))

    def allow_ready(self, project="testproj", rel=REL):
        """Let a Queen's `bzz ready` pass the ready gate: the plan names one requirement, the state file lists it done, the check agent passes."""
        from unittest import mock
        from haivequeen import readygate
        ws = self.repo if project == "testproj" else self.base / ("repo-" + project)
        with (ws / rel).open("a", encoding="utf-8") as f:
            f.write("\n## Requirements\n\n- T-1: dummy requirement\n")
        runtime.record_submission(self.conn, project, rel, (ws / rel).read_text(encoding="utf-8"))   # the plan as submitted is the reference of the ready check
        (ws / rel.replace("-PLAN.md", "-STATE.md")).write_text("# State\n\n## Completeness\n\n- T-1: done | test evidence\n", encoding="utf-8")
        patcher = mock.patch.object(readygate, "CHECK_RUNNER", lambda request: ("passed", ""))
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, name, project="testproj", **f):
        """Create or update an agent row; `started`/`ended` are minutes ago. Defaults: claude, the dummy plan, phase work, cwd = wt, status done."""
        row = {"type": "claude", "provider": "claude", "plan_path": REL, "phase": "work", "cwd": str(self.wt), "status": "done"}
        if "started" in f:
            row["started_at"] = minutes_ago(f.pop("started"))
        if "ended" in f:
            row["ended_at"] = minutes_ago(f.pop("ended"))
        row.update(f)
        return runtime.upsert_agent(self.conn, project, name, row)

    def finish(self, name, final="", status="done", project="testproj"):
        """Mark an agent ended with an optional final answer file."""
        runtime.upsert_agent(self.conn, project, name, {"status": status, "ended_at": db.now(), "exit_code": 0, "last_line": "test"})
        if final:
            d = self.env(project).run_dir(name)
            d.mkdir(parents=True, exist_ok=True)
            (d / "final.txt").write_text(final, encoding="utf-8")

    def ag(self, name, project="testproj"):
        """The newest row of an agent, or None."""
        return runtime.get_agent(self.conn, project, name)

    def names(self, project="testproj"):
        """Names of all agent rows of a project."""
        return [a["name"] for a in runtime.list_agents(self.conn, project, "all")]

    def round(self, answer=None, **kw):
        """Run one round over all projects with plenty of RAM; `answer` is ignored (the Beekeeper asks no model any more)."""
        kw.setdefault("free_ram", lambda: 16.0)
        kw.setdefault("settle", 0)
        kw.pop("brain_runner", None)
        from unittest import mock
        with mock.patch("haivequeen.bees.prepare_queen_worktree", return_value=str(self.wt)), mock.patch("haivequeen.bees.valid_queen_worktree", side_effect=lambda env, path: str(path) != str(env.code_repo)), mock.patch("haivequeen.bees.foreign_plan_branch", return_value=False):
            return loop.run_round(self.conn, self.machine, self.envs(), **kw)

    def calls(self):
        """Number of brain calls recorded so far."""
        return len(runtime.usage_list(self.conn, kind="brain"))

    def brain_answer(self, *actions):
        """A fake brain answer with these actions."""
        return {"actions": list(actions)}

    def log(self, project="testproj"):
        """Text of the ROUNDS.log mirror of a project."""
        f = runtime.mirror_dir(project) / "ROUNDS.log"
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    def seed(self):
        """A finished old work row that gives the dummy plan a known worktree, so the routine plans it without the brain."""
        self.agent("DUMMY-SEED-work", package="SEED", started=30, ended=20, exit_code=0, last_line="seed")


if __name__ == "__main__":
    print(json.dumps({"ok": True}))


def verified_tool_evidence(case, available):
    """Explicit tool-discovery fixture for routing tests; never enable undeclared tools."""
    from unittest import mock
    patcher = mock.patch('haivequeen.mcp_setup.status', side_effect=lambda provider: {
        'available': available.get(provider.get('name'), []), 'servers': {}})
    patcher.start()
    case.addCleanup(patcher.stop)
