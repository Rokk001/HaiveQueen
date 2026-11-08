"""Tests of the step bar of running Bees (process Bees and subagent Bees) in the Queens and Bees table, and of the Bee rules a subagent Bee gets."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import isolation  # noqa: E402,F401  (redirects the default data and settings folders to a temp folder before anything is imported)
from bzztest import REL, RoundCase  # noqa: E402
from haivequeen import bees, cli, db, gui_data, hives, runtime  # noqa: E402

PLAN = "docs/plans/AI-LEVELS-PLAN.md"
SYS = {"cores": 8, "cpu": 38, "ram_used_gb": 25.2, "ram_total_gb": 32.0, "ram_pct": 80, "disk_used_tb": 1.2, "disk_total_tb": 2.7, "disk_pct": 45}
CFG = {"slots": 2, "providers": [{"name": "claude"}]}


class StepBarTest(unittest.TestCase):
    """One Hive with a running Queen, packages P1 and P2, and the Bees of the tests."""

    # Build the data.
    def setUp(self):
        """A temp database with a running Queen."""
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "o.db")
        db.upsert_project(self.conn, "ODP", None, "cyan", 1)
        hives.create(self.conn, "ODP", PLAN, "claude", "cyan")
        self.conn.execute("UPDATE hives SET state='running' WHERE project='ODP'")
        for pid in ("P1", "P2"):
            self.conn.execute("INSERT INTO packages(project,plan_path,id,title) VALUES ('ODP',?,?,?)", (PLAN, pid, pid))
        self.conn.commit()
        runtime.upsert_agent(self.conn, "ODP", "ODP__queen", {"plan": PLAN, "phase": "queen", "provider": "claude", "status": "running", "startedAt": db.now(), "person": "Ava"})
        patch = mock.patch("haivequeen.loop.loop_running", return_value=0)
        patch.start()
        self.addCleanup(patch.stop)

    # Close the database.
    def tearDown(self):
        """Release the temp database."""
        self.conn.close()
        self.tmp.cleanup()

    # Start a process Bee.
    def process_bee(self, name, package, status="running", agent=None):
        """Store a Bee process row of `package`."""
        runtime.upsert_agent(self.conn, "ODP", agent or "ODP__" + name, {"plan": PLAN, "phase": "bee", "provider": "claude", "status": status, "startedAt": db.now(),
                                                                         "person": name, "package": package})

    # A step report of an agent.
    def note(self, agent, text):
        """Store a bzz note of `agent`."""
        runtime.append_event(self.conn, "ODP", text, kind="note", agent=agent, mirror="none")   # no log file: tests must not write outside their temp folders

    # The row of a Bee in the window's data.
    def row(self, name):
        """The Bees-table row of the Bee `name`."""
        snap = gui_data.snapshot(self.conn, now=datetime.now(), cfg=CFG, sysinfo=SYS)
        return next(r for r in snap["bees"] if r["name"] == name)

    # U-5: a process Bee starts at 0/m.
    def test_process_bee_shows_the_bar_from_its_start(self):
        """A running process Bee that has not reported shows 0/m of the step count it was started with, and an empty bar."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        row = self.row("Sophia")
        self.assertEqual((row["step"], row["step_frac"]), ("0/4", 0.0))

    # U-5: a subagent Bee starts at 0/m, too.
    def test_subagent_bee_shows_the_bar_from_its_start(self):
        """A running subagent Bee shows 0/m, and the Queen's step report moves the bar."""
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P2", "Mona", "heavy", "claude", "claude-opus-5-5", "medium")
        bees.record_step_count(self.conn, "ODP", PLAN, "P2", 5)
        self.assertEqual(self.row("Mona")["step"], "0/5")
        self.note("ODP__queen", "Mona P2: step 2/5 wrote the tests")
        row = self.row("Mona")
        self.assertEqual((row["step"], row["step_frac"]), ("2/5", 0.4))

    # U-5: the report need not have the exact wording.
    def test_other_wordings_of_a_step_report_count(self):
        """A report that names the package and a step number moves the bar, whatever else it says."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        for text, want in (("Sophia P1: step 1/4 read the code", "1/4"), ("Sophia (P1) - Step 2 of 4: tests written", "2/4"),
                           ("P1 step 3/4 done, Sophia", "3/4"), ("Sophia P1 Schritt 4/4 fertig", "4/4"), ("Sophia, package P1, step 2/4 again", "2/4")):
            self.note("ODP__Sophia", text)
            self.assertEqual(self.row("Sophia")["step"], want, text)

    # U-5: a text without a package or without a step number is no step report.
    def test_text_without_package_or_step_is_no_report(self):
        """A note that names no package of the plan, or has no step number, leaves the bar where it is."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        self.note("ODP__Sophia", "Sophia P1: step 1/4 read")
        self.note("ODP__Sophia", "Sophia P1: tests are green")
        self.note("ODP__Sophia", "Sophia: step 3/4 without a package")
        self.assertEqual(self.row("Sophia")["step"], "1/4")

    def test_out_of_range_step_reports_are_ignored(self):
        """Step reports must stay within 0..m and use a positive total."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        self.note("ODP__Sophia", "Sophia P1: step 5/4 outside range")
        self.assertEqual(self.row("Sophia")["step"], "0/4")
        hive = hives.hive_data(self.conn, "ODP", runtime.list_agents(self.conn, "ODP", "all"), PLAN)[0]
        self.assertEqual([(bee["name"], bee["step"]) for bee in hive["bees"]], [("Sophia", "")])
        self.note("ODP__Sophia", "Sophia P1: step 2/0 invalid total")
        self.assertEqual(self.row("Sophia")["step"], "0/4")
        hive = hives.hive_data(self.conn, "ODP", runtime.list_agents(self.conn, "ODP", "all"), PLAN)[0]
        self.assertEqual([(bee["name"], bee["step"]) for bee in hive["bees"]], [("Sophia", "")])

    def test_step_ranges_show_the_highest_step_in_both_readers(self):
        """A range report advances the Bee and package displays to its end."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 6)
        self.process_bee("Sophia", "P1")
        self.note("ODP__Sophia", "Sophia P1: step 2-5/6 tests done")
        hive = hives.hive_data(self.conn, "ODP", runtime.list_agents(self.conn, "ODP", "all"), PLAN)[0]
        self.assertEqual(hive["bees"][0]["step"], "5/6")
        self.assertEqual(hive["bees"][0]["task"], "tests done")
        self.assertEqual(hive["package_steps"]["P1"], {"step": "5/6", "task": "tests done"})
        self.assertEqual(self.row("Sophia")["step"], "5/6")
        for invalid in ("step 5-2/6", "step 2-7/6", "step 2-5/0"):
            self.note("ODP__Sophia", "Sophia P1: " + invalid)
            hive = hives.hive_data(self.conn, "ODP", runtime.list_agents(self.conn, "ODP", "all"), PLAN)[0]
            self.assertEqual(hive["bees"][0]["step"], "5/6", invalid)
            self.assertEqual(hive["package_steps"]["P1"]["step"], "5/6", invalid)
        self.note("ODP__Sophia", "Sophia P1: step 5-6/6 finished")
        hive = hives.hive_data(self.conn, "ODP", runtime.list_agents(self.conn, "ODP", "all"), PLAN)[0]
        self.assertEqual(hive["bees"], [])
        self.assertEqual(hive["package_steps"]["P1"]["step"], "6/6")

    # U-5: a replacement keeps the last state until its own first report.
    def test_replacement_bee_keeps_the_last_state_until_its_first_step(self):
        """The replacement Bee of a package shows the last reported state, with the old step count, until it reports its own first step; then its own count counts."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        self.note("ODP__Sophia", "Sophia P1: step 3/4 almost there")
        self.process_bee("Sophia", "P1", status="failed")
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 6)   # the replacement gets other steps
        self.process_bee("Lena", "P1")
        self.assertEqual(self.row("Lena")["step"], "3/4")
        self.note("ODP__Lena", "Lena P1: step 1/6 continued")
        self.assertEqual(self.row("Lena")["step"], "1/6")

    # U-5: a replacement subagent likewise.
    def test_replacement_subagent_keeps_the_last_state(self):
        """A replaced subagent Bee of the same package keeps the last reported state."""
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P2", "Mona", "heavy", "claude")
        bees.record_step_count(self.conn, "ODP", PLAN, "P2", 5)
        self.note("ODP__queen", "Mona P2: step 4/5 nearly")
        bees.record_embedded_start(self.conn, "ODP", PLAN, "P2", "Nora", "heavy", "claude")
        bees.record_step_count(self.conn, "ODP", PLAN, "P2", 3)
        self.assertEqual(self.row("Nora")["step"], "4/5")
        self.note("ODP__queen", "Nora P2: step 1/3 started over")
        self.assertEqual(self.row("Nora")["step"], "1/3")

    def test_step_range_parser_keeps_only_valid_reports(self):
        """The Hive table and per-package progress use the same 0..m step range."""
        bees.record_step_count(self.conn, "ODP", PLAN, "P1", 4)
        self.process_bee("Sophia", "P1")
        self.note("ODP__Sophia", "Sophia P1: step 2/4 valid")
        self.note("ODP__Sophia", "Sophia P1: step 5/4 outside range")
        self.note("ODP__Sophia", "Sophia P1: step 2/0 invalid total")
        agents = runtime.list_agents(self.conn, "ODP", "all")
        rows = hives.hive_data(self.conn, "ODP", agents, PLAN)
        self.assertEqual(rows[0]["package_steps"]["P1"]["step"], "2/4")
        self.assertEqual(hives.package_steps(self.conn, "ODP", PLAN, {"P1"}, agents, [
            {"project": "ODP", "agent": "ODP__Sophia", "text": "Sophia P1: step 2/4 valid"},
            {"project": "ODP", "agent": "ODP__Sophia", "text": "Sophia P1: step 5/4 outside range"},
            {"project": "ODP", "agent": "ODP__Sophia", "text": "Sophia P1: step 2/0 invalid total"},
        ])["P1"]["step"], "2/4")


class SubagentRulesTest(RoundCase):
    """A subagent Bee gets the Bee rules of roles/bee.md, and both kinds of Bee record how many steps they were given."""

    # A Queen of a provider whose Bees run as subagents (Claude Code only, M-6).
    def setUp(self):
        """Plan with P1 and a running Queen."""
        super().setUp()
        self.plan('IN_PROGRESS', [{'id': 'P1'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.machine['providers'][0]['beeMode'] = 'subagent'

    # Run bzz bee start.
    def start(self, no_subagents=False):
        """Return the parsed answer of bzz bee start for package P1."""
        args = cli.build_parser().parse_args(['bee', 'start', '--project', 'testproj', '--plan', REL, '--package', 'P1', '--class', 'medium', '--name', 'Ada',
                                              '--task', 'build the thing', '--steps', '["read", "write", "test"]', '--cwd', str(self.wt)] + (['--no-subagents'] if no_subagents else []))
        output = io.StringIO()
        with mock.patch.dict('os.environ', {'BZZ_AGENT': 'queen'}), mock.patch.object(cli.config, 'load_config', return_value=self.machine), \
                mock.patch('haivequeen.bees.valid_bee_worktree', return_value=True), contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_bee(args, self.conn), 0, output.getvalue())
        return json.loads(output.getvalue().strip().splitlines()[-1])

    def test_process_start_marks_package_working(self):
        """A successful process Bee start records the package as working."""
        self.machine['providers'][1]['beeMode'] = 'process'
        with mock.patch('haivequeen.loop._do_start', return_value=True):
            out = self.start(no_subagents=True)
        self.assertEqual(out['kind'], 'process')
        self.assertEqual(self.conn.execute("SELECT state FROM package_progress WHERE package='P1'").fetchone()[0], 'working')

    # The prompt a subagent gets.
    def test_start_of_a_subagent_returns_the_bee_prompt_and_records_the_steps(self):
        """bzz bee start answers kind subagent with the prompt built from roles/bee.md (task, numbered steps, the step report format) and stores the step count."""
        out = self.start()
        self.assertEqual(out["kind"], "subagent")
        self.assertIn("You are a Bee", out["prompt"])
        self.assertIn("build the thing", out["prompt"])
        self.assertIn("3. test", out["prompt"])
        self.assertIn("Ada P1: step n/3", out["prompt"])
        self.assertEqual(self.conn.execute("SELECT steps FROM bee_step_counts WHERE package='P1'").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT state FROM package_progress WHERE package='P1'").fetchone()[0], "working")

    # The Queen is told to hand the prompt on.
    def test_queen_role_tells_to_use_the_returned_prompt(self):
        """The Queen's role text says that the subagent gets the returned prompt."""
        text = (Path(__file__).resolve().parent.parent / "roles" / "queen.md").read_text(encoding="utf-8")
        self.assertIn("returned prompt", text)


if __name__ == "__main__":
    unittest.main()
