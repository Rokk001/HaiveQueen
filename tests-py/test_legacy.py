"""Tests of haivequeen/cli.py import-legacy with invented v1 files in a temp folder and a temp database."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AGENTS = [
    {"name": "ALPHA-P1-work", "person": "Anna", "bundle": ["P2"], "provider": "claude", "paneId": 3, "type": "claude",
     "plan": "docs\\plans\\plan-a.md", "package": "P1", "phase": "work", "pid": 111, "tokensIn": 1000, "tokensOut": 20,
     "startedAt": "2026-01-02T10:00:00+01:00", "status": "running", "lastLine": "Fertig: Käse über Öl", "ctxTokens": 5},
    {"name": "ALPHA-P2-work", "startedAt": "2026-01-02T11:00:00+01:00", "status": "failed", "exitCode": 1},
    "not an agent",
]
ARCHIVE = [{"name": "BETA-P1-work", "startedAt": "2026-01-01T09:00:00+01:00", "status": "done", "exitCode": 0,
            "tokensIn": 7, "tokensOut": 3}]
ROUNDS = ("﻿2026-01-02 10:00:00 start ALPHA-P1-work (claude, work)\r\n"
          "2026-01-02 10:05:00 NOTE Größe prüfen\r\n"
          "2026-01-02 10:05:00 NOTE Größe prüfen\r\n"
          "this line is broken\r\n")
EVENTS = "09:58|O|ALPHA-P1-work gestartet\n10:02|E|ALPHA-P2-work Fehler\nbroken\n"
BRAIN = ('{"ts":"2026-01-02T10:00:30+01:00","trigger":"agent_failed","model":"Model X","context":100,"input":500,'
         '"output":50,"durationSec":12,"actions":"a; b","error":false}\n'
         "{broken json\n"
         '{"ts":"2026-01-02T11:00:30+01:00","trigger":"verify_open","model":"Model X","context":1,"input":2,'
         '"output":3,"durationSec":1,"actions":"","error":true}\n')
QUESTIONS = (
    "# Open questions\r\n\r\n"
    "## Q-20260102-001\r\n- Plan: docs\\plans\\plan-a.md\r\n- Item: P1 Größe\r\n- Question: Soll es größer sein?\r\n"
    "- Recommendation: Ja.\r\n- Teilantwort 2026-01-02: vielleicht\r\n- Answer (owner 2026-01-03): Ja, 12.\r\n- Status: ANSWERED\r\n\r\n"
    "## Q-20260102-002\r\n- Plan: docs\\plans\\plan-a.md\r\n- Item: P2\r\n- Question: Welche Farbe?\r\n"
    "- Recommendation: Blau.\r\n- Status: OPEN\r\n\r\n"
    "## Q-20260102-003\r\n- Plan: docs\\plans\\plan-a.md\r\n- Item: P3\r\n- Recommendation: none\r\n- Status: OPEN\r\n"
)
ANSWERS = ("# Decisions\n\n## 2026-01-03 Größe P1\nOwner: \"12 ist ok\"\n\n## P2 Farbe\nBlau, final.\n\n## \nempty heading\n")
QUEUE = (
    "# Queue\n\n| Branch | Tip | Herkunfts-Plan | Status | Reihenfolge / Abhaengigkeit | Datum |\n|---|---|---|---|---|---|\n"
    "| feature/one | aaa111 | alpha | integriert (Lauf 3, gepusht) | unabhaengig | 2026-01-02 |\n"
    "| feature/two | bbb222 | alpha | ready | nach one | 2026-01-03 |\n"
    "| feature/three | ccc333 | beta | in Lauf | | 2026-01-04 |\n"
    "| feature/four | ddd444 | beta | blockiert (Build rot) | | 2026-01-05 |\n"
    "| feature/five | eee555 | beta | unbekannt (anderswo) | | 2026-01-05 |\n"
)
PLAN_A = ("# Plan A\nStatus: READY\n\n| Package | Content | Class | Depends on |\n|---|---|---|---|\n"
          "| P1 | Erstes Paket | light | none |\n| P2 | Zweites Paket | heavy | P1 |\n")


class LegacyTest(unittest.TestCase):
    """Runs the import-legacy command on invented v1 files."""

    def setUp(self):
        """Write the invented v1 files into a temp folder and point the data folder there."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.env = dict(os.environ, BZZ_CONFIG_DIR=str(base / "cfg"), BZZ_DATA_DIR=str(base / "data"),
                        BZZ_LOG_DIR=str(base / "logs"))
        rt = base / "rt"
        rt.mkdir()
        (rt / "AGENTS.json").write_text(json.dumps(AGENTS, ensure_ascii=False, indent=2), encoding="utf-8")
        (rt / "AGENTS-ARCHIVE.json").write_text(json.dumps(ARCHIVE), encoding="utf-8")
        (rt / "ROUNDS.log").write_bytes(ROUNDS.encode("utf-8"))
        (rt / "ORCH-EVENTS.log").write_text(EVENTS, encoding="utf-8")
        (rt / "BUILD-JOBS.json").write_text('{"HOST1|alpha": 258.0}', encoding="utf-8")
        (rt / "BRAIN-USAGE.jsonl").write_text(BRAIN, encoding="utf-8")
        (rt / "ORCH-HANDLED.json").write_text('{"verify:A-P1":"2026-01-02 10:20","q:Q-1":"2026-01-02 10:40"}', encoding="utf-8")
        (rt / "NOFOLLOW.json").write_text('{"keys":["Plan-A.md|P1","plan-a.md|p2"]}', encoding="utf-8")
        (rt / "ORCH-STATE.json").write_text('{"status":"idle"}', encoding="utf-8")
        (rt / "usage-claude.json").write_text('{"sevenDay":{"utilization":0.4}}', encoding="utf-8")
        (base / "q.md").write_bytes(QUESTIONS.encode("utf-8"))
        (base / "a.md").write_bytes(ANSWERS.encode("utf-8"))
        (base / "queue.md").write_text(QUEUE, encoding="utf-8")
        (base / "plans").mkdir()
        (base / "plans" / "plan-a.md").write_text(PLAN_A, encoding="utf-8")
        self.args = ["import-legacy", "--project", "alpha", "--runtime", str(rt), "--questions", str(base / "q.md"),
                     "--answers", str(base / "a.md"), "--queue", str(base / "queue.md"),
                     "--plans", str(base / "plans" / "*.md")]

    def tearDown(self):
        """Remove the temp folder."""
        self.tmp.cleanup()

    def run_import(self):
        """Run the import; return (exit code, parsed JSON)."""
        r = subprocess.run([sys.executable, "-I", str(ROOT / "haivequeen" / "cli.py"), *self.args], env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, json.loads(r.stdout)

    def counts(self):
        """Row counts of all imported tables."""
        conn = sqlite3.connect(str(self.base / "data" / "haivequeen.db"))
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("agents", "events", "build_measurements", "providers_usage", "handled", "nofollow",
                              "questions", "decisions", "queue", "kv", "plans", "packages")}
        finally:
            conn.close()

    def query(self, sql, args=()):
        """Run a query on the test database."""
        conn = sqlite3.connect(str(self.base / "data" / "haivequeen.db"))
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def test_imports_all_sources(self):
        """Every source lands in its table with the expected values."""
        code, out = self.run_import()
        self.assertEqual(code, 0, out)
        src = out["sources"]
        self.assertEqual(src["AGENTS.json"]["imported"], 2)
        self.assertEqual(src["AGENTS.json"]["errors"], 1)
        self.assertEqual(src["AGENTS-ARCHIVE.json"]["imported"], 1)
        self.assertEqual(self.counts(), {
            "agents": 3, "events": 5, "build_measurements": 1, "providers_usage": 2, "handled": 2, "nofollow": 2,
            "questions": 2, "decisions": 2, "queue": 4, "kv": 2, "plans": 1, "packages": 2})
        a = self.query("SELECT * FROM agents WHERE name='ALPHA-P1-work'")[0]
        self.assertEqual((a["archived"], a["pid"], a["pane_id"], a["tokens_in"], a["plan_path"], a["last_line"]),
                         (0, 111, "3", 1000, "docs/plans/plan-a.md", "Fertig: Käse über Öl"))
        self.assertEqual(json.loads(a["bundle_json"]), ["P2"])
        self.assertEqual(json.loads(a["extra_json"])["ctxTokens"], 5)
        self.assertEqual(self.query("SELECT archived FROM agents WHERE name='BETA-P1-work'")[0]["archived"], 1)

    def test_events_and_text_encoding(self):
        """BOM, CRLF, umlauts and the time-only events log are read; broken lines are counted."""
        code, out = self.run_import()
        self.assertEqual(out["sources"]["ROUNDS.log"], {"imported": 3, "skipped": 0, "errors": 1,
                                                        "error_samples": out["sources"]["ROUNDS.log"]["error_samples"]})
        self.assertEqual(out["sources"]["ORCH-EVENTS.log"]["errors"], 1)
        texts = [r["text"] for r in self.query("SELECT text FROM events WHERE kind='rounds' ORDER BY id")]
        self.assertEqual(texts, ["start ALPHA-P1-work (claude, work)", "NOTE Größe prüfen", "NOTE Größe prüfen"])
        ev = self.query("SELECT ts, text, data_json FROM events WHERE kind='events' ORDER BY ts")
        self.assertEqual([e["text"] for e in ev], ["ALPHA-P1-work gestartet", "ALPHA-P2-work Fehler"])
        self.assertTrue(json.loads(ev[1]["data_json"])["error"])
        self.assertEqual(self.query("SELECT ts FROM events WHERE kind='rounds' ORDER BY id")[0]["ts"], "2026-01-02T10:00:00")

    def test_questions_answers_queue_plans(self):
        """Questions, decisions, queue rows and plans carry the right status and fields."""
        code, out = self.run_import()
        q = self.query("SELECT * FROM questions ORDER BY id")
        self.assertEqual((q[0]["status"], q[0]["answered_by"], q[0]["item"], q[0]["plan_path"]),
                         ("answered", "owner", "P1 Größe", "docs/plans/plan-a.md"))
        self.assertTrue(q[0]["answer"].startswith("Ja, 12"))
        self.assertEqual((q[1]["status"], q[1]["answer"]), ("open", None))
        self.assertEqual(out["sources"]["questions"]["errors"], 1)
        d = self.query("SELECT * FROM decisions ORDER BY id")
        self.assertEqual(d[0]["ts"], "2026-01-03T00:00:00")
        self.assertIn("12 ist ok", d[0]["text"])
        self.assertEqual(out["sources"]["answers"]["errors"], 1)
        st = {r["branch"]: r for r in self.query("SELECT * FROM queue")}
        self.assertEqual({k: v["status"] for k, v in st.items()},
                         {"feature/one": "integrated", "feature/two": "ready", "feature/three": "in_run",
                          "feature/four": "blocked"})
        self.assertIn("Build rot", st["feature/four"]["note"])
        self.assertEqual(st["feature/one"]["tip"], "aaa111")
        self.assertEqual(out["sources"]["queue"]["errors"], 1)
        plan = self.query("SELECT * FROM plans")[0]
        self.assertEqual((plan["status"], plan["title"]), ("READY", "Plan A"))
        pk = {r["id"]: r for r in self.query("SELECT * FROM packages")}
        self.assertEqual((pk["P2"]["class"], json.loads(pk["P2"]["depends_on"])), ("heavy", ["P1"]))
        self.assertEqual([r["key"] for r in self.query("SELECT key FROM nofollow ORDER BY key")],
                         ["plan-a.md|p1", "plan-a.md|p2"])
        self.assertEqual(self.query("SELECT value FROM kv WHERE key='loop-state'")[0]["value"], '{"status":"idle"}')
        self.assertEqual(self.query("SELECT per_job_mb, host FROM build_measurements")[0], {"per_job_mb": 258.0, "host": "HOST1"})
        u = self.query("SELECT * FROM providers_usage ORDER BY ts")
        self.assertEqual((u[0]["kind"], u[0]["tokens_in"], u[0]["error"], u[1]["error"]), ("brain", 500, 0, 1))

    def test_second_run_adds_nothing(self):
        """A second run inserts no rows; everything is reported as skipped."""
        self.run_import()
        first = self.counts()
        code, out = self.run_import()
        self.assertEqual(code, 0)
        self.assertEqual(self.counts(), first)
        for name, res in out["sources"].items():
            self.assertEqual(res["imported"], 0, name)

    def test_missing_files_are_ignored(self):
        """Absent optional runtime files are not an error; a missing explicit file is."""
        os.remove(self.base / "rt" / "BUILD-JOBS.json")
        code, out = self.run_import()
        self.assertEqual(code, 0)
        self.assertNotIn("BUILD-JOBS.json", out["sources"])
        self.args[self.args.index("--queue") + 1] = str(self.base / "nope.md")
        code, out = self.run_import()
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
