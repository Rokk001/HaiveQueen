import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from bzztest import RoundCase
from haivequeen import loop, platform, runtime, scheduler, selfupdate, version_layout


class ReleaseRollbackProcessTest(RoundCase):
    def setUp(self):
        super().setUp()
        # These process tests isolate core recovery, not archive authentication.
        # Authentication and compatibility use real fixtures in the public
        # release lifecycle and provenance tests.
        for name, value in (
            ('haivequeen.release_staging.verify_installed', True),
            ('haivequeen.release_contract.prepare', {'verified': 'fixture'}),
            ('haivequeen.release_contract.assess', {'known': True, 'compatible': True, 'reason': '', 'evidence': {}}),
        ):
            override = patch(name, return_value=value)
            override.start()
            self.addCleanup(override.stop)

    def test_real_launcher_recovers_old_core_after_missed_round(self):
        root = self.base / 'install'
        source = Path(__file__).resolve().parent.parent
        for version in ('old', 'new'):
            dest = root / 'versions' / version
            shutil.copytree(source / 'haivequeen', dest / 'haivequeen', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(source / 'roles', dest / 'roles')
            (dest / 'haivequeen' / 'cli.py').write_text('''
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest.mock import patch
from haivequeen import config, db, loop, proctree, version_leases
base = Path(os.environ['TEST_BASE'])
version = Path(__file__).resolve().parents[1].name
if '--child' not in sys.argv:
    subprocess.Popen([sys.executable, '-I', __file__, '--child'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sys.exit(0)
conn = db.connect(config.db_path())
def round_(conn, machine, envs, **kwargs):
    (base / (version + '-round')).write_text(str(os.getpid()))
    if version == 'new':
        while (base / 'install' / 'active-version').read_text().strip() == 'new': time.sleep(.02)
with patch.object(loop, 'run_round', side_effect=round_), patch.object(loop, 'daily_export'), \\
     patch.object(proctree, 'sweep'), patch.object(version_leases, 'collect', return_value=[]):
    loop.run_loop(conn, {'providers': [], 'projects': [], 'roundMinutes': .01}, wait_seconds=.05)
conn.close()
(base / (version + '-exited')).touch()
''', encoding='utf-8')
        (root / 'versions' / 'new' / 'haivequeen-update.json').write_text('{"compatible_with_older": true}', encoding='utf-8')
        version_layout.install_launcher(root)
        version_layout.set_active(root, 'old')
        try:
            with patch.dict(os.environ, {'TEST_BASE': str(self.base)}):
                result = selfupdate.run_update(self.conn, live=root / 'versions' / 'old', version='new', poll=.05, round_timeout=12)
            self.assertEqual(result['result'], 'rollback', result)
            self.assertEqual((root / 'active-version').read_text().strip(), 'old')
            self.assertTrue((self.base / 'new-round').exists())
            pid = loop.loop_running()
            self.assertGreater(pid, 0)
            receipt = json.loads(runtime.kv_get(self.conn, scheduler.GLOBAL, 'loop-completed-round'))
            self.assertEqual(receipt['pid'], pid)
            self.assertEqual(receipt['identity'], platform.process_identity(pid))
            self.assertEqual(Path(receipt['code_path']), root / 'versions' / 'old')
            row = self.conn.execute('SELECT result FROM self_updates ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(row['result'], 'rollback')
            self.assertIn('rolled back', runtime.kv_get(self.conn, scheduler.GLOBAL, 'global-fault:self_update_rollback'))
        finally:
            loop.stop_flag().parent.mkdir(parents=True, exist_ok=True)
            loop.stop_flag().touch()
            deadline = time.monotonic() + 10
            while (loop.loop_running() or not (self.base / 'old-exited').exists() or not (self.base / 'new-exited').exists()) and time.monotonic() < deadline:
                time.sleep(.05)
            time.sleep(.5)




    def test_non_yielding_core_is_not_terminated_on_rollback(self):
        root = self.base / 'install'
        for version in ('old', 'new'):
            folder = root / 'versions' / version / 'haivequeen'
            folder.mkdir(parents=True)
            (folder / 'cli.py').write_text('pass', encoding='utf-8')
        (root / 'versions' / 'new' / 'haivequeen-update.json').write_text('{"compatible_with_older": true}', encoding='utf-8')
        version_layout.set_active(root, 'old')
        code = ('import os,sys,time; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)')
        child = subprocess.Popen([sys.executable, '-c', code, str(loop.lock_file())])
        try:
            deadline = time.monotonic() + 10
            while loop.loop_running() != child.pid and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(loop.loop_running(), child.pid)
            result = selfupdate.run_update(self.conn, live=root / 'versions' / 'old', version='new', poll=.05, round_timeout=.5)
            self.assertEqual(result['result'], 'failed')
            self.assertIn('old core restart was not verified', result['note'])
            self.assertEqual((root / 'active-version').read_text().strip(), 'old')
            self.assertIsNone(child.poll())
            self.assertEqual(loop.loop_running(), child.pid)
        finally:
            child.terminate()
            child.wait(timeout=10)
            loop.lock_file().unlink(missing_ok=True)
