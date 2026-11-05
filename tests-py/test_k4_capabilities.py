"""Provider capability migration and routing regressions."""
import unittest
from haivequeen import bees, scheduler as sch


class CapabilityTests(unittest.TestCase):
    def provider(self, **values):
        return {"name": "p", "protocol": "codex", "slots": 1,
                "classes": {"medium": {"model": "m"}}, **values}

    def test_legacy_mapping_and_queen_defaults(self):
        p = sch.get_providers({"providers": [self.provider(use="capability-only", capabilities=["images", "blender", "build"])]})[0]
        self.assertEqual(p["capabilities"], ["Image generation", "3D modelling", "Build", "Queen"])
        self.assertEqual(p["use"], "preferred")
        self.assertEqual(sch.package_needs({}, "", "images, blender, build"), ["Image generation", "3D modelling", "Build"])
        self.assertEqual(bees.route_candidates([p], "p", [], False), [])
        self.assertEqual(bees.route_candidates([p], "p", ["images"], False), [p])

    def test_explicit_choices_and_endpoint_legacy(self):
        p = sch.get_providers({"providers": [self.provider(capabilitiesVersion=1, capabilities=["Queen"], endpoint={"baseUrl": "http://localhost"})]})[0]
        self.assertEqual(p["capabilities"], ["Queen"])
        self.assertIs(sch.select_provider([p], "medium", ["Queen"], {"p": 1}), p)
        old = sch.get_providers({"providers": [self.provider(endpoint={"baseUrl": "http://localhost"})]})[0]
        self.assertEqual(old["capabilities"], ["Coding"])

    def test_disabled_limit_keeps_default_rank(self):
        p = sch.get_providers({"providers": [self.provider(limit={"source": "none"})]})[0]
        self.assertEqual(sch.provider_limit(p, limits={"p": {"usedPercent": 100}}), {"FreePct": 100.0, "Hours": 168.0})


if __name__ == "__main__":
    unittest.main()
