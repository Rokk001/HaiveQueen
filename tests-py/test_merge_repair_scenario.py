"""Isolated #740-shaped scenario; deterministic provider, real Git and verification."""
import json
import sys
from pathlib import Path

from bzztest import REL
from haivequeen import integration, orchestrator, repair_review, runtime
from test_o1_orchestrator import OTHER, OrchCase


class MergeRepairScenario(OrchCase):
    def test_comment_conflicts_and_followup_failure_finish_both_plans(self):
        base = '// base explanation\nint getBrighterMaterial() { return 1; }\n'
        self.commit(self.repo, 'RenderManager.cpp', base)
        self.git('push', '-q', 'origin', 'main')
        self.git('merge', '--ff-only', 'main', cwd=self.pwt)
        second = self.make_worktree('second-br', 'secondwt')
        self.ready_plan()
        self.ready_plan(OTHER, 'second-br', second, 'other.txt')
        for folder in (self.pwt, second):
            self.commit(folder, 'RenderManager.cpp', base.replace('base explanation', 'retain brighter material'))
        self.remote_commit('RenderManager.cpp', base.replace('base explanation', 'reuse the bright variant'))
        script = "from pathlib import Path; s=Path('RenderManager.cpp').read_text(); assert '<<<<<<<' not in s; assert 'return 1;' in s"
        self.check = '"%s" -c "%s"' % (sys.executable, script)
        script = "from pathlib import Path; assert Path('index.txt').exists(), 'required generated index missing'"
        self.build = '"%s" -c "%s"' % (sys.executable, script)
        self.project_toml()
        seen, failed_once, diagnosed, repaired = set(), set(), set(), set()
        for _ in range(18):
            self.round()
            for agent in self.fixers():
                if agent['name'] in seen or agent['status'] != 'running':
                    continue
                seen.add(agent['name'])
                plan, folder = agent['plan_path'], Path(agent['cwd'])
                state = self.state(plan)
                fix = state['fix']
                if fix.get('diagnosis'):
                    conflicted = self.git('diff', '--name-only', '--diff-filter=U', cwd=folder)
                    self.assertIn('RenderManager.cpp', conflicted)
                    ours = self.git('show', ':2:RenderManager.cpp', cwd=folder)
                    theirs = self.git('show', ':3:RenderManager.cpp', cwd=folder)
                    self.assertEqual(ours.splitlines()[1:], theirs.splitlines()[1:])
                    known = repair_review.decisions(self.conn, 'testproj', plan)
                    report = dict(kind='technical', commit=integration.head_commit(folder),
                        failure_log=fix.get('log_file', ''), reviewed_decisions=[d['id'] for d in known],
                        evidence=['RenderManager.cpp stages 2 and 3: implementation identical, only comment differs'],
                        analysis='The remaining conflict has no product behavior difference.',
                        next_action='Retain both comment explanations and run the unchanged checks.',
                        why_new='The unmerged Git stages were inspected after the unsuccessful attempt.')
                    (self.env().run_dir(agent['name']) / 'repair-diagnosis.json').write_text(json.dumps(report), encoding='utf-8')
                    self.end_fixer(agent, verification=False)
                    diagnosed.add(plan)
                elif fix['step'] == 'Merge' and plan not in failed_once:
                    self.end_fixer(agent, 'No repair was produced.', verification=False)
                    failed_once.add(plan)
                elif fix['step'] == 'Merge':
                    self.commit(folder, 'RenderManager.cpp',
                        '// retain brighter material; reuse the bright variant\nint getBrighterMaterial() { return 1; }\n')
                    self.end_fixer(agent, verification_command=self.check)
                elif fix['step'] == 'Build':
                    self.commit(folder, 'index.txt', 'verified fixture\n')
                    self.end_fixer(agent, verification_command=self.build)
                    repaired.add(plan)
                else:
                    self.fail('Unexpected repair step: ' + fix['step'])
            if self.plan_status() == self.plan_status(OTHER) == 'REVIEW':
                break
        self.assertEqual(self.plan_status(), 'REVIEW')
        self.assertEqual(self.plan_status(OTHER), 'REVIEW')
        self.assertTrue(diagnosed)
        self.assertTrue(repaired)
        self.assertFalse([q for q in runtime.list_questions(self.conn, 'testproj', 'open') if q['item'] != 'owner-check'])
        for plan in (REL, OTHER):
            verified = [r for r in orchestrator.repair_history(self.conn, 'testproj', plan) if r['event'] == 'verified']
            self.assertTrue(verified)
            self.assertIn('Build', verified[-1]['steps'])
