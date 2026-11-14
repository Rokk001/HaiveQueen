"""Cutting a submitted plan: requirements with ids in the state file, capability packages, dependencies, and the requirement check of bzz plan check."""
import unittest
from unittest import mock

from bzztest import REL, runtime
from haivequeen import plans, roles
import test_plan_submit
from test_plan_submit import plan_of

STATE_REL = plans.state_file_path(REL)
REQUIREMENTS = "# State\n\n## Requirements\n- R1: Show the window\n- R2: Draw the logo\n- R3: Save the file\n\n## Notes\n- X9: not a requirement\n"


class RequirementFormatTests(unittest.TestCase):
    """The format of the requirement list in the state file."""

    def test_requirements_are_read_with_their_ids(self):
        """Lines '- id: text' under the heading Requirements are read, other sections are not."""
        self.assertEqual(plans.parse_requirements(REQUIREMENTS), [("R1", "Show the window"), ("R2", "Draw the logo"), ("R3", "Save the file")])

    def test_the_authors_own_ids_are_kept(self):
        self.assertEqual(plans.parse_requirements("## Requirements\n- 3.2: Sound\n- A-4: Music\n"), [("3.2", "Sound"), ("A-4", "Music")])

    def test_state_file_sits_beside_the_plan(self):
        self.assertTrue(plans.state_file_path("docs/plans/X-PLAN.md").replace("\\", "/").endswith("docs/plans/X-STATE.md"))

    def test_package_requirements_are_parsed(self):
        plan = plans.parse_plan_text('+++\nstatus = "READY"\n[[packages]]\nid = "P1"\ntitle = "t"\nrequirements = ["R1", "R2"]\n+++\n# T\n')
        self.assertEqual(plan.packages[0].requirements, ["R1", "R2"])

    def test_an_uncovered_requirement_is_a_problem(self):
        plan = plan_of([("P1", "First", [], "")])
        plan.packages[0].requirements = ["R1"]
        self.assertEqual(plans.check_plan(plan, set(), [("R1", "a")]), [])
        text = " ".join(plans.check_plan(plan, set(), [("R1", "a"), ("R2", "b")]))
        self.assertIn("requirement R2 belongs to no package", text)
        self.assertIn("is not in the state file", " ".join(plans.check_plan(plan, set(), [("R2", "b")])))


class CutRoleTests(unittest.TestCase):
    """The cutting instructions of the role file."""

    def test_role_file_tells_how_to_cut(self):
        text = roles.role_text("queen-cut", {"project": "P", "plan": "x.md", "branch": "b", "worktree": "w"})
        for part in ("free text or already formatted", "## Requirements", "- <id>: <requirement text>", "ids the plan author already gave",
                     'capability = "<name>"', "contains no other work", "run in parallel", "depends_on", "have no depends_on between them",
                     "belongs to no package", "as small and sensible as possible"):
            self.assertIn(part, text)
        self.assertNotIn("{{", text)


class CutCheckTests(test_plan_submit.SubmittedPlanTests):
    """bzz plan check with a requirement list in the state file."""

    def state(self, text=REQUIREMENTS):
        (self.repo / STATE_REL).write_text(text, encoding="utf-8")

    def test_check_refuses_while_a_requirement_belongs_to_no_package(self):
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "First", "requirements": ["R1", "R2"]}])
        self.state()
        code, output = self.check()
        self.assertNotEqual(code, 0)
        self.assertIn("requirement R3 belongs to no package", output)
        self.assertEqual(plans.load_plan(str(self.repo / REL)).status, "SENT")

    @mock.patch("haivequeen.mcp_setup.status", return_value={"available": ["Image generation", "3D modelling"]})
    def test_check_passes_when_every_requirement_is_covered_and_capability_work_is_apart(self, verified_setup):
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "Window", "requirements": ["R1", "R3"]},
                                    {"id": "P2", "title": "Logo", "capability": "images", "requirements": ["R2"]},
                                    {"id": "P3", "title": "Use the logo", "depends": ["P2"], "requirements": ["R1"]}])
        self.state()
        code, output = self.check()
        self.assertEqual(code, 0, output)
        packages = {p["id"]: p for p in runtime.list_packages(self.conn, "testproj", REL)}
        self.assertEqual(packages["P2"]["capability"], "images")
        self.assertEqual(packages["P3"]["depends_on"], ["P2"])
        self.assertEqual(packages["P1"]["depends_on"], [])

    def test_without_a_requirement_list_the_check_is_as_before(self):
        self.queen_of("SUBMITTED", [{"id": "P1", "title": "First"}])
        code, output = self.check()
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
