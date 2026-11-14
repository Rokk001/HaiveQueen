import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from haivequeen import export


class ExportIsolationTests(unittest.TestCase):
    def test_overlapping_exports_publish_complete_independent_backups(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source.db'
            folder = root / 'exports'
            with closing(sqlite3.connect(source)) as conn:
                conn.execute('CREATE TABLE evidence(value)')
                conn.execute('INSERT INTO evidence VALUES (42)')
                conn.commit()
            barrier = threading.Barrier(2)
            connect = export.sqlite3.connect
            replace = export.os.replace
            paths = []

            def publish(src, dst):
                paths.append(str(src))
                replace(src, dst)

            class Source:
                def __init__(self, conn):
                    self.conn = conn
                def backup(self, dst):
                    barrier.wait(timeout=5)
                    self.conn.backup(dst)
                def close(self):
                    self.conn.close()

            def connection(path, **kwargs):
                conn = connect(path, **kwargs)
                return Source(conn) if str(path) == str(source) else conn

            with patch.object(export.os, 'replace', side_effect=publish), patch.object(export.sqlite3, 'connect', side_effect=connection):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    jobs = [pool.submit(export.run_export, datetime(2026, 1, 5), folder, source) for _ in range(2)]
                    results = [job.result(timeout=10) for job in jobs]
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(result['ok'] for result in results))
            with closing(sqlite3.connect(results[0]['file'])) as backup:
                self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(backup.execute('SELECT value FROM evidence').fetchone()[0], 42)
            self.assertEqual(list(folder.glob('*.tmp')), [])

    def test_failed_backup_preserves_previous_export_and_removes_own_temp(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            target = export.export_path(datetime(2026, 1, 5), root)
            target.write_bytes(b'previous valid backup')
            unrelated = root / 'another-export.tmp'
            unrelated.write_bytes(b'active')
            with patch.object(export.sqlite3, 'connect', side_effect=sqlite3.OperationalError('unavailable')):
                with self.assertRaises(sqlite3.OperationalError):
                    export.run_export(datetime(2026, 1, 5), root, root / 'source.db')
            self.assertEqual(target.read_bytes(), b'previous valid backup')
            self.assertEqual(list(root.glob('*.tmp')), [unrelated])

    def test_failed_publication_keeps_existing_backup(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source.db'
            with closing(sqlite3.connect(source)) as conn:
                conn.execute('CREATE TABLE evidence(value)')
            folder = root / 'exports'
            folder.mkdir()
            target = export.export_path(datetime(2026, 1, 5), folder)
            target.write_bytes(b'previous backup')
            with patch.object(export.os, 'replace', side_effect=PermissionError('locked target')):
                with self.assertRaises(PermissionError):
                    export.run_export(datetime(2026, 1, 5), folder, source)
            self.assertEqual(target.read_bytes(), b'previous backup')
            self.assertEqual(list(folder.glob('*.tmp')), [])

    def test_separate_processes_export_to_same_day(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source.db'
            with closing(sqlite3.connect(source)) as conn:
                conn.execute('CREATE TABLE evidence(value)')
                conn.execute('INSERT INTO evidence VALUES (42)')
                conn.commit()
            folder = root / 'exports'
            code = ('from haivequeen import export; from datetime import datetime; import sys; '
                    'export.run_export(datetime(2026, 1, 5), sys.argv[1], sys.argv[2])')
            jobs = [subprocess.Popen([sys.executable, '-c', code, str(folder), str(source)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            for job in jobs:
                out, err = job.communicate(timeout=20)
                self.assertEqual(job.returncode, 0, err.decode())
            with closing(sqlite3.connect(export.export_path(datetime(2026, 1, 5), folder))) as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(conn.execute('SELECT value FROM evidence').fetchone()[0], 42)
            self.assertEqual(list(folder.glob('*.tmp')), [])
