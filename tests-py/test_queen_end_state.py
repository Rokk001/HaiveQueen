import tempfile
import unittest
from pathlib import Path
from haivequeen import hives
from test_display_fix import EndedWithoutWorkTest


class QueenEndStateTest(EndedWithoutWorkTest):
    """Exercise persisted worktree snapshots and Queen report selection."""

    def test_uncommitted_change_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, run = root / 'repo', root / 'run'
            repo.mkdir(); run.mkdir()
            self.git(repo, 'init', '-q')
            (repo / 'a').write_text('old')
            self.git(repo, 'add', 'a')
            self.git(repo, 'commit', '-q', '-m', 'old', date='2020-01-01T00:00:00')
            (repo / 'a').write_text('baseline')
            hives.save_worktree_state(repo, run)
            class Env:
                def run_dir(self, name): return run
            agent = {'name': 'Ada', 'cwd': str(repo), 'started_at': '2021-01-01T00:00:00'}
            self.assertTrue(hives.ended_without_work(Env(), agent))
            (repo / 'a').write_text('changed')
            self.assertFalse(hives.ended_without_work(Env(), agent))
            (repo / 'a').write_text('baseline')
            (repo / 'a').unlink()
            self.assertFalse(hives.ended_without_work(Env(), agent))

    def test_report_file_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'final.txt').write_text('first')
            (run / 'report.md').write_text('WAITING_FOR_OWNER')
            self.assertEqual(hives.queen_final_text(run, 0), 'WAITING_FOR_OWNER')


    def test_ignored_and_state_file_do_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / 'repo'; run = root / 'run'
            repo.mkdir(); run.mkdir()
            self.git(repo, 'init', '-q')
            (repo / '.gitignore').write_text('ignored\n')
            (repo / 'state.md').write_text('before')
            self.git(repo, 'add', '.gitignore', 'state.md')
            self.git(repo, 'commit', '-q', '-m', 'old', date='2020-01-01T00:00:00')
            hives.save_worktree_state(repo, run, [repo / 'state.md', repo / 'alternate-state.md'])
            (repo / 'state.md').write_text('after')
            (repo / 'ignored').write_text('ignored')
            class Env:
                def run_dir(self, name): return run
            agent = {'name': 'Ada', 'cwd': str(repo), 'started_at': '2021-01-01T00:00:00'}
            self.assertTrue(hives.ended_without_work(Env(), agent))
            (repo / 'new').write_text('new')
            self.assertFalse(hives.ended_without_work(Env(), agent))

    def test_unreadable_snapshot_worktree_counts_as_work(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = root / 'repo'; run = root / 'run'
            repo.mkdir(); run.mkdir()
            self.git(repo, 'init', '-q')
            (repo / 'a').write_text('old')
            self.git(repo, 'add', 'a')
            self.git(repo, 'commit', '-q', '-m', 'old', date='2020-01-01T00:00:00')
            hives.save_worktree_state(repo, run)
            class Env:
                def run_dir(self, name): return run
            agent = {'name': 'Ada', 'cwd': str(repo), 'started_at': '2021-01-01T00:00:00'}
            with patch.object(hives, 'worktree_state', side_effect=OSError('unreadable')):
                self.assertFalse(hives.ended_without_work(Env(), agent))

    def test_runner_files_are_excluded_and_message_is_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            for name in ('runner-error.txt', 'rules.md', 'prompt.md', 'stderr.txt'):
                (run / name).write_text('not a report')
            self.assertEqual(hives.queen_final_text(run, 0, 'last message'), 'last message')
            self.assertEqual(hives.queen_final_text(run, 0), '')

    def test_claude_empty_result_keeps_last_message(self):
        import io
        from haivequeen.agent_runner import AgentRun
        with tempfile.TemporaryDirectory() as tmp:
            run = AgentRun(None, 'P', 'Ada', 'claude', 'claude', tmp, tmp, phase='queen', out=io.StringIO())
            run._claude_event({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'last message'}]}})
            run._claude_event({'type': 'result', 'result': ''})
            self.assertEqual(run.final, 'last message')

    def test_newest_report_and_question_text(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'old.md').write_text('old')
            (run / 'new.txt').write_text('real report')
            os.utime(run / 'old.md', (1, 1))
            os.utime(run / 'new.txt', (2, 2))
            text = hives.queen_final_text(run, 0, 'message')
            self.assertEqual(text, 'real report')
            self.assertEqual(hives.queen_final_text(run, 3, 'message'), 'message')
            self.assertEqual(hives.queen_final_text(run, 0, 'message', ended=1.5), 'old')
            class Env:
                name = 'P'
            with patch.object(hives.db, 'add_question', return_value=1) as add:
                hives.ask_about_end(None, Env(), 'plan', {'name': 'Ada', 'status': 'done'}, text)
                question = add.call_args.args[4]
                self.assertIn('(no commit, no changed file, no question)', question)
                self.assertIn('real report', question)
                hives.ask_about_end(None, Env(), 'plan', {'name': 'Ada', 'status': 'done'}, '')
                self.assertIn('no final text (status done)', add.call_args.args[4])


from bzztest import REL, RoundCase
from haivequeen import db, runtime


class QueenReportRoundTest(RoundCase):
    """The Hive uses the Queen's report when deciding to wait for the owner."""

    def test_report_waiting_for_owner_controls_hive(self):
        self.plan('READY', [{'id': 'P1', 'title': 'First'}])
        self.round()
        queen = next(a for a in runtime.list_agents(self.conn, 'testproj', 'all') if a['status'] in ('running', 'starting'))
        db.add_question(self.conn, 'testproj', REL, 'P1', 'Which?', '')
        self.finish(queen['name'], final='first sentence')
        runtime.upsert_agent(self.conn, 'testproj', queen['name'], {'pid': None})
        (self.env().run_dir(queen['name']) / 'report.md').write_text('WAITING_FOR_OWNER')
        self.round()
        self.assertEqual(hives.get(self.conn, 'testproj', REL)['state'], 'answer')


from test_agent_runner import RunnerBase, FAKE_CLI, codex_stream


class QueenReportPaneTest(RunnerBase):
    """A report written during the run is printed and saved as the final text."""

    def test_pane_and_saved_final_match_report(self):
        script = self.base / 'fake_cli.py'
        script.write_text("from pathlib import Path\nimport os\nPath(os.environ['REPORT_PATH']).write_text('actual report')\n" + FAKE_CLI)
        self.prepare('Q-1', provider='codex')
        report = self.runs / 'Q-1' / 'report.md'
        result = self.run_runner('Q-1', 'codex', codex_stream(), phase='queen', env={'REPORT_PATH': str(report)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((report.parent / 'final.txt').read_text(encoding='utf-8'), 'actual report')
        self.assertIn('Final message: actual report', result.stdout)
