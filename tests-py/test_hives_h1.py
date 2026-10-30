"""Tests for the Hive configuration and plan parsing."""
import unittest

from haivequeen import plans, scheduler


# Check the requested defaults and machine-wide settings.
class HiveConfigurationTests(unittest.TestCase):
    """Check the requested defaults and machine-wide settings."""

    # A machine starts two Hives and Queens use medium with three Bees.
    def test_hive_defaults(self):
        """A machine starts two Hives and Queens use medium with three Bees."""
        settings = scheduler.build_settings({}, {})
        self.assertEqual(settings["hives"], 2)
        self.assertEqual(settings["queenClass"], "medium")
        self.assertEqual(settings["beesPerQueen"], 3)
        self.assertEqual(settings["escalateAfter"], 3)
        self.assertFalse(settings.get("preferredProvider"))
        self.assertFalse(settings.get("excludedProviders"))

    # A package does not select its provider through the old agent field.
    def test_plan_agent_field_is_ignored(self):
        """A package does not select its provider through the old agent field."""
        plan = plans.parse_plan_text('+++\nstatus = "READY"\n[[packages]]\nid = "H1"\nagent = "codex"\n+++\n# Plan\n')
        self.assertFalse(getattr(plan.packages[0], "agent", ""))

from bzztest import RoundCase, runtime


# Exercise Hive scheduling in an isolated runtime.
class HiveRoundTests(RoundCase):
    """Exercise Hive scheduling in an isolated runtime."""

    # Simulate a finished runner, including its process exit.
    def finish(self, name, final="", status="done", project="testproj"):
        """Simulate a finished runner, including its process exit."""
        super().finish(name, final=final, status=status, project=project)
        runtime.upsert_agent(self.conn, project, name, {"pid": None})

    # One plan starts one Queen even when multiple packages are ready.
    def test_one_queen_for_a_plan_with_two_packages(self):
        """One plan starts one Queen even when multiple packages are ready."""
        self.plan("READY", [{"id": "P1", "title": "First"}, {"id": "P2", "title": "Second"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["phase"], "queen")
        self.assertEqual(self.calls(), 0)

    # A paused plan cannot acquire a Queen.
    def test_pause_does_not_start_queen(self):
        """A paused plan cannot acquire a Queen."""
        self.plan("PAUSED", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(active, [])

    # Project settings cannot override the machine-wide Hive limit.
    def test_hive_count_is_machine_wide(self):
        """Project settings cannot override the machine-wide Hive limit."""
        settings = scheduler.build_settings({"hives": 2}, {"hives": 8})
        self.assertEqual(settings["hives"], 2)

    # A project may set its Queen class and number of Bees.
    def test_queen_settings_may_be_overridden_by_project(self):
        """A project may set its Queen class and number of Bees."""
        settings = scheduler.build_settings({"queenClass": "medium", "beesPerQueen": 3}, {"queenClass": "heavy", "beesPerQueen": 1})
        self.assertEqual(settings["queenClass"], "heavy")
        self.assertEqual(settings["beesPerQueen"], 1)

    # The removed stop and agent controls are absent from defaults.
    def test_old_stop_settings_removed(self):
        """The removed stop and agent controls are absent from defaults."""
        settings = scheduler.build_settings({}, {})
        for key in ("progressVerifies", "planStallHours", "minClass", "bundle", "triage", "brain", "integratorSlots"):
            with self.subTest(key=key):
                self.assertNotIn(key, settings)

    # A Queen still uses her provider slot while integrating.
    def test_queen_integration_occupies_provider_slot(self):
        """A Queen still uses her provider slot while integrating."""
        from bzztest import provider
        providers = scheduler.get_providers({"providers": [provider("claude", 2)]})
        rows = [{"status": "running", "phase": "integrate", "provider": "claude"}]
        self.assertEqual(scheduler.provider_free(providers, rows)["claude"], 1)

    # A Queen cannot start on a provider without her configured class.
    def test_queen_provider_rejects_unoffered_class(self):
        """A Queen cannot start on a provider without her configured class."""
        from bzztest import provider
        providers = scheduler.get_providers({"providers": [provider("claude", 1, classes=("light",))]})
        picked = scheduler.select_provider(providers, "medium", [], {"claude": 1}, limits={})
        self.assertIsNone(picked)

    # The owner can dequeen or requeen a named Hive from the CLI.
    def test_owner_commands_accept_hive_and_optional_provider(self):
        """The owner can dequeen or requeen a named Hive from the CLI."""
        from haivequeen import cli
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["dequeen", "hive-1"]).command, "dequeen")
        self.assertEqual(parser.parse_args(["requeen", "hive-1", "codex"]).command, "requeen")

    # Two READY plans can each start one Queen when two places are free.
    def test_two_plans_get_separate_queens(self):
        """Two READY plans can each start one Queen when two places are free."""
        self.plan("READY", [{"id": "P1", "title": "First"}], rel="docs/plans/one/ONE-PLAN.md")
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel="docs/plans/two/TWO-PLAN.md")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 2)
        self.assertEqual(len({a["plan_path"] for a in active}), 2)
        self.assertTrue(all(a["phase"] == "queen" for a in active))

    # A branch outside any Hive remains untouched.
    def test_ready_row_without_hive_does_not_start_agent(self):
        """A branch outside any Hive remains untouched."""
        runtime.queue_upsert(self.conn, "testproj", "outside-branch", tip="abc", status="ready")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(active, [])
        row = self.conn.execute("SELECT status FROM queue WHERE branch = ?", ("outside-branch",)).fetchone()
        self.assertEqual(row[0], "ready")

    # Simultaneously running Queens have distinct short names.
    def test_hive_names_are_distinct(self):
        """Simultaneously running Queens have distinct short names."""
        self.plan("READY", [{"id": "P1", "title": "First"}], rel="docs/plans/one/ONE-PLAN.md")
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel="docs/plans/two/TWO-PLAN.md")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 2)
        self.assertEqual(len({a.get("person") for a in active}), 2)
        self.assertTrue(all(a.get("person") in __import__("haivequeen.loop", fromlist=["AGENT_NAMES"]).AGENT_NAMES for a in active))

    # Starting a Hive updates its plan status in the plan file.
    def test_started_plan_becomes_in_progress(self):
        """Starting a Hive updates its plan status in the plan file."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        from bzztest import REL
        self.assertEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status, "IN_PROGRESS")

    # Pause starts no new Queen and leaves the existing process alone.
    def test_global_pause_preserves_running_queen(self):
        """Pause starts no new Queen and leaves the existing process alone."""
        from haivequeen import runstate
        self.agent("Ada", phase="queen", status="running", pid=42)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        runstate.pause(self.conn)
        self.round(alive=lambda pid: True)
        self.assertEqual(self.ag("Ada")["status"], "running")
        self.assertEqual(len(runtime.list_agents(self.conn, "testproj", "active")), 1)

    # Drain waits for a running Queen and starts no replacement.
    def test_drain_preserves_running_queen(self):
        """Drain waits for a running Queen and starts no replacement."""
        from haivequeen import runstate
        self.agent("Ada", phase="queen", status="running", pid=42)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        runstate.drain(self.conn)
        self.round(alive=lambda pid: True)
        self.assertEqual(self.ag("Ada")["status"], "running")
        self.assertEqual(len(runtime.list_agents(self.conn, "testproj", "active")), 1)

    # The existing weekly-limit scoring selects the provider with more free capacity per reset hour.
    def test_hive_provider_uses_most_free_weekly_limit(self):
        """The existing weekly-limit scoring selects the provider with more free capacity per reset hour."""
        from bzztest import provider
        entries = [provider("a", 1, limit="x"), provider("b", 1, limit="x")]
        providers = scheduler.get_providers({"providers": entries})
        limits = {"a": {"usedPercent": 20, "resetHours": 100}, "b": {"usedPercent": 50, "resetHours": 1}}
        picked = scheduler.select_provider(providers, "medium", [], {"a": 1, "b": 1}, limits=limits)
        self.assertEqual(picked["name"], "b")

    # An available preferred provider wins even with less free weekly limit.
    def test_preferred_provider_has_priority_when_available(self):
        """An available preferred provider wins even with less free weekly limit."""
        from bzztest import provider
        self.machine["providers"] = [provider("a", 1), provider("b", 1, "fallback")]
        self.machine["preferredProvider"] = "b"
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "b")

    # First resumed round pauses the four old HQ plans and keeps open questions.
    def test_migration_pauses_existing_hq_plans_and_keeps_questions(self):
        """First resumed round pauses the four old HQ plans and keeps open questions."""
        from haivequeen import runstate
        self.add_project("hq")
        for tag in ("OLD-ONE", "OLD-TWO", "OLD-THREE", "OLD-FOUR"):
            self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}], project="hq", rel=f"docs/plans/{tag}/{tag}-PLAN.md")
            self.agent("old-" + tag, project="hq", plan_path=f"docs/plans/{tag}/{tag}-PLAN.md", phase="work", status="done")
        self.conn.execute("INSERT INTO questions (project,plan_path,item,question,status,created_at) VALUES (?,?,?,?,?,?)",
                          ("hq", "docs/plans/OLD-ONE/OLD-ONE-PLAN.md", "P1", "Which?", "open", "2026-01-01"))
        rel = "docs/plans/OLD-ONE/OLD-ONE-PLAN.md"
        runtime.set_package(self.conn, "hq", rel, "P1", locked=True, reason="old lock")
        runtime.kv_set(self.conn, "hq", "stall:" + rel + "|P1", "old")
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        rows = runtime.list_plans(self.conn, "hq")
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["status"] == "PAUSED" for row in rows))
        self.assertEqual(len(runtime.list_questions(self.conn, "hq", status="open")), 1)
        self.assertFalse(runtime.list_packages(self.conn, "hq", rel)[0]["locked"])
        self.assertIsNone(runtime.kv_get(self.conn, "hq", "stall:" + rel + "|P1"))

    # A finished context handoff starts a new Queen on the same provider.
    def test_context_handoff_restarts_same_hive_and_provider(self):
        """A finished context handoff starts a new Queen on the same provider."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        handoff = self.env().run_dir(first["name"]) / "HANDOFF"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        handoff.write_text("context limit", encoding="utf-8")
        self.finish(first["name"], final="Handoff")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertNotEqual(active[0]["name"], first["name"])
        self.assertEqual(active[0]["provider"], first["provider"])
        self.assertEqual(active[0]["plan_path"], first["plan_path"])

    # A vanished Queen is retried on the same provider, not counted as a failed package.
    def test_interrupted_queen_restarts_without_provider_switch(self):
        """A vanished Queen is retried on the same provider, not counted as a failed package."""
        self.machine["retryWaitMinutes"] = [0]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "last_line": "process gone after orchestrator restart"})
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], first["provider"])
        self.assertEqual(active[0]["plan_path"], first["plan_path"])

    # At 300k the Queen receives the handoff marker without being ended.
    def test_context_limit_marks_running_queen_for_handoff(self):
        """At 300k the Queen receives the handoff marker without being ended."""
        from haivequeen import routine
        queen = {"name": "Ada", "phase": "queen", "status": "running", "ctxTokens": 300000}
        marked = routine.set_handoff_flags(self.env(), [queen])
        self.assertEqual(marked[0]["Name"], "Ada")
        self.assertTrue((self.env().run_dir("Ada") / "HANDOFF").exists())
        self.assertEqual(queen["status"], "running")

    # A rate-limited Queen leaves her Hive resting even if another provider has room.
    def test_rate_limited_hive_does_not_switch_provider(self):
        """A rate-limited Queen leaves her Hive resting even if another provider has room."""
        import json
        import os
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        limits = self.base / "limits.json"
        limits.write_text(json.dumps({first["provider"]: {"usedPercent": 100, "resetHours": 48}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(limits)
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "interrupted": "rate_limit", "last_line": "rate limit"})
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(active, [])

    # A machine-wide limit of one prevents a second project starting a Queen.
    def test_hive_limit_counts_across_projects(self):
        """A machine-wide limit of one prevents a second project starting a Queen."""
        self.machine["hives"] = 1
        self.add_project("other")
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.plan("READY", [{"id": "P2", "title": "Second"}], project="other")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, None, "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["phase"], "queen")


    # A living Queen remains running when the Beekeeper restarts.
    def test_running_queen_is_reattached_after_restart(self):
        """A living Queen remains running when the Beekeeper restarts."""
        from haivequeen import reattach
        self.agent("Ada", phase="queen", status="running", pid=1234)
        result = reattach.reattach(self.conn, "testproj", lambda pid: pid == 1234)
        self.assertIn("Ada", result["kept"])
        self.assertEqual(self.ag("Ada")["status"], "running")

    # A vanished Queen is not treated as an ordinary package failure.
    def test_missing_queen_is_recorded_as_interrupted(self):
        """A vanished Queen is not treated as an ordinary package failure."""
        from haivequeen import reattach
        self.agent("Ada", phase="queen", status="running", pid=1234)
        result = reattach.reattach(self.conn, "testproj", lambda pid: False)
        self.assertIn("Ada", result["failed"])
        row = self.ag("Ada")
        self.assertEqual(row["status"], "failed")
        self.assertIn("process gone", row["last_line"])

    # A second Hive waits for its project's integration place in arrival order.
    def test_integration_reservation_is_per_project_and_serial(self):
        """A second Hive waits for its project's integration place in arrival order."""
        from haivequeen import hives
        self.assertTrue(hives.take_integration(self.conn, "testproj", "first"))
        self.assertFalse(hives.take_integration(self.conn, "testproj", "second"))
        self.assertTrue(hives.take_integration(self.conn, "other", "third"))
        self.assertFalse(hives.take_integration(self.conn, "testproj", "fourth"))
        hives.release_integration(self.conn, "testproj", "first")
        self.assertFalse(hives.take_integration(self.conn, "testproj", "fourth"))
        self.assertTrue(hives.take_integration(self.conn, "testproj", "second"))

    # Answering an open owner question wakes the waiting Hive.
    def test_answer_wakes_waiting_hive(self):
        """Answering an open owner question wakes the waiting Hive."""
        from haivequeen import db, hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        question = db.add_question(self.conn, "testproj", REL, "P1", "Which?", "A")
        hives.wait_for_answers(self.conn, "testproj", REL, [question])
        self.finish(first["name"], final="Waiting for owner answer")
        self.round()
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")])
        db.answer_question(self.conn, question, "A", "owner")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_path"], REL)

    # Dequeening ends only the Hive Queen and suppresses automatic restart.
    def test_dequeen_preserves_hive_until_requeen(self):
        """Dequeening ends only the Hive Queen and suppresses automatic restart."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        ended = []
        hives.dequeen(self.conn, "testproj", REL, stop=lambda pid: ended.append(pid))
        self.assertTrue(ended)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(active, [])
        hives.requeen(self.conn, "testproj", REL)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_path"], REL)

    # The owner can select the provider for the replacement Queen.
    def test_requeen_explicit_provider_changes_only_that_hive(self):
        """The owner can select the provider for the replacement Queen."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        original = active[0]
        other = "codex" if original["provider"] != "codex" else "claude"
        hives.requeen(self.conn, "testproj", REL, provider=other, stop=lambda pid: True)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], other)
        self.assertEqual(active[0]["plan_path"], REL)

    # Persisted Hive identity survives reconnecting to the database.
    def test_hive_colour_and_provider_survive_database_reopen(self):
        """Persisted Hive identity survives reconnecting to the database."""
        from haivequeen import db, hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        before = hives.get(self.conn, "testproj", REL)
        self.assertTrue(before["colour"])
        self.assertTrue(before["provider"])
        path = self.base / "data" / "haivequeen.db"
        self.conn.close()
        self.conn = db.connect(path)
        after = hives.get(self.conn, "testproj", REL)
        self.assertEqual(after["colour"], before["colour"])
        self.assertEqual(after["provider"], before["provider"])

    # A Hive waiting for its owner lets another READY plan use the place.
    def test_resting_hive_does_not_consume_hive_place(self):
        """A Hive waiting for its owner lets another READY plan use the place."""
        from haivequeen import db, hives
        rel1 = "docs/plans/one/ONE-PLAN.md"
        rel2 = "docs/plans/two/TWO-PLAN.md"
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=rel1)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        question = db.add_question(self.conn, "testproj", rel1, "P1", "Which?", "A")
        hives.wait_for_answers(self.conn, "testproj", rel1, [question])
        self.finish(active[0]["name"], final="Waiting for owner answer")
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=rel2)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_path"], rel2)

    # Explicit provider selection cannot bypass offered classes.
    def test_requeen_rejects_provider_without_queen_class(self):
        """Explicit provider selection cannot bypass offered classes."""
        from haivequeen import hives
        from bzztest import REL, provider
        self.machine["providers"] = [provider("claude", 1), provider("lightonly", 1, classes=("light",))]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        with self.assertRaises(ValueError):
            hives.requeen(self.conn, "testproj", REL, provider="lightonly", stop=lambda pid: self.fail("Queen must not be stopped"), providers=self.env().providers)
        self.assertEqual(self.ag(active[0]["name"])["status"], "running")

    # A Hive with an answer gets the next available place before a new plan.
    def test_answered_waiting_hive_has_priority_over_new_plan(self):
        """A Hive with an answer gets the next available place before a new plan."""
        from haivequeen import db, hives
        rel1 = "docs/plans/one/ONE-PLAN.md"
        rel2 = "docs/plans/two/TWO-PLAN.md"
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=rel1)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        question = db.add_question(self.conn, "testproj", rel1, "P1", "Which?", "A")
        hives.wait_for_answers(self.conn, "testproj", rel1, [question])
        self.finish(active[0]["name"], final="Waiting for owner answer")
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=rel2)
        db.answer_question(self.conn, question, "A", "owner")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_path"], rel1)

    # The first Queen sees unresolved questions from before the migration.
    def test_first_queen_receives_existing_open_questions(self):
        """The first Queen sees unresolved questions from before the migration."""
        from haivequeen import db
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        db.add_question(self.conn, "testproj", REL, "P1", "Which direction?", "North")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        prompt = (self.env().run_dir(active[0]["name"]) / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertIn("Which direction?", prompt)

    # An explicit requeen waits when the requested provider is full.
    def test_requeen_waits_for_free_provider_slot(self):
        """An explicit requeen waits when the requested provider is full."""
        from haivequeen import hives
        from bzztest import REL, provider
        self.machine["providers"] = [provider("claude", 1), provider("codex", 1, proto="codex")]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        target = "codex" if active[0]["provider"] != "codex" else "claude"
        self.agent("Busy", project="testproj", plan_path="other", phase="queen", status="running", provider=target, pid=42)
        hives.requeen(self.conn, "testproj", REL, provider=target, stop=lambda pid: True)
        self.round(alive=lambda pid: True)
        ours = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["plan_path"] == REL and a["status"] in ("running", "starting")]
        self.assertEqual(ours, [])

    # Three old unfinished work runs do not block a Hive or ask the owner.
    def test_old_work_streak_does_not_stop_hive(self):
        """Three old unfinished work runs do not block a Hive or ask the owner."""
        from bzztest import REL
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        for n in range(3):
            self.agent(f"old-{n}", phase="work", package="P1", status="done", started=90 - n * 20, ended=80 - n * 20)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["phase"], "queen")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", status="open", plan=REL), [])

    # A machine exclusion is respected even if that provider has free slots.
    def test_excluded_provider_is_not_used_for_queen(self):
        """A machine exclusion is respected even if that provider has free slots."""
        from bzztest import provider
        self.machine["providers"] = [provider("first", 1), provider("second", 1)]
        self.machine["excludedProviders"] = ["first"]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "second")

    # The handoff waits for the two running Bees and forbids another Bee.
    def test_context_limit_blocks_new_bees_until_running_bees_finish(self):
        """The handoff waits for the two running Bees and forbids another Bee."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        queen = active[0]
        self.agent("Bee1", plan_path=REL, phase="bee", status="running", provider=queen["provider"], pid=42)
        self.agent("Bee2", plan_path=REL, phase="bee", status="running", provider=queen["provider"], pid=43)
        hives.context_limit(self.conn, "testproj", REL)
        self.assertFalse(hives.can_start_bee(self.conn, "testproj", REL))
        self.assertFalse(hives.can_handoff(self.conn, "testproj", REL))
        self.finish("Bee1")
        self.finish("Bee2")
        self.assertTrue(hives.can_handoff(self.conn, "testproj", REL))

    # The one-time migration waits for an explicit first resume.
    def test_migration_does_not_run_while_paused(self):
        """The one-time migration waits for an explicit first resume."""
        from haivequeen import runstate
        self.add_project("hq")
        rel = "docs/plans/OLD-ONE/OLD-ONE-PLAN.md"
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}], project="hq", rel=rel)
        self.agent("old-work", project="hq", plan_path=rel, phase="work", status="done")
        runstate.pause(self.conn)
        self.round()
        self.assertEqual(runtime.list_plans(self.conn, "hq")[0]["status"], "IN_PROGRESS")

    # An unfinished normal end starts another Queen on the same Hive.
    def test_unfinished_queen_restarts_without_handoff(self):
        """An unfinished normal end starts another Queen on the same Hive."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        self.finish(first["name"], final="unfinished")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], first["provider"])
        self.assertEqual(active[0]["plan_path"], first["plan_path"])

    # Two active Hives get different persistent colours.
    def test_running_hives_have_distinct_colours(self):
        """Two active Hives get different persistent colours."""
        from haivequeen import hives
        rel1 = "docs/plans/one/ONE-PLAN.md"
        rel2 = "docs/plans/two/TWO-PLAN.md"
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=rel1)
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=rel2)
        self.round()
        first = hives.get(self.conn, "testproj", rel1)
        second = hives.get(self.conn, "testproj", rel2)
        self.assertTrue(first["colour"])
        self.assertTrue(second["colour"])
        self.assertNotEqual(first["colour"], second["colour"])

    # With one available place, the project with higher priority starts first.
    def test_hive_start_respects_project_priority(self):
        """With one available place, the project with higher priority starts first."""
        self.machine["hives"] = 1
        self.add_project("high", priority=10)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.plan("READY", [{"id": "P2", "title": "Second"}], project="high")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, None, "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["project"], "high")

    # Preference never blocks a Hive when another provider has capacity.
    def test_preferred_provider_busy_uses_another_available_provider(self):
        """Preference never blocks a Hive when another provider has capacity."""
        from bzztest import provider
        entries = [provider("a", 1), provider("b", 1)]
        providers = scheduler.get_providers({"providers": entries})
        picked = scheduler.select_provider(providers, "medium", [], {"a": 0, "b": 1}, prefer="a", limits={})
        self.assertEqual(picked["name"], "b")

    # Syncing a legacy header does not retain its provider hint.
    def test_old_plan_agent_does_not_reach_runtime_package(self):
        """Syncing a legacy header does not retain its provider hint."""
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First", "agent": "codex"}])
        packages = runtime.list_packages(self.conn, "testproj", REL)
        self.assertEqual(len(packages), 1)
        self.assertFalse(packages[0].get("agent_type"))

    # A process Bee uses a slot in addition to its Queen.
    def test_queen_and_process_bee_consume_separate_slots(self):
        """A process Bee uses a slot in addition to its Queen."""
        from bzztest import provider
        providers = scheduler.get_providers({"providers": [provider("claude", 3), provider("codex", 1, proto="codex")]})
        rows = [{"status": "running", "phase": "queen", "provider": "claude", "pid": 11},
                {"status": "running", "phase": "bee", "provider": "codex", "pid": 12}]
        free = scheduler.provider_free(providers, rows)
        self.assertEqual(free["claude"], 2)
        self.assertEqual(free["codex"], 0)

    # A resting Hive retains its provider and waits until the reset time.
    def test_limit_reset_wakes_only_when_reset_is_due(self):
        """A resting Hive retains its provider and waits until the reset time."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        current = hives.get(self.conn, "testproj", REL)
        hives.rest_until(self.conn, "testproj", REL, "2026-01-01T12:00:00")
        hives.wake_due(self.conn, "2026-01-01T11:59:59")
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "resting")
        hives.wake_due(self.conn, "2026-01-01T12:00:00")
        resumed = hives.get(self.conn, "testproj", REL)
        self.assertNotEqual(resumed["state"], "resting")
        self.assertEqual(resumed["provider"], current["provider"])

    # A resting Hive cannot hold the project integration place.
    def test_resting_hive_releases_integration_place(self):
        """A resting Hive cannot hold the project integration place."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        self.assertTrue(hives.take_integration(self.conn, "testproj", REL))
        hives.rest_until(self.conn, "testproj", REL, "2099-01-01T00:00:00")
        self.assertTrue(hives.take_integration(self.conn, "testproj", "another"))

    # Old work history does not start verify, release, triage or brain agents.
    def test_no_removed_agent_roles_start(self):
        """Old work history does not start verify, release, triage or brain agents."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        self.agent("old-work", package="P1", phase="work", status="done", started=30, ended=20)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertTrue(active)
        self.assertTrue(all(a["phase"] == "queen" for a in active))
        self.assertEqual(self.calls(), 0)

    # The draft stays DRAFT and the migration is only performed once.
    def test_migration_keeps_draft_and_does_not_repeat(self):
        """The draft stays DRAFT and the migration is only performed once."""
        from haivequeen import runstate
        self.add_project("hq")
        rel = "docs/plans/DRAFT-ONLY/DRAFT-ONLY-PLAN.md"
        self.plan("DRAFT", [{"id": "P1", "title": "First"}], project="hq", rel=rel)
        old = "docs/plans/OLD-ONE/OLD-ONE-PLAN.md"
        self.plan("IN_PROGRESS", [{"id": "P2", "title": "Second"}], project="hq", rel=old)
        self.agent("old-work", project="hq", plan_path=old, phase="work", status="done")
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        statuses = {p["path"]: p["status"] for p in runtime.list_plans(self.conn, "hq")}
        self.assertEqual(statuses[rel], "DRAFT")
        self.assertEqual(statuses[old], "PAUSED")
        runtime.set_plan_status(self.conn, "hq", old, "IN_PROGRESS")
        self.round()
        statuses = {p["path"]: p["status"] for p in runtime.list_plans(self.conn, "hq")}
        self.assertEqual(statuses[rel], "DRAFT")
        self.assertEqual(statuses[old], "IN_PROGRESS")

    # Repeated old verification records do not lock the Hive.
    def test_old_verification_streak_does_not_stop_hive(self):
        """Repeated old verification records do not lock the Hive."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        for n in range(3):
            name = f"old-verify-{n}"
            self.agent(name, phase="verify", package="P1", status="done", started=90 - n * 20, ended=80 - n * 20)
            self.finish(name, final="Offene Anforderungen: 3")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["phase"], "queen")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", status="open"), [])

    # The Queen retry waits for her provider instead of switching automatically.
    def test_rate_limit_waits_for_queen_provider_even_if_other_free(self):
        """The Queen retry waits for her provider instead of switching automatically."""
        import json
        import os
        from datetime import datetime
        from haivequeen import routine
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        limits = self.base / "limits.json"
        limits.write_text(json.dumps({"claude": {"usedPercent": 100, "resetHours": 4}, "codex": {"usedPercent": 0, "resetHours": 10}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(limits)
        self.agent("Ada", phase="queen", status="failed", provider="claude", interrupted="rate_limit", last_line="rate limit")
        env = self.env()
        agents = routine.all_agents(env)
        queen = next(a for a in agents if a["name"] == "Ada")
        self.assertFalse(routine.retry_due(env, agents, queen, now=datetime.now()))
        self.assertTrue(self.ag("Ada").get("retryAt"))

    # A resting Hive and its reset time survive reconnecting.
    def test_resting_state_survives_database_reopen(self):
        """A resting Hive and its reset time survive reconnecting."""
        from haivequeen import db, hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        hives.rest_until(self.conn, "testproj", REL, "2099-01-01T00:00:00")
        before = hives.get(self.conn, "testproj", REL)
        self.conn.close()
        self.conn = db.connect(self.base / "data" / "haivequeen.db")
        after = hives.get(self.conn, "testproj", REL)
        self.assertEqual(after["state"], "resting")
        self.assertEqual(after["reset_at"], before["reset_at"])

    # Dequeen ends its own process Bees without touching another Hive.
    def test_dequeen_stops_own_process_bees_only(self):
        """Dequeen ends its own process Bees without touching another Hive."""
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.agent("Bee1", plan_path=REL, phase="bee", status="running", pid=401)
        self.agent("Other", plan_path="other-plan", phase="queen", status="running", pid=402)
        stopped = []
        hives.dequeen(self.conn, "testproj", REL, stop=lambda pid: stopped.append(pid) or True)
        self.assertIn(401, stopped)
        self.assertNotIn(402, stopped)
        self.assertEqual(self.ag("Other")["status"], "running")

    # Equal-priority plans use their waiting order.
    def test_oldest_waiting_plan_starts_first_with_equal_priority(self):
        """Equal-priority plans use their waiting order."""
        first = "docs/plans/one/ONE-PLAN.md"
        second = "docs/plans/two/TWO-PLAN.md"
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=first)
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=second)
        runtime.kv_set(self.conn, "testproj", "wait:" + first + "|P1", "2026-01-02T00:00:00")
        runtime.kv_set(self.conn, "testproj", "wait:" + second + "|P2", "2026-01-01T00:00:00")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["plan_path"], second)

    # An explicit requeen bypasses the machine Hive count when a slot is free.
    def test_owner_requeen_may_exceed_hive_limit(self):
        """An explicit requeen bypasses the machine Hive count when a slot is free."""
        from haivequeen import hives
        from bzztest import REL
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")][0]
        hives.dequeen(self.conn, "testproj", REL, stop=lambda pid: True)
        second = "docs/plans/two/TWO-PLAN.md"
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=second)
        self.round()
        hives.requeen(self.conn, "testproj", REL)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual({a["plan_path"] for a in active}, {REL, second})

    # Existing otherproject plans stay eligible for their own Hives after migration.
    def test_migration_leaves_other_project_plans_active(self):
        """Existing otherproject plans stay eligible for their own Hives after migration."""
        from haivequeen import runstate
        self.add_project("otherproject")
        names = ("FIRST", "SECOND", "THIRD", "FOURTH")
        for name in names:
            self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}], project="otherproject", rel=f"docs/plans/{name}/{name}-PLAN.md")
        runstate.pause(self.conn)
        runstate.resume(self.conn)
        self.round()
        rows = runtime.list_plans(self.conn, "otherproject")
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["status"] == "IN_PROGRESS" for row in rows))

    # Three in-session Bees do not use process slots in addition to their Queen.
    def test_embedded_subagents_do_not_take_provider_slots(self):
        """Three in-session Bees do not use process slots in addition to their Queen."""
        from bzztest import provider
        providers = scheduler.get_providers({"providers": [provider("claude", 4)]})
        rows = [{"status": "running", "phase": "queen", "provider": "claude", "pid": 11,
                 "embedded_bees": ["Bee1", "Bee2", "Bee3"]}]
        self.assertEqual(scheduler.provider_free(providers, rows)["claude"], 3)

    # A full five-hour window rests the Hive even if the weekly window has room.
    def test_five_hour_limit_also_rests_queen(self):
        """A full five-hour window rests the Hive even if the weekly window has room."""
        import json
        import os
        from datetime import datetime
        from haivequeen import routine
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        limits = self.base / "limits.json"
        limits.write_text(json.dumps({"claude": {"usedPercent": 20, "resetHours": 100,
            "windows": [{"usedPercent": 100, "resetHours": 2}, {"usedPercent": 20, "resetHours": 100}]},
            "codex": {"usedPercent": 0, "resetHours": 10}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(limits)
        self.agent("Ada", phase="queen", status="failed", provider="claude", interrupted="rate_limit", last_line="rate limit")
        env = self.env()
        agents = routine.all_agents(env)
        queen = next(a for a in agents if a["name"] == "Ada")
        self.assertFalse(routine.retry_due(env, agents, queen, now=datetime.now()))

    # A vanished Queen does not increment a package failure count.
    def test_interrupted_queen_is_not_a_package_failure(self):
        """A vanished Queen does not increment a package failure count."""
        from haivequeen import routine
        from bzztest import REL
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        for n in range(3):
            self.agent(f"queen-{n}", phase="queen", package="P1", status="failed", interrupted="vanished", last_line="process gone")
        env = self.env()
        self.assertEqual(routine.package_failures(env, routine.all_agents(env), REL, "P1"), 0)

    # Owner dequeening survives a Beekeeper restart.
    def test_queenless_state_survives_database_reopen(self):
        """Owner dequeening survives a Beekeeper restart."""
        from haivequeen import db, hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        hives.dequeen(self.conn, "testproj", REL, stop=lambda pid: True)
        self.conn.close()
        self.conn = db.connect(self.base / "data" / "haivequeen.db")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(active, [])
        self.assertEqual(hives.get(self.conn, "testproj", REL)["state"], "queenless")

    # Old elapsed-time stop rules do not create owner questions.
    def test_six_hours_without_integration_does_not_stop_hive(self):
        """Old elapsed-time stop rules do not create owner questions."""
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        self.agent("Old", phase="work", package="P1", status="done", started=420, ended=410)
        self.agent("Ada", phase="queen", package="P1", status="running", started=10, pid=1234)
        self.round(alive=lambda pid: True)
        self.assertEqual(self.ag("Ada")["status"], "running")
        self.assertEqual(runtime.list_questions(self.conn, "testproj", status="open"), [])

    # The owner can requeen a resting Hive on another configured provider.
    def test_owner_requeen_moves_rate_limited_hive_to_chosen_provider(self):
        """The owner can requeen a resting Hive on another configured provider."""
        import json
        import os
        from haivequeen import hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        first = active[0]
        other = "codex" if first["provider"] != "codex" else "claude"
        limits = self.base / "limits.json"
        limits.write_text(json.dumps({first["provider"]: {"usedPercent": 100, "resetHours": 48}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(limits)
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "interrupted": "rate_limit", "last_line": "rate limit"})
        self.round()
        hives.requeen(self.conn, "testproj", REL, provider=other)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], other)

    # The integration command queues the second Queen until the first releases.
    def test_integration_command_serializes_two_real_queen_requests(self):
        """The integration command queues the second Queen until the first releases."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli, hives, loop
        rel1 = "docs/plans/one/ONE-PLAN.md"
        rel2 = "docs/plans/two/TWO-PLAN.md"
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=rel1)
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=rel2)
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] in ("running", "starting")]
        self.assertEqual(len(active), 2)
        queens = {a["plan_path"]: a["name"] for a in active}
        saved = os.environ.get("BZZ_AGENT")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                os.environ["BZZ_AGENT"] = queens[rel1]
                a = argparse.Namespace(action="acquire", project="testproj", plan=rel1)
                self.assertEqual(cli.cmd_integration(a, self.conn), 0)
                os.environ["BZZ_AGENT"] = queens[rel2]
                a = argparse.Namespace(action="acquire", project="testproj", plan=rel2)
                self.assertEqual(cli.cmd_integration(a, self.conn), 1)
                self.assertEqual(hives.get(self.conn, "testproj", rel2)["state"], "integration_wait")
                hives.release_integration(self.conn, "testproj", rel1)
                self.assertEqual(cli.cmd_integration(a, self.conn), 0)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # At the context limit the round waits for Bees before restarting the Queen.
    def test_round_context_handoff_waits_for_real_bee_rows(self):
        """At the context limit the round waits for Bees before restarting the Queen."""
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        first = queens[0]
        self.agent("Bee1", plan_path=REL, phase="bee", status="running", provider=first["provider"], pid=401)
        self.agent("Bee2", plan_path=REL, phase="bee", status="running", provider=first["provider"], pid=402)
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"ctxTokens": 300000})
        self.round(alive=lambda pid: True)
        self.assertTrue((self.env().run_dir(first["name"]) / "HANDOFF").exists())
        self.finish(first["name"], final="Handoff")
        self.round(alive=lambda pid: True)
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(active, [])
        self.finish("Bee1")
        self.finish("Bee2")
        self.round(alive=lambda pid: True)
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], first["provider"])

    # The real requeen command starts a replacement at once when unpaused.
    def test_requeen_cli_starts_replacement_without_waiting_for_round(self):
        """The real requeen command starts a replacement at once when unpaused."""
        import argparse
        import contextlib
        import io
        from unittest.mock import patch
        from haivequeen import cli, hives, loop
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        original = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(original), 1)
        with patch.object(cli.config, "load_config", return_value=self.machine), patch.object(hives.osplat, "kill_process_tree", return_value=True), patch.object(loop, "ram_gb", return_value=16), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_requeen(argparse.Namespace(hive=REL, provider=None), self.conn), 0)
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertNotEqual(active[0]["name"], original[0]["name"])

    # Questions created by Queens reach the owner without a triage agent.
    def test_ask_command_opens_owner_question_without_triage(self):
        """Questions created by Queens reach the owner without a triage agent."""
        import argparse
        import contextlib
        import io
        from haivequeen import cli
        from bzztest import REL
        args = argparse.Namespace(project="testproj", plan=REL, item="P1", question="Which?", recommendation="A")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_ask(args, self.conn), 0)
        questions = runtime.list_questions(self.conn, "testproj", status="open")
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], "Which?")

    # A Queen report makes the Hive wait until its existing question is answered.
    def test_owner_wait_is_recorded_from_actual_queen_report(self):
        """A Queen report makes the Hive wait until its existing question is answered."""
        from haivequeen import db
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        question = db.add_question(self.conn, "testproj", REL, "P1", "Which?", "A")
        self.finish(first["name"], final="WAITING_FOR_OWNER")
        self.round()
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"])
        db.answer_question(self.conn, question, "A", "owner")
        self.round()
        self.assertEqual(len([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]), 1)

    # The local LLM is used for Bees, while a Hive Queen uses Claude or Codex.
    def test_local_llm_is_not_selected_as_hive_queen(self):
        """The local LLM is used for Bees, while a Hive Queen uses Claude or Codex."""
        from bzztest import provider
        local = provider("local", 1, proto="codex")
        local["endpoint"] = {"baseUrl": "http://127.0.0.1:1234/v1"}
        self.machine["providers"] = [local, provider("claude", 1)]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "claude")

    # A provider with a full short window is not chosen for a new Hive.
    def test_full_five_hour_window_excludes_queen_provider(self):
        """A provider with a full short window is not chosen for a new Hive."""
        import json
        import os
        from bzztest import provider
        self.machine["providers"] = [provider("a", 1), provider("b", 1)]
        limits = self.base / "limits.json"
        limits.write_text(json.dumps({"a": {"usedPercent": 0, "resetHours": 100,
            "windows": [{"usedPercent": 100, "resetHours": 2}, {"usedPercent": 0, "resetHours": 100}]},
            "b": {"usedPercent": 0, "resetHours": 200}}), encoding="utf-8")
        os.environ["BZZ_PROVIDER_LIMITS_FILE"] = str(limits)
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "b")

    # The old capability-only routing does not disqualify a Codex Queen.
    def test_hive_may_choose_codex_even_when_old_use_was_capability_only(self):
        """The old capability-only routing does not disqualify a Codex Queen."""
        from bzztest import provider
        self.machine["providers"] = [provider("codex", 1, use="capability-only", proto="codex")]
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "codex")

    # Old machine and project entries cannot restore removed stop mechanisms.
    def test_removed_settings_are_ignored_even_in_existing_config(self):
        """Old machine and project entries cannot restore removed stop mechanisms."""
        old = {"progressVerifies": 3, "planStallHours": 6, "minClass": {"verify": "heavy"},
               "bundle": {"maxPackages": 2}, "triage": True, "brain": {"command": "old"}, "integratorSlots": 3}
        settings = scheduler.build_settings(old, old)
        for key in old:
            with self.subTest(key=key):
                self.assertNotIn(key, settings)

    # Historical queue rows do not mark an unrelated new Hive done.
    def test_old_integrated_row_does_not_finish_new_hive(self):
        """Historical queue rows do not mark an unrelated new Hive done."""
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        runtime.queue_upsert(self.conn, "testproj", "older-branch", tip="abc", plan=REL, status="integrated")
        self.round()
        self.round()
        self.assertEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status, "IN_PROGRESS")

    # Only integration of the Queen's registered row completes her plan.
    def test_own_integrated_ready_row_finishes_plan(self):
        """Only integration of the Queen's registered row completes her plan."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        saved = os.environ.get("BZZ_AGENT")
        try:
            os.environ["BZZ_AGENT"] = queen["name"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_ready(argparse.Namespace(project="testproj", branch="branch", tip="abc", plan=REL, note=""), self.conn), 0)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved
        runtime.queue_upsert(self.conn, "testproj", "branch", status="integrated")
        self.finish(queen["name"], final="Integrated")
        self.round()
        self.assertEqual(plans.parse_plan_text((self.repo / REL).read_text(encoding="utf-8")).status, "DONE")

    # Queen startup works with existing project work-phase rule selections.
    def test_queen_runner_gets_existing_work_phase_rules(self):
        """Queen startup works with existing project work-phase rule selections."""
        from haivequeen import agent_runner
        source = self.base / "rules.md"
        source.write_text("# Work\nDo work.\n# Integrate\nDo integration.\n", encoding="utf-8")
        config = {"ruleSources": {"project": "rules.md"}, "phaseRules": {"work": {"project": ["# Work"]}}}
        output = self.base / "out.md"
        agent_runner.write_phase_rules(config, self.base, "queen", output)
        self.assertIn("Do work.", output.read_text(encoding="utf-8"))

    # The launched Queen is asked to work the plan rather than one old phase.
    def test_real_queen_prompt_does_not_end_after_one_package(self):
        """The launched Queen is asked to work the plan rather than one old phase."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        prompt = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertIn("Queen of this Hive", prompt)
        self.assertNotIn("one task, then you end", prompt)

    # A connection interruption waits before starting the next Queen.
    def test_connection_interruption_obeys_existing_retry_delay(self):
        """A connection interruption waits before starting the next Queen."""
        from haivequeen import db
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "interrupted": "connection", "ended_at": db.now(), "last_line": "connection lost"})
        self.round()
        self.assertFalse([a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"])

    # Existing project views are still refreshed by a Hive round.
    def test_hive_round_updates_existing_project_views(self):
        """Existing project views are still refreshed by a Hive round."""
        from unittest.mock import patch
        from haivequeen import views
        self.plan("READY", [{"id": "P1", "title": "First"}])
        with patch.object(views, "write_views", wraps=views.write_views) as update:
            self.round()
        self.assertTrue(update.called)

    # A Queen continues an existing plan in its known worktree.
    def test_migrated_hive_reuses_existing_plan_worktree(self):
        """A Queen continues an existing plan in its known worktree."""
        from bzztest import REL
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}])
        self.agent("Old", plan_path=REL, phase="work", package="P1", cwd=str(self.wt), status="done", started=30, ended=20)
        self.round()
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)
        self.assertEqual(queens[0]["cwd"], str(self.wt))

    # A manually started branch and a Queen cannot integrate simultaneously.
    def test_external_and_queen_share_project_integration_place(self):
        """A manually started branch and a Queen cannot integrate simultaneously."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        saved = os.environ.pop("BZZ_AGENT", None)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                external = argparse.Namespace(action="acquire", project="testproj", plan="outside-plan", owner_pid=os.getpid())
                self.assertEqual(cli.cmd_integration(external, self.conn), 0)
                os.environ["BZZ_AGENT"] = queen["name"]
                own = argparse.Namespace(action="acquire", project="testproj", plan=REL)
                self.assertEqual(cli.cmd_integration(own, self.conn), 1)
                os.environ.pop("BZZ_AGENT", None)
                external.action = "release"
                self.assertEqual(cli.cmd_integration(external, self.conn), 0)
                os.environ["BZZ_AGENT"] = queen["name"]
                self.assertEqual(cli.cmd_integration(own, self.conn), 0)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # Registering a branch alone does not block another running integration.
    def test_ready_registration_does_not_take_integration_place(self):
        """Registering a branch alone does not block another running integration."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli, hives
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        saved = os.environ.get("BZZ_AGENT")
        try:
            os.environ["BZZ_AGENT"] = queen["name"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_ready(argparse.Namespace(project="testproj", branch="branch", tip="abc", plan=REL, note=""), self.conn), 0)
            self.assertTrue(hives.take_integration(self.conn, "testproj", "outside-plan"))
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # A vanished external owner cannot hold the integration place indefinitely.
    def test_dead_external_integrator_releases_place_on_round(self):
        """A vanished external owner cannot hold the integration place indefinitely."""
        import argparse
        import contextlib
        import io
        from haivequeen import cli
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan="outside", owner_pid=987654), self.conn), 0)
        self.round(alive=lambda pid: False)
        from haivequeen import hives
        self.assertTrue(hives.take_integration(self.conn, "testproj", "next"))

    # The waiting Hive retains its queue place after its Queen ends.
    def test_waiting_queen_keeps_integration_order_after_handoff(self):
        """The waiting Hive retains its queue place after its Queen ends."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli
        rel1 = "docs/plans/one/ONE-PLAN.md"
        rel2 = "docs/plans/two/TWO-PLAN.md"
        self.plan("READY", [{"id": "P1", "title": "First"}], rel=rel1)
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=rel2)
        self.round()
        queens = {a["plan_path"]: a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"}
        runtime.upsert_agent(self.conn, "testproj", queens[rel2]["name"], {"pid": 87654})
        saved = os.environ.get("BZZ_AGENT")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                os.environ["BZZ_AGENT"] = queens[rel1]["name"]
                self.assertEqual(cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan=rel1), self.conn), 0)
                os.environ["BZZ_AGENT"] = queens[rel2]["name"]
                self.assertEqual(cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan=rel2), self.conn), 1)
            waiting_id = self.conn.execute("SELECT id FROM hive_integration WHERE project=? AND plan_path=?", ("testproj", rel2)).fetchone()[0]
            self.finish(queens[rel2]["name"], final="waiting for integration")
            self.round(alive=lambda pid: pid == queens[rel1]["pid"])
            self.assertEqual(self.conn.execute("SELECT id FROM hive_integration WHERE project=? AND plan_path=?", ("testproj", rel2)).fetchone()[0], waiting_id)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # A paused Beekeeper blocks new Bee processes but not in-session subagents.
    def test_pause_blocks_only_new_bee_processes(self):
        """A paused Beekeeper blocks new Bee processes but not in-session subagents."""
        from haivequeen import hives, runstate
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        runstate.pause(self.conn)
        self.assertFalse(hives.can_start_bee(self.conn, "testproj", REL, own_process=True))
        self.assertTrue(hives.can_start_bee(self.conn, "testproj", REL, own_process=False))

    # Migration does not run merely because a new database defaults to running.
    def test_migration_requires_an_explicit_resume(self):
        """Migration does not run merely because a new database defaults to running."""
        self.add_project("hq")
        rel = "docs/plans/OLD-ONE/OLD-ONE-PLAN.md"
        self.plan("IN_PROGRESS", [{"id": "P1", "title": "First"}], project="hq", rel=rel)
        self.agent("old-work", project="hq", plan_path=rel, phase="work", status="done")
        self.round()
        self.assertEqual(runtime.list_plans(self.conn, "hq")[0]["status"], "IN_PROGRESS")

    # The actual Queen prompt forbids new Bees and waits for running Bees at handoff.
    def test_queen_handoff_prompt_waits_for_bees(self):
        """The actual Queen prompt forbids new Bees and waits for running Bees at handoff."""
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        prompt = (self.env().run_dir(queen["name"]) / "prompt.md").read_text(encoding="utf-8-sig")
        self.assertIn("do not start new Bees", prompt)
        self.assertIn("wait for running Bees", prompt)

    # A waiting Hive retains its queue position without holding a free place.
    def test_waiting_integration_does_not_reserve_without_free_queen_slot(self):
        """A waiting Hive retains its queue position without holding a free place."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli, hives
        from bzztest import REL
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        hives.take_integration(self.conn, "testproj", "external")
        saved = os.environ.get("BZZ_AGENT")
        try:
            os.environ["BZZ_AGENT"] = queen["name"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan=REL), self.conn), 1)
            self.finish(queen["name"], final="waiting for integration")
            hives.release_integration(self.conn, "testproj", "external")
            self.agent("Busy", plan_path="other", phase="queen", status="running", pid=42)
            self.round(alive=lambda pid: True)
            row = self.conn.execute("SELECT acquired FROM hive_integration WHERE project=? AND plan_path=?", ("testproj", REL)).fetchone()
            self.assertEqual(row[0], 0)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # External integration cannot silently bind its lock to a transient shell.
    def test_external_integration_requires_stable_owner_process(self):
        """External integration cannot silently bind its lock to a transient shell."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli
        saved = os.environ.pop("BZZ_AGENT", None)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                result = cli.cmd_integration(argparse.Namespace(action="acquire", project="testproj", plan="outside", owner_pid=None), self.conn)
            self.assertEqual(result, 1)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hive_integration").fetchone()[0], 0)
        finally:
            if saved is not None:
                os.environ["BZZ_AGENT"] = saved

    # A Queen may register the absolute plan path supplied in her prompt.
    def test_queen_ready_accepts_absolute_plan_path(self):
        """A Queen may register the absolute plan path supplied in her prompt."""
        import argparse
        import contextlib
        import io
        import os
        from haivequeen import cli
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        queen = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        saved = os.environ.get("BZZ_AGENT")
        try:
            os.environ["BZZ_AGENT"] = queen["name"]
            with contextlib.redirect_stdout(io.StringIO()):
                result = cli.cmd_ready(argparse.Namespace(project="testproj", branch="branch", tip="abc", plan=str(self.repo / REL), note=""), self.conn)
            self.assertEqual(result, 0)
        finally:
            if saved is None:
                os.environ.pop("BZZ_AGENT", None)
            else:
                os.environ["BZZ_AGENT"] = saved

    # A retry following a connection error still writes the project views.
    def test_retry_round_still_refreshes_views(self):
        """A retry following a connection error still writes the project views."""
        from unittest.mock import patch
        from haivequeen import views
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "failed", "pid": None, "interrupted": "connection", "retryAt": "2000-01-01T00:00:00"})
        with patch.object(views, "write_views", wraps=views.write_views) as update:
            self.round()
        self.assertTrue(update.called)

    # A new Hive cannot take the colour of one resting until a reset.
    def test_resting_hive_colour_stays_reserved(self):
        """A new Hive cannot take the colour of one resting until a reset."""
        from haivequeen import hives
        from bzztest import REL
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        colour = hives.get(self.conn, "testproj", REL)["colour"]
        hives.rest_until(self.conn, "testproj", REL, "2099-01-01T00:00:00")
        self.finish(first["name"], final="waiting")
        other = "docs/plans/other/OTHER-PLAN.md"
        self.plan("READY", [{"id": "P2", "title": "Second"}], rel=other)
        self.round()
        self.assertNotEqual(hives.get(self.conn, "testproj", other)["colour"], colour)

    # A Queen runner still alive after its done report prevents a second Queen.
    def test_living_runner_is_not_replaced_after_done_report(self):
        """A Queen runner still alive after its done report prevents a second Queen."""
        from bzztest import REL
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        runtime.upsert_agent(self.conn, "testproj", first["name"], {"status": "done", "pid": 98765})
        self.round(alive=lambda pid: pid == 98765)
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 1)

    # A ready continuation wins a free machine place before a new high-priority plan.
    def test_answered_hive_precedes_new_plan_in_other_project(self):
        """A ready continuation wins a free machine place before a new high-priority plan."""
        from haivequeen import db, hives
        from bzztest import REL
        self.machine["hives"] = 1
        self.plan("READY", [{"id": "P1", "title": "First"}])
        self.round()
        first = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["status"] == "running" and a["phase"] == "queen"][0]
        question = db.add_question(self.conn, "testproj", REL, "P1", "Which?", "A")
        hives.wait_for_answers(self.conn, "testproj", REL, [question])
        self.finish(first["name"], final="WAITING_FOR_OWNER")
        self.add_project("higher", priority=10)
        self.plan("READY", [{"id": "P2", "title": "Second"}], project="higher")
        db.answer_question(self.conn, question, "A", "owner")
        self.round()
        active = [a for a in runtime.list_agents(self.conn, None, "all") if a["status"] == "running" and a["phase"] == "queen"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["project"], "testproj")

    # A completed Queen wakes the real Beekeeper loop without waiting for its timer.
    def test_beekeeper_restarts_handoff_without_waiting_for_periodic_round(self):
        """The loop detects a Queen ending and immediately begins the next round."""
        from haivequeen import loop
        self.plan("READY", [{"id": "P1", "title": "First"}])
        calls = []
        # End the currently simulated Queen at each loop wait.
        def sleeper(seconds):
            """End the currently simulated Queen at each loop wait."""
            calls.append(seconds)
            self.assertLess(len(calls), 4, "the loop did not wake after the Queen ended")
            for row in runtime.list_agents(self.conn, "testproj", "active"):
                if row["phase"] == "queen" and row["status"] in ("running", "starting"):
                    self.finish(row["name"], final="Handoff")
        loop.run_loop(self.conn, self.machine, rounds=2, round_minutes=10, sleep=sleeper,
                      round_kwargs={"free_ram": lambda: 16.0, "settle": 0})
        queens = [a for a in runtime.list_agents(self.conn, "testproj", "all") if a["phase"] == "queen"]
        self.assertEqual(len(queens), 2)
