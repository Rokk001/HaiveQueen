import json
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from haivequeen import release_compat


class CompatibilityTests(unittest.TestCase):
    def test_declarations_and_unknown_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(release_compat.compatibility(root)['compatible'])
            path = root / release_compat.METADATA
            for reason in ('Database migration', 'Shared settings change'):
                path.write_text(json.dumps({'compatible_with_older': False, 'reason': reason}))
                self.assertEqual(release_compat.compatibility(root), {'compatible': False, 'reason': reason})
            path.write_text(json.dumps({'compatible_with_older': True}))
            self.assertTrue(release_compat.compatibility(root)['compatible'])
            path.write_text(json.dumps({'compatible_with_older': False}))
            self.assertEqual(release_compat.compatibility(root)['reason'], release_compat.UNKNOWN_REASON)

    def test_incompatible_gate_blocks_starts_before_snapshot_and_reports_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / release_compat.METADATA).write_text(json.dumps({'compatible_with_older': False, 'reason': 'Database migration'}))
            stop = root / 'STOP'
            jobs = [{'kind': k, 'name': k, 'project': 'Example'} for k in ('Queen', 'Agent', 'Build', 'Deploy')]
            def snapshot(conn):
                self.assertTrue(stop.exists())
                return jobs
            with patch.object(release_compat.loop, 'stop_flag', return_value=stop), patch.object(release_compat.loop, 'hive_schedule_lock', return_value=nullcontext()):
                result = release_compat.begin_wait(None, root, snapshot)
                self.assertEqual(result, {'waiting': True, 'reason': 'Database migration', 'jobs': jobs})
                result = release_compat.begin_wait(None, root, lambda conn: [])
                self.assertFalse(result['waiting'])
                self.assertTrue(stop.exists())

    def test_compatible_gate_never_stops_or_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / release_compat.METADATA).write_text(json.dumps({'compatible_with_older': True}))
            with patch.object(release_compat.loop, 'hive_schedule_lock', side_effect=AssertionError('must not lock')):
                self.assertFalse(release_compat.begin_wait(None, root)['waiting'])

    def test_job_snapshot_includes_native_agents_and_pipeline_work(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.executescript('CREATE TABLE embedded_bees(project TEXT,name TEXT,ended_at TEXT); CREATE TABLE kv(project TEXT,key TEXT,value TEXT);')
        conn.execute("INSERT INTO embedded_bees VALUES ('p','native',NULL)")
        for step in ('Build', 'Deploy'):
            conn.execute('INSERT INTO kv VALUES (?,?,?)', ('p', 'orch:' + step, json.dumps({'job': {'step': step, 'pid': 123}})))
        with patch.object(release_compat.runtime, 'list_agents', return_value=[{'name':'queen', 'project':'p', 'phase':'queen', 'status':'starting'}]), patch.object(release_compat.osplat, 'process_alive', return_value=True):
            self.assertEqual({job['kind'] for job in release_compat.running_jobs(conn)}, {'Queen', 'Agent', 'Build', 'Deploy'})
        conn.close()


if __name__ == '__main__':
    unittest.main()
