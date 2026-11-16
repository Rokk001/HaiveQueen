import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DependencyPins(unittest.TestCase):
    def test_actions_use_immutable_revisions(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            for action in re.findall(r'uses:\s*([^\s#]+)', path.read_text(encoding='utf-8')):
                if not action.startswith('./'):
                    self.assertRegex(action, r'@[0-9a-f]{40}$', str(path))

    def test_installs_pin_existing_dependencies_and_match_release_builder(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        for requirement in project['build-system']['requires'] + project['project']['dependencies']:
            self.assertRegex(requirement, r'^[A-Za-z0-9_-]+==\d+(\.\d+)+$')
        self.assertIn(project['build-system']['requires'][0],
                      (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8'))
