import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from haivequeen import dashboard_data, orchestrator, version_leases
from datetime import datetime


class VersionVisibilityTests(unittest.TestCase):
    def test_queen_lease_remains_old_after_switch_and_unknown_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'versions' / 'new' / 'haivequeen').mkdir(parents=True)
            (root / 'active-version').write_text('new')
            with patch.object(dashboard_data, '__file__', str(root / 'versions' / 'new' / 'haivequeen' / 'dashboard_data.py')), \
                 patch.object(version_leases, '_load', return_value={'a': {'pid': 42, 'identity': 'birth', 'version': 'old'}}):
                self.assertEqual(dashboard_data.queen_version({'pid': 42, 'processIdentity': 'birth'}), 'old')
                self.assertEqual(dashboard_data.queen_version({'pid': 42, 'processIdentity': 'other'}), '–')
                self.assertEqual(dashboard_data.queen_version({'pid': 42}), '–')
                queen = {'pid': 42, 'processIdentity': 'birth', 'status': 'running', 'name': 'Q'}
                hive = {'project': 'P', 'plan_path': 'p.md', 'queen': queen}
                self.assertEqual(dashboard_data.hive_row(hive, [], {}, datetime.now())['queen_version'], 'old')

    def test_build_and_deploy_report_job_version_not_core_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'versions' / 'old'
            new = root / 'versions' / 'new' / 'haivequeen'
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / '.haivequeen-release-version').write_text('1.0.0')
            (root / 'active-version').write_text('new')
            with patch.object(dashboard_data, '__file__', str(new / 'dashboard_data.py')):
                for step in ('Build', 'Deploy'):
                    state = {'phase': 'pipeline', 'step': step, 'job': {'version_path': str(old)}, 'steps': {step: 'running'}}
                    with patch.object(orchestrator, 'load', return_value=state):
                        info = dashboard_data.orch_info(None, None, {'project': 'P', 'plan_path': 'p.md'})
                    self.assertEqual(info['version'], '1.0.0')
                self.assertEqual(dashboard_data.job_version({'step': 'Build', 'job': {}}), '–')


if __name__ == '__main__':
    unittest.main()
