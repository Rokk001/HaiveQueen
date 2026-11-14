"""T1 terminology and compatibility regressions."""
import unittest
from pathlib import Path

from bzztest import REL, RoundCase
from haivequeen import cli, db, hives


class TerminologyTests(unittest.TestCase):
    def test_agent_command_and_legacy_alias_share_handler(self):
        parser = cli.build_parser()
        options = ['wait', '--project', 'p', '--plan', 'p.md', '--package', 'T1', '--class', 'medium']
        self.assertIs(parser.parse_args(['agent', *options]).fn,
                      parser.parse_args(['bee', *options]).fn)
        self.assertIn('agent', parser.format_help())

    def test_plan_list_uses_standard_terms(self):
        source = Path('haivequeen/web-client.js').read_text(encoding='utf-8')
        self.assertIn("section('Plans')", source)
        self.assertNotIn('tr("Has Hive")', source)

    def test_public_docs_use_standard_terms(self):
        import re
        for path in [Path('README.md'), *Path('docs').rglob('*.md'), *Path('roles').rglob('*.md')]:
            text = path.read_text(encoding='utf-8')
            text = text.replace('bzz bee', '')
            text = text.replace('`hives`', '')  # Existing config key.
            text = text.replace('`Hives`', '')  # Approved dashboard section title.
            text = re.sub(r'`(?:hives|bees|apiary)\.[^`]+`', '', text)  # Internal code references.
            text = text.replace('`apiary`', '')  # Existing web API field.
            text = re.sub(r'`[^`]*\.(?:py|md|json|toml)`', '', text)  # Existing file names remain compatible.
            with self.subTest(path=str(path)):
                self.assertIsNone(re.search(r'\b(?:Apiar(?:y|ies)|Hives?|Beekeepers?|Bees?)\b', text, re.I))


class OwnerCheckUpgradeTests(RoundCase):
    def test_unprocessed_answer_remains_waiting(self):
        qid = db.add_question(self.conn, 'testproj', REL, 'owner-check', 'Check', '')
        db.answer_question(self.conn, qid, 'ok', 'owner')
        self.assertEqual(hives.owner_check_step(self.conn, 'testproj', REL), ('wait', [qid]))
