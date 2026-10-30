"""Port of test-providers.ps1: provider selection by weekly limit, capabilities, slots 0, forwarding, capability-only, fallback, dummy provider, rounds."""
import unittest

from bzztest import RoundCase, provider, runtime
from haivequeen import scheduler as sch


def settings(*providers):
    """Minimal settings with the given provider entries."""
    return {"providers": list(providers)}


class SelectTest(unittest.TestCase):
    """Unit tests of the selection with invented limit values (the limits argument replaces every real source)."""

    def pick(self, provs, cls="medium", needs=(), free=None, limits=None, avoid="", prefer=""):
        """Name of the provider that gets a task, or 'none'."""
        ps = sch.get_providers(settings(*provs))
        p = sch.select_provider(ps, cls, list(needs), free, avoid=avoid, prefer=prefer, limits=limits if limits is not None else {})
        return p["name"] if p else "none"

    def test_weekly_limit(self):
        """The higher (100 - used) / hours to reset wins; equal values: list order."""
        ps = [provider("A", 2, limit="x"), provider("B", 2, limit="x")]
        free = {"A": 2, "B": 2}
        self.assertEqual(self.pick(ps, free=free, limits={"A": {"usedPercent": 80, "resetHours": 24}, "B": {"usedPercent": 20, "resetHours": 48}}), "B")
        self.assertEqual(self.pick(ps, free=free, limits={"A": {"usedPercent": 10, "resetHours": 24}, "B": {"usedPercent": 20, "resetHours": 48}}), "A")
        self.assertEqual(self.pick(ps, free=free, limits={"A": {"usedPercent": 50, "resetHours": 100}, "B": {"usedPercent": 50, "resetHours": 10}}), "B")
        self.assertEqual(self.pick([provider("A", 1), provider("B", 1)], free={"A": 1, "B": 1}), "A")

    def test_capability(self):
        """A needed capability goes to the provider with it, also at a full limit; without a need a provider at its limit drops out."""
        ps = [provider("A", 2, limit="x"), provider("G", 2, caps=("images",), limit="x")]
        free = {"A": 2, "G": 2}
        lim = {"A": {"usedPercent": 0, "resetHours": 10}, "G": {"usedPercent": 100, "resetHours": 10}}
        self.assertEqual(self.pick(ps, free=free, limits=lim), "A")
        self.assertEqual(self.pick(ps, needs=["images"], free=free, limits=lim), "G")
        self.assertEqual(self.pick(ps, needs=["voice"], free=free, limits=lim), "none")

    def test_no_limit_counts_full(self):
        """A provider without a weekly limit counts as 100 % free."""
        ps = [provider("A", 2, limit="x"), provider("L", 1)]
        free = {"A": 2, "L": 1}
        self.assertEqual(self.pick(ps, free=free, limits={"A": {"usedPercent": 95, "resetHours": 10}}), "L")
        self.assertEqual(self.pick(ps, free=free, limits={"A": {"usedPercent": 10, "resetHours": 100}}), "A")
        lim = sch.provider_limit(sch.get_providers(settings(provider("L", 1)))[0], None, {})
        self.assertEqual(lim["FreePct"], 100)

    def test_slots_zero(self):
        """Slots 0 = off for everything, also for capabilities."""
        ps = sch.get_providers(settings(provider("A", 0), provider("G", 0, caps=("images",)), provider("B", 1)))
        self.assertEqual(self.pick([provider("A", 0), provider("G", 0, caps=("images",)), provider("B", 1)], free={"A": 0, "G": 0, "B": 1}), "B")
        self.assertEqual(self.pick([provider("A", 0), provider("G", 0, caps=("images",)), provider("B", 1)], needs=["images"], free={"A": 0, "G": 5, "B": 1}), "none")
        self.assertFalse(sch.capability_available(ps, ["images"]))
        self.assertTrue(sch.capability_available(sch.get_providers(settings(provider("G", 1, caps=("images",)))), ["images"]))

    # A busy slot forwards at once; prefer and avoid behave as before; free slots count every running provider process.
    def test_busy_slot_forwards(self):
        """A busy slot forwards at once; prefer and avoid behave as before; free slots count every running provider process."""
        ps = [provider("A", 2), provider("B", 2)]
        self.assertEqual(self.pick(ps, free={"A": 0, "B": 2}), "B")
        self.assertEqual(self.pick(ps, free={"A": 0, "B": 0}), "none")
        self.assertEqual(self.pick(ps, free={"A": 2, "B": 2}, prefer="B"), "B")
        self.assertEqual(self.pick(ps, free={"A": 2, "B": 0}, prefer="B"), "A")
        self.assertEqual(self.pick(ps, free={"A": 2, "B": 2}, avoid="A"), "B")
        self.assertEqual(self.pick(ps, free={"A": 2, "B": 0}, avoid="A"), "A")
        rows = [{"status": "running", "phase": "work", "type": "claude", "provider": "B"}, {"status": "starting", "phase": "work", "type": "claude"},
                {"status": "running", "phase": "integrate", "type": "claude", "provider": "A"}, {"status": "done", "phase": "work", "provider": "A"}]
        f = sch.provider_free(sch.get_providers(settings(provider("A", 2), provider("B", 2), provider("claude", 2))), rows)
        self.assertEqual((f["A"], f["B"], f["claude"]), (1, 1, 1))

    def test_capability_only_and_fallback(self):
        """capability-only gets no normal package; fallback only when every preferred provider is full or at its limit."""
        ps = [provider("A", 1, limit="x"), provider("G", 2, "capability-only", ("images",))]
        free = {"A": 1, "G": 2}
        lim = {"A": {"usedPercent": 100, "resetHours": 1}}
        self.assertEqual(self.pick(ps, free=free, limits=lim), "none")
        self.assertEqual(self.pick(ps, needs=["images"], free=free, limits=lim), "G")
        ps = [provider("P", 1), provider("F", 2, "fallback", limit="x")]
        lim = {"P": {"usedPercent": 60, "resetHours": 100}, "F": {"usedPercent": 0, "resetHours": 1}}
        self.assertEqual(self.pick(ps, free={"P": 1, "F": 2}, limits=lim), "P")
        self.assertEqual(self.pick(ps, free={"P": 0, "F": 2}, limits=lim), "F")
        ps = [provider("P", 1, limit="x"), provider("F", 2, "fallback", limit="x")]
        lim = {"P": {"usedPercent": 100, "resetHours": 100}, "F": {"usedPercent": 0, "resetHours": 1}}
        self.assertEqual(self.pick(ps, free={"P": 1, "F": 2}, limits=lim), "F")
        self.assertEqual(self.pick([provider("P", 1, classes=("medium",)), provider("F", 1, "fallback")], cls="light", free={"P": 1, "F": 1}), "F")

    def test_dummy_provider_and_models(self):
        """Only the classes of the entry count; an unlisted class falls back to the nearest listed one; wrong use or protocol is refused."""
        dummy = provider("dummy", 1, classes=("medium",))
        ps = sch.get_providers(settings(provider("A", 1), dummy))
        self.assertEqual(sch.get_provider(ps, "dummy")["slots"], 1)
        self.assertEqual(self.pick([dummy], cls="light", free={"dummy": 1}), "none")
        self.assertEqual(self.pick([provider("A", 1), dummy], free={"A": 0, "dummy": 1}), "dummy")
        self.assertEqual(sch.class_model(ps, "dummy", "medium")["Model"], "dummy-medium")
        self.assertEqual(sch.class_model(ps, "dummy", "heavy")["Model"], "dummy-medium")
        with self.assertRaisesRegex(ValueError, "use must be"):
            sch.get_providers(settings(provider("X", 1, "sometimes")))
        with self.assertRaisesRegex(ValueError, "protocol must be"):
            sch.get_providers(settings(provider("X", 1, proto="other")))

    def test_needs_and_endpoint(self):
        """Capabilities from the plan header or the rules; endpoint arguments for the codex CLI."""
        self.assertIn("images", sch.package_needs({}, "Blender models and textures"))
        self.assertEqual(sch.package_needs({}, "Change the parser"), [])
        rules = {"capabilityRules": [{"capability": "voice", "patterns": [r"(?i)\bnarration\b"]}]}
        self.assertIn("voice", sch.package_needs(rules, "Record the narration"))
        self.assertEqual(sch.package_needs(rules, "Blender models"), [])
        self.assertIn("images", sch.package_needs({}, "Draw the logo", "images"))
        self.assertEqual(sch.package_needs({}, "Blender models and textures", "voice"), ["voice"])
        self.assertEqual(sch.package_needs({}, "Draw the logo", "images, voice"), ["images", "voice"])
        self.assertIn("images", sch.package_needs({}, "Blender models and textures", ""))
        lm = {"name": "lmstudio", "protocol": "codex", "slots": 1, "classes": {"light": {"model": "qwen-coder", "effort": "medium"}},
              "endpoint": {"baseUrl": "http://192.0.2.10:1234/v1"}}
        ea = sch.endpoint_args(sch.get_providers(settings(lm))[0])
        self.assertIn("model_provider=lmstudio", ea)
        self.assertIn("model_providers.lmstudio.base_url=http://192.0.2.10:1234/v1", ea)
        self.assertIn("model_providers.lmstudio.wire_api=responses", ea)


class ProviderRoundTest(RoundCase):
    """Whole rounds with a provider list that exists only in the settings."""

    def setUp(self):
        """Dummy providers are set per test before the first round."""
        super().setUp()
        self.seed()

    def reset(self):
        """Empty the agent rows (keep the seed worktree row)."""
        self.conn.execute("DELETE FROM agents")
        self.conn.execute("DELETE FROM questions")
        self.seed()

    def prov(self, *p):
        """Set the provider list."""
        self.machine["providers"] = list(p)

    # One Queen uses one provider slot for a plan with two packages.
    def test_dummy_single_slot(self):
        """One Queen uses one provider slot for a plan with two packages."""
        self.prov(provider("dummy", 1))
        self.plan("IN_PROGRESS", [{"id": "W1", "title": "change the parser"}, {"id": "W2", "title": "change the lexer"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "dummy")

    # A disabled provider is skipped when starting the plan's Queen.
    def test_slots_zero_and_forwarding(self):
        """A disabled provider is skipped when starting the plan's Queen."""
        self.prov(provider("dummy", 0), provider("other", 1))
        self.plan("IN_PROGRESS", [{"id": "W1", "title": "change the parser"}, {"id": "W2", "title": "change the lexer"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "other")

    # An image package does not change the provider chosen for the plan's Queen.
    def test_graphics_package(self):
        """An image package does not change the provider chosen for the plan's Queen."""
        self.prov(provider("plain", 2), provider("gfx", 1, "capability-only", ("images",), proto="codex"))
        self.plan("IN_PROGRESS", [{"id": "G1", "title": "Blender models and textures"}, {"id": "W1", "title": "change the parser"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "plain")

    # A missing image provider does not prevent the Queen starting the plan.
    def test_no_provider_with_capability(self):
        """A missing image provider does not prevent the Queen starting the plan."""
        self.prov(provider("plain", 2), provider("gfx", 0, "capability-only", ("images",), proto="codex"))
        self.plan("IN_PROGRESS", [{"id": "G1", "title": "Blender models and textures"}, {"id": "W1", "title": "change the parser"}])
        self.round()
        active = [a for a in runtime.list_agents(self.conn, "testproj", "active") if a["phase"] == "queen" and a["status"] == "running"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["provider"], "plain")
        self.assertEqual(runtime.list_questions(self.conn, "testproj"), [])


class ExampleConfigTest(unittest.TestCase):
    """Port of the s9 part 'argument templates': the example provider entries carry the reduced start context options."""

    def test_example_args(self):
        """claude: tools, setting sources, rules file; codex: --ignore-user-config."""
        import tomllib
        from pathlib import Path
        cfg = tomllib.loads((Path(__file__).resolve().parent.parent / "examples" / "config.example.toml").read_text(encoding="utf-8"))
        provs = {p["name"]: p for p in sch.get_providers({"providers": cfg["providers"], "slots": cfg.get("slots", 2)})}
        cl, cx = " ".join(provs["claude"]["args"]), " ".join(provs["codex"]["args"])
        self.assertIn("--tools {claudeTools}", cl)
        self.assertIn("--append-system-prompt-file {rules}", cl)
        self.assertIn("--setting-sources", cl)
        self.assertIn("--ignore-user-config", cx)
        self.assertEqual(sch.class_model(list(provs.values()), "claude", "heavy")["Model"], "claude-opus-5-5")
        self.assertEqual(sch.class_model(list(provs.values()), "codex", "medium")["Model"], "gpt-6.1-terra")


if __name__ == "__main__":
    unittest.main()
