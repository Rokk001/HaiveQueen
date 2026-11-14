import unittest
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from haivequeen import scheduler, settings, web_ui, repair_review, orchestrator, roles


class DiagnosisSettings(unittest.TestCase):
    def test_diagnosis_reads_role_template_on_every_start(self):
        original = roles.role_text
        with patch.object(roles, 'role_text', side_effect=lambda name, values: 'MARKER\n' + original(name, values)) as loader:
            text = repair_review.prompt('result.json', 'head', 'run.jsonl', [], [])
        self.assertEqual(loader.call_args.args[0], 'diagnosis')
        self.assertIn('MARKER', text)
        self.assertIn('result.json', text)
        self.assertNotIn('{{', text)

    def test_fix_reads_role_template_on_every_start(self):
        env = SimpleNamespace(workspace=Path('.'), conn=None, name='testproj')
        state = {'fix': {'reason': 'step', 'step': 'Check', 'error': 'failed'}}
        with patch.object(orchestrator, '_worktree', return_value=Path('.')), \
             patch.object(orchestrator, '_remote', return_value=('origin', 'main')), \
             patch.object(orchestrator, 'repair_history', return_value=[]), \
             patch.object(roles, 'role_text', wraps=roles.role_text) as loader:
            text = orchestrator._fix_prompt(env, 'missing.md', state)
        self.assertEqual(loader.call_args.args[0], 'fix')
        self.assertIn('failed', text)
        self.assertNotIn('{{', text)

    def test_fix_references_plan_instead_of_embedding_it(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = Path(folder) / 'plan.md'
            plan.write_text('UNRELATED-PLAN-TEXT ' * 4000, encoding='utf-8')
            env = SimpleNamespace(workspace=Path(folder), conn=None, name='testproj')
            state = {'fix': {'reason': 'step', 'step': 'Check', 'error': 'failed'}}
            with patch.object(orchestrator, '_worktree', return_value=Path(folder)), \
                 patch.object(orchestrator, '_remote', return_value=('origin', 'main')), \
                 patch.object(orchestrator, 'repair_history', return_value=[{'event': 'attempt-ended', 'report': 'OLD-REPORT ' * 4000}]):
                text = orchestrator._fix_prompt(env, 'plan.md', state)
            self.assertFalse('UNRELATED-PLAN-TEXT' in text)
            self.assertLess(len(text), 6500)
            self.assertIn('orch-repair-history:plan.md', text)
            self.assertIn(str(plan), text)
            self.assertIn('failed', text)

    def test_large_history_is_retrievable_without_bloating_start_context(self):
        with tempfile.TemporaryDirectory() as folder:
            history = [{'event': 'attempt-ended', 'report': 'LONG-REPORT-' * 1000} for _ in range(20)]
            known = [{'id': n, 'text': 'LONG-DECISION-' * 1000} for n in range(20)]
            prompt = repair_review.prompt(Path(folder) / 'result.json', 'head', 'run.jsonl',
                                          known, history, project='testproj', plan='plans/test.md')
            self.assertLess(len(prompt), 6500)
            self.assertNotIn(history[0]['report'], prompt)
            self.assertNotIn(known[0]['text'], prompt)
            self.assertIn('bzz kv get', prompt)
            self.assertIn('orch-repair-history:plans/test.md', prompt)
            self.assertIn('bzz decisions list', prompt)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_diagnosis_class_is_exposed_like_queen_class(self):
        self.assertEqual(scheduler.DEFAULT_SETTINGS['diagnosisClass'], 'medium')
        self.assertEqual(settings.CHOICES['diagnosisClass'], settings.CHOICES['queenClass'])
        self.assertIn('diagnosisClass', settings.GENERAL_LABELS)
        self.assertIn('effort', settings.GENERAL_HELP['diagnosisClass'])
        self.assertIn('diagnosisClass', settings.SHORT_KEYS)
        data = json.loads(re.search(r'<script id="bootstrap" type="application/json">(.*?)</script>',
                                    web_ui.page('test', 'English')).group(1))
        self.assertEqual(data['general']['diagnosisClass'], 'medium')
        self.assertEqual(data['choices']['diagnosisClass'], ['light', 'medium', 'heavy'])

    def test_configured_class_uses_provider_model_and_effort(self):
        config = scheduler.build_settings({'diagnosisClass': 'light'}, {})
        providers = [{'name': 'test', 'classes': {
            'light': {'model': 'chosen-model', 'effort': 'low'},
            'heavy': {'model': 'other-model', 'effort': 'high'}}}]
        self.assertEqual(scheduler.class_model(providers, 'test', config['diagnosisClass']),
                         {'Model': 'chosen-model', 'Effort': 'low'})
