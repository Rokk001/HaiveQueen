import unittest
from haivequeen import readygate, runtime
from bzztest import REL

class OwnerVerificationTests(unittest.TestCase):
    def plan(self, packages):
        return '+++\n' + ''.join('[[packages]]\nid = "P%s"\nverify = "%s"\nrequirements = ["R-1"]\n' % (i, v) for i, v in enumerate(packages)) + '+++\n# Plan\n## Requirements\n- R-1: owner check result\n'

    def test_only_all_owner_packages_exempt(self):
        for verifies, exempt in [(['owner'], True), (['owner', 'owner'], True), (['owner', 'code'], False), ([], False)]:
            result = readygate.evaluate(self.plan(verifies), '## Completeness\n- R-1: owner-check\n', [])
            self.assertEqual(result.owner == ['R-1'], exempt, verifies)
            self.assertEqual(bool(result.blocking), not exempt, verifies)

class CutSnapshotTests(__import__('test_plan_submit').SubmittedPlanTests):
    def test_snapshot_survives_header_changes_and_rechecks(self):
        from haivequeen import plans
        self.queen_of('SUBMITTED', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Second', 'verify': 'code', 'requirements': ['R-2']}])
        self.assertEqual(self.check()[0], 0)
        original = runtime.cut_assignments(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8'))
        self.assertEqual(original, [['R-1', 'owner'], ['R-2', 'code']])
        self.plan('READY', [{'id': 'P1', 'title': 'Changed', 'verify': 'code', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Changed', 'verify': 'owner', 'requirements': ['R-2']}])
        self.check()
        self.assertEqual(runtime.cut_assignments(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8')), original)

    def test_existing_plan_without_snapshot_is_conservative(self):
        self.queen_of('READY', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}])
        runtime.kv_delete(self.conn, 'testproj', 'cut-verification:' + REL)   # an older plan: worked on before cut records existed
        runtime.sync_plans(self.conn, 'testproj')
        self.assertIsNone(runtime.cut_assignments(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8')))
        result = readygate.evaluate(OwnerVerificationTests().plan(['owner']), '## Completeness\n- R-1: owner-check\n', [], [])
        self.assertEqual(result.owner, [])
        self.assertTrue(result.blocking)


    def test_mixed_cut_coverage_does_not_exempt_after_header_edit(self):
        self.queen_of('SUBMITTED', [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}, {'id': 'P2', 'title': 'Second', 'verify': 'code', 'requirements': ['R-1']}])
        self.assertEqual(self.check()[0], 0)
        assignments = runtime.cut_assignments(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8'))
        result = readygate.evaluate(OwnerVerificationTests().plan(['owner']), '## Completeness\n- R-1: owner-check\n', [], assignments)
        self.assertEqual(result.owner, [])
        self.assertTrue(result.blocking)


class CutLifecycleTests(__import__('test_plan_submit').SubmittedPlanTests):
    """A cut record belongs to one plan generation and title; a reused path or another plan never inherits it, edits keep it, and an older plan is recovered only when no agent can have changed its header."""
    OWNER = [{'id': 'P1', 'title': 'First', 'verify': 'owner', 'requirements': ['R-1']}]
    CODE = [{'id': 'P1', 'title': 'First', 'verify': 'code', 'requirements': ['R-1']}]

    def text(self):
        return (self.repo / REL).read_text(encoding='utf-8')

    def stored(self):
        return runtime.cut_assignments(self.conn, 'testproj', REL, self.text())

    def test_recut_by_status_flip_keeps_the_first_cut(self):
        """A header set back to SUBMITTED and checked again with other verify values does not replace the accepted cut."""
        self.queen_of('SUBMITTED', self.CODE)
        self.assertEqual(self.check()[0], 0)
        self.plan('SUBMITTED', self.OWNER, branch='fix/x', worktree=str(self.base / 'wt-x'))
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.stored(), [['R-1', 'code']])

    def test_edited_plan_text_keeps_the_cut(self):
        """Edits below the header (wording, new requirement lines) and status changes keep the cut of this plan generation."""
        self.queen_of('SUBMITTED', self.OWNER)
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.stored(), [['R-1', 'owner']])
        edited = self.text().replace('status = "QUEUED"', 'status = "RUNNING"') + '\nReworded text.\n\n## Requirements\n\n- R-1: changed wording\n- R-2: added later\n'
        (self.repo / REL).write_text(edited, encoding='utf-8')
        runtime.sync_plans(self.conn, 'testproj')
        self.assertEqual(self.stored(), [['R-1', 'owner']])

    def test_other_plan_title_at_the_path_has_no_cut(self):
        """Another plan written over an active path (another title) does not inherit the cut, and a later check cannot record one."""
        self.queen_of('SUBMITTED', self.OWNER)
        self.assertEqual(self.check()[0], 0)
        self.plan('SUBMITTED', self.OWNER, title='Another plan', branch='fix/x', worktree=str(self.base / 'wt-x'))
        self.assertIsNone(self.stored())
        self.assertEqual(self.check()[0], 0)
        self.assertIsNone(self.stored())

    def test_old_generation_record_is_not_used(self):
        """A record written before the path's last reset does not apply even if it was not deleted."""
        self.queen_of('SUBMITTED', self.OWNER)
        self.assertEqual(self.check()[0], 0)
        value = runtime.kv_get(self.conn, 'testproj', 'cut-verification:' + REL)
        runtime.reset_cut_assignments(self.conn, 'testproj', REL)
        runtime.kv_set(self.conn, 'testproj', 'cut-verification:' + REL, value)
        self.assertIsNone(self.stored())

    def test_reused_path_after_done_gets_its_own_cut(self):
        """A finished plan's path written again with a new plan is cut afresh; the old record is gone at once."""
        self.queen_of('SUBMITTED', self.OWNER)
        self.assertEqual(self.check()[0], 0)
        runtime.set_plan_status(self.conn, 'testproj', REL, 'DONE')
        self.finish('queen')
        self.plan('SUBMITTED', self.CODE, title='Second plan', branch='fix/x', worktree=str(self.base / 'wt-x'))
        self.assertIsNone(runtime.kv_get(self.conn, 'testproj', 'cut-verification:' + REL))
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.stored(), [['R-1', 'code']])

    def test_owner_reject_and_restart_start_a_new_generation(self):
        """Cancelling a stopped plan drops its cut; starting the redrafted plan sends it and its Queen's checked cut is the new record."""
        from haivequeen import hives
        self.queen_of('SUBMITTED', self.CODE)
        self.assertEqual(self.check()[0], 0)
        self.finish('queen')
        hives.pause_plan(self.conn, 'testproj', REL)
        hives.reject_plan(self.conn, 'testproj', REL)
        self.assertIsNone(runtime.kv_get(self.conn, 'testproj', 'cut-verification:' + REL))
        hives.start_again(self.conn, 'testproj', REL)
        self.plan('DRAFT', self.OWNER, branch='fix/x', worktree=str(self.base / 'wt-x'))
        hives.start_plan(self.conn, 'testproj', REL)
        self.assertIn('status = "SENT"', self.text())
        self.assertIsNone(self.stored())   # Start records no cut: the plan is prepared like any sent plan
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.stored(), [['R-1', 'owner']])

    def test_unworked_ready_plan_is_recovered(self):
        """An older READY plan no agent has worked on yet gets its header as cut record."""
        self.plan('READY', self.OWNER, branch='fix/x', worktree=str(self.base / 'wt-x'))
        self.assertEqual(self.stored(), [['R-1', 'owner']])

    def test_worked_older_plan_is_not_recovered(self):
        """An older plan with a Queen or Bee before any cut record stays without one: evidence is required."""
        self.plan('READY', self.OWNER, branch='fix/x', worktree=str(self.base / 'wt-x'))
        runtime.kv_delete(self.conn, 'testproj', 'cut-verification:' + REL)
        self.agent('queen', phase='queen', status='running', provider='claude', pid=42)
        runtime.sync_plans(self.conn, 'testproj')
        self.assertIsNone(self.stored())

    def test_reused_path_is_recovered_only_without_new_agents(self):
        """After a finished plan, agents of the old generation do not block recovery of the new unworked plan; a new agent does."""
        self.queen_of('SUBMITTED', self.CODE)
        self.assertEqual(self.check()[0], 0)
        self.finish('queen')
        self.conn.execute("UPDATE agents SET started_at=?", ('2026-01-01T00:00:00',))
        runtime.set_plan_status(self.conn, 'testproj', REL, 'DONE')
        self.plan('READY', self.OWNER, title='Second plan', branch='fix/x', worktree=str(self.base / 'wt-x'))
        self.assertEqual(self.stored(), [['R-1', 'owner']])

    def gate_after_edit(self, packages, extra_header, requirement_lines):
        """Cut the plan, then edit it as the owner may (a package gains a requirement, wording changes, new lines); return the gate result."""
        from haivequeen import readygate
        self.queen_of('SUBMITTED', packages)
        self.assertEqual(self.check()[0], 0)
        text = self.text()
        for old, new in extra_header:
            text = text.replace(old, new, 1)
        (self.repo / REL).write_text(text + '\nReworded after the cut.\n\n## Requirements\n\n' + requirement_lines, encoding='utf-8')
        runtime.sync_plans(self.conn, 'testproj')
        runtime.record_submission(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8'))   # submitted again: the edited text is the reference
        (self.repo / REL.replace('-PLAN.md', '-STATE.md')).write_text('# State\n', encoding='utf-8')
        return readygate.gate(self.env(), REL, {'cwd': ''})

    def test_gate_keeps_owner_exemption_through_later_requirements(self):
        """The cut owner requirement stays exempt after edits; a requirement added later, even to the owner package, needs evidence."""
        accepted, message, _ = self.gate_after_edit(self.OWNER, [('requirements = ["R-1"]', 'requirements = ["R-1", "R-2"]')],
                                                    '- R-1: owner check, reworded\n- R-2: added later\n')
        self.assertFalse(accepted)
        self.assertIn('R-2 (missing in the list)', message)
        self.assertNotIn('R-1', message)

    def test_gate_keeps_mixed_cut_non_exempt_after_edits(self):
        """A requirement the cut covered by an owner and a code package needs evidence also after edits and later additions."""
        mixed = self.OWNER + [{'id': 'P2', 'title': 'Second', 'verify': 'code', 'requirements': ['R-1']}]
        accepted, message, _ = self.gate_after_edit(mixed, [], '- R-1: mixed\n- R-3: added later\n')
        self.assertFalse(accepted)
        self.assertIn('R-1 (missing in the list)', message)
        self.assertIn('R-3 (missing in the list)', message)

    def test_gate_uses_the_cut_of_this_plan_title(self):
        """The gate exempts by the cut record only while the plan at the path keeps its title."""
        from haivequeen import readygate
        self.plan('READY', self.OWNER)
        self.plan('READY', self.OWNER, title='Another plan')
        with (self.repo / REL).open('a', encoding='utf-8') as f:
            f.write('\n## Requirements\n\n- R-1: owner check\n')
        runtime.record_submission(self.conn, 'testproj', REL, (self.repo / REL).read_text(encoding='utf-8'))
        state = self.repo / REL.replace('-PLAN.md', '-STATE.md')
        state.write_text('# State\n', encoding='utf-8')
        accepted, message, _ = readygate.gate(self.env(), REL, {'cwd': ''})
        self.assertFalse(accepted)
        self.assertIn('R-1', message)
