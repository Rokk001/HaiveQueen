"""Isolated regressions for authoritative Claude CLI quota refreshes."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock
from bzztest import RoundCase
import os
from haivequeen import overview, runtime, usage
from haivequeen import dashboard_data


# Exercise metadata without real provider processes or authentication access.
class ClaudeRefreshTests(RoundCase):
    """Only zero-turn injected metadata may update the temporary cache."""

    # Seed a stale snapshot for refresh and failure tests.
    def seed(self):
        """Store old session twenty-percent and weekly ninety-percent readings."""
        self.reset = datetime.now(timezone.utc) + timedelta(days=1)
        runtime.kv_set(self.conn, '*', 'usage-claude', json.dumps({'saved': '2026-01-01T00:00:00+00:00',
            'fiveHour': {'utilization': 0.2, 'resetsAt': int(self.reset.timestamp())},
            'sevenDay': {'utilization': 0.9, 'resetsAt': int(self.reset.timestamp())}}))

    # Build the proven top-level assistant report and a metadata-only final result.
    def response(self, rows=None, **result):
        """Return a synthetic CompletedProcess for the injected subprocess runner."""
        if rows is None:
            rows = [{'kind': 'weekly_all', 'percent': 100, 'resets_at': self.reset.isoformat(), 'is_active': False}]
        events = [{'type': 'assistant', 'usage_report': {'rate_limits': {'limits': rows}}},
                  {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 0, 'total_cost_usd': 0, **result}]
        return subprocess.CompletedProcess([], 0, '\n'.join(json.dumps(e) for e in events), '')

    # Inject the process boundary and resolve only a fake executable.
    def refresh(self, run=None, **kwargs):
        """Call refresh with the configured Claude provider in the temporary runtime."""
        with mock.patch('haivequeen.usage.shutil.which', return_value='/fake/claude'):
            return usage.refresh_claude_usage(self.conn, [{'command': 'claude', 'limit': {'source': 'claude'}}],
                                              run=run or mock.Mock(return_value=self.response()), **kwargs)

    def test_transient_missing_limits_retries_before_failing(self):
        """A transient empty rate-limit response is retried without losing the reading."""
        self.seed()
        empty = self.response()
        events = [json.loads(line) for line in empty.stdout.splitlines()]
        events[0]['usage_report']['rate_limits']['limits'] = None
        empty.stdout = "\n".join(json.dumps(event) for event in events)
        runner = mock.Mock(side_effect=[empty, self.response()])
        self.assertTrue(self.refresh(run=runner))
        self.assertEqual(runner.call_count, 2)
        self.assertFalse(usage.claude_refresh_failed(self.conn))

    def test_retention_expires_at_six_hours(self):
        """A reading exactly six hours old is no longer younger than six hours."""
        fixed = datetime(2026, 11, 8, tzinfo=timezone.utc)
        runtime.kv_set(self.conn, '*', 'usage-claude', json.dumps({
            'saved': (fixed - timedelta(hours=6)).isoformat(), 'refresh_error': 'failed',
            'sevenDay': {'utilization': 0.2, 'resetsAt': None}}))
        with mock.patch('haivequeen.usage.datetime', wraps=datetime) as clock:
            clock.now.return_value = fixed
            self.assertIsNone(usage.retained_claude(self.conn))

    # A current global reading replaces stale data even when is_active is false.
    def test_stale_ninety_becomes_current_hundred(self):
        """Missing session quota remains unknown rather than copying the old value."""
        self.seed()
        self.assertTrue(self.refresh())
        raw = json.loads(runtime.kv_get(self.conn, '*', 'usage-claude'))
        self.assertEqual(raw['sevenDay']['utilization'], 1)
        self.assertIsNone(raw['fiveHour'])
        self.assertIn('checked_at', raw)
        self.assertIn('source', raw)
        self.assertEqual(usage.get_claude_usage(self.conn)['seven_day']['pct'], 100)

    # Session exhaustion is independent of weekly and scoped percentages.
    def test_session_blocks_and_scoped_weekly_is_ignored(self):
        """A scoped weekly value never fabricates the missing account-wide window."""
        self.seed()
        rows = [{'kind': 'session', 'percent': 100, 'resets_at': self.reset.isoformat()},
                {'kind': 'weekly_scoped', 'percent': 1, 'resets_at': self.reset.isoformat()}]
        self.assertTrue(self.refresh(run=mock.Mock(return_value=self.response(rows))))
        self.assertIsNone(usage.get_claude_usage(self.conn)['seven_day'])
        self.assertEqual(usage.limit_windows(self.conn, {'limit': 'claude'}, read_pc=True)[0]['used_pct'], 100)

    # Model turns, cost and unsuccessful completion invalidate the response.
    def test_only_zero_turn_zero_cost_success_is_accepted(self):
        """Metadata guards cannot be bypassed by an otherwise valid quota report."""
        self.seed()
        for result in ({'num_turns': 1}, {'num_turns': False}, {'total_cost_usd': 0.01}, {'total_cost_usd': False}, {'subtype': 'error'}, {'is_error': True}):
            with self.subTest(result=result):
                self.assertFalse(self.refresh(run=mock.Mock(return_value=self.response(**result)), force=True))
                self.assertTrue(usage.claude_refresh_failed(self.conn))
                self.assertIsNone(usage.get_claude_usage(self.conn)['seven_day'])

    # Failures preserve historical raw data without displaying it or allowing starts.
    def test_transport_and_invalid_report_fail_closed(self):
        """Timeout, JSON, exit, empty-report and percentage errors stay generic."""
        self.seed()
        runners = [mock.Mock(side_effect=subprocess.TimeoutExpired('claude', 20)),
                   mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'not JSON SECRET', 'SECRET')),
                   mock.Mock(return_value=subprocess.CompletedProcess([], 1, '', 'SECRET')),
                   mock.Mock(return_value=self.response([])),
                   mock.Mock(return_value=self.response([{'kind': 'weekly_all', 'percent': -1, 'resets_at': self.reset.isoformat()}]))]
        for runner in runners:
            with self.subTest(runner=runner):
                self.assertFalse(self.refresh(run=runner, force=True))
                raw = runtime.kv_get(self.conn, '*', 'usage-claude')
                self.assertNotIn('SECRET', raw)
                self.assertEqual(json.loads(raw)['sevenDay']['utilization'], 0.9)
                self.assertIsNone(usage.get_claude_usage(self.conn)['seven_day'])
                limit = usage.get_provider_limit(self.conn, {'limit': 'claude'}, read_pc=True)
                self.assertEqual((limit['used_pct'], limit['free_pct']), (None, 0))
                self.assertEqual(usage.limit_windows(self.conn, {'limit': 'claude'}, read_pc=True),
                                 [{'used_pct': 100, 'reset_epoch': None, 'unknown': True}])

    # Shared cache timestamps throttle callers and retries can clear failure state.
    def test_throttle_and_forced_retry(self):
        """Failure and success both throttle, while force permits an immediate recovery."""
        self.seed()
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired('claude', 20))
        self.assertFalse(self.refresh(run=runner))
        self.assertFalse(self.refresh(run=runner))
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(self.refresh(force=True))
        self.assertFalse(usage.claude_refresh_failed(self.conn))
        other = mock.Mock(return_value=self.response())
        self.assertFalse(self.refresh(run=other))
        other.assert_not_called()

    # A provider that could not be reached is checked only every ten minutes.
    def test_unreachable_provider_is_checked_every_ten_minutes(self):
        """After a failed check, a retry five minutes later is throttled; after a successful check the one minute interval applies."""
        self.seed()
        failing = mock.Mock(side_effect=subprocess.TimeoutExpired('claude', 20))
        self.assertFalse(self.refresh(run=failing, force=True))
        self.assertTrue(usage.claude_refresh_failed(self.conn))
        runtime.kv_set(self.conn, '*', usage.CLAUDE_CHECK_KEY, (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
        retry = mock.Mock(return_value=self.response())
        self.assertFalse(self.refresh(run=retry))
        retry.assert_not_called()
        runtime.kv_set(self.conn, '*', usage.CLAUDE_CHECK_KEY, (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat())
        self.assertTrue(self.refresh(run=retry))
        retry.assert_called_once()
        runtime.kv_set(self.conn, '*', usage.CLAUDE_CHECK_KEY, (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat())
        again = mock.Mock(return_value=self.response())
        self.assertTrue(self.refresh(run=again))   # reachable again: one minute interval
        again.assert_called_once()

    def test_named_providers_have_independent_intervals(self):
        self.seed()
        providers = [{'name': 'slow', 'command': 'claude', 'limit': {'source': 'claude', 'interval': 3600}},
                     {'name': 'fast', 'command': 'claude', 'limit': {'source': 'claude', 'interval': 60}}]
        run = mock.Mock(return_value=self.response())
        with mock.patch('haivequeen.usage.shutil.which', return_value='/fake/claude'):
            self.assertTrue(usage.refresh_claude_usage(self.conn, providers, run=run))
            self.assertTrue(usage.refresh_claude_usage(self.conn, providers, run=run))
            self.assertFalse(usage.refresh_claude_usage(self.conn, providers, run=run))
        self.assertEqual(run.call_count, 2)

    # The ten minute rule is the same for every model provider.
    def test_usage_interval_depends_only_on_reachability(self):
        """A reachable provider is asked every minute, an unreachable one every ten minutes."""
        self.assertEqual(usage.usage_interval(False), usage.USAGE_INTERVAL)
        self.assertEqual(usage.usage_interval(True), usage.USAGE_UNREACHABLE_INTERVAL)

    # Codex is read less often while it cannot be reached.
    def test_codex_refresh_follows_reachability(self):
        """A read that found no window throttles for ten minutes; once windows are found the one minute interval applies."""
        import tempfile
        from pathlib import Path
        empty = {'five_hour': None, 'seven_day': None}
        window = {'five_hour': {'pct': 10, 'at': 1}, 'seven_day': None}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(usage, 'get_codex_usage', return_value=empty) as read:
            usage._codex_refresh.clear()
            usage._codex_unreachable.clear()
            usage.refresh_codex_usage(self.conn, folder)
            self.assertEqual(read.call_count, 1)
            usage._codex_refresh[str(Path(folder))] -= 30
            usage.refresh_codex_usage(self.conn, folder)
            self.assertEqual(read.call_count, 1)
            usage._codex_refresh[str(Path(folder))] -= 60
            read.return_value = window
            self.assertTrue(usage.refresh_codex_usage(self.conn, folder))
            self.assertEqual(read.call_count, 2)
            usage._codex_refresh[str(Path(folder))] -= 120
            usage.refresh_codex_usage(self.conn, folder)
            self.assertEqual(read.call_count, 3)
            usage._codex_refresh.clear()
            usage._codex_unreachable.clear()

    # Isolation prohibits implicit real CLI execution.
    def test_test_runtime_never_starts_cli_without_injector(self):
        """The normal test environment cannot reach auth or network through refresh."""
        with mock.patch('haivequeen.usage.subprocess.run') as run:
            self.assertFalse(usage.refresh_claude_usage(self.conn, [{'command': 'claude', 'limit': 'claude'}]))
            run.assert_not_called()

    # Refresh disables hooks, tools, MCP and persistent sessions without using a shell.
    def test_safe_process_arguments_and_environment(self):
        """The proven metadata command is bounded with closed input and hidden output."""
        self.seed()
        run = mock.Mock(return_value=self.response())
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}):
            self.assertTrue(self.refresh(run=run))
        args, kwargs = run.call_args
        argv = args[0]
        self.assertEqual(argv[:3], ['/fake/claude', '-p', '/usage'])
        for flag in ('--no-session-persistence', '--strict-mcp-config', '--setting-sources', '--settings', '--mcp-config', '--tools'):
            self.assertIn(flag, argv)
        self.assertNotIn('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', kwargs['env'])
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertLessEqual(kwargs['timeout'], 60)
        self.assertFalse(kwargs.get('shell', False))

    # Malformed and nonfinite quota fields cannot create a fresh-looking snapshot.
    def test_invalid_numeric_and_reset_values_are_rejected(self):
        """NaN, infinities, invalid ranges and ambiguous resets fail closed."""
        self.seed()
        for percent in (-1, 101, float('nan'), float('inf'), float('-inf'), True):
            with self.subTest(percent=percent):
                row = {'kind': 'weekly_all', 'percent': percent, 'resets_at': self.reset.isoformat()}
                self.assertFalse(self.refresh(run=mock.Mock(return_value=self.response([row])), force=True))
        for reset in ('bad', '2026-11-01T19:00:00'):
            with self.subTest(reset=reset):
                row = {'kind': 'weekly_all', 'percent': 10, 'resets_at': reset}
                self.assertFalse(self.refresh(run=mock.Mock(return_value=self.response([row])), force=True))

    # Ordinary interval expiry must permit retry without requiring force.
    def test_expired_checked_at_allows_new_attempt(self):
        """A persisted timestamp throttles all callers only until the interval ends."""
        self.seed()
        self.assertTrue(self.refresh())
        raw = json.loads(runtime.kv_get(self.conn, '*', 'usage-claude'))
        raw['checked_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
        runtime.kv_set(self.conn, '*', 'usage-claude', json.dumps(raw))
        runtime.kv_set(self.conn, '*', 'usage-claude-last-check', raw['checked_at'])
        run = mock.Mock(return_value=self.response())
        self.assertTrue(self.refresh(run=run))
        self.assertEqual(run.call_count, 1)


    # A documented null reset does not invalidate a verified quota percentage.
    def test_known_percent_with_null_reset_remains_visible_and_blocks(self):
        """Retain real session and weekly exhaustion without inventing reset times."""
        self.seed()
        rows = [{'kind': 'session', 'percent': 100, 'resets_at': None},
                {'kind': 'weekly_all', 'percent': 100, 'resets_at': None}]
        self.assertTrue(self.refresh(run=mock.Mock(return_value=self.response(rows))))
        known = usage.get_claude_usage(self.conn)
        self.assertEqual(known['five_hour'], {'pct': 100, 'reset': '', 'at': None})
        self.assertEqual(known['seven_day'], {'pct': 100, 'reset': '', 'at': None})
        windows = usage.limit_windows(self.conn, {'limit': 'claude'}, read_pc=True)
        self.assertEqual(windows, [{'used_pct': 100, 'reset_epoch': None}, {'used_pct': 100, 'reset_epoch': None}])
        limit = usage.get_provider_limit(self.conn, {'limit': 'claude'}, read_pc=True)
        self.assertEqual((limit['used_pct'], limit['free_pct'], limit['reset_epoch']), (100, 0, None))

    # Active stream telemetry must not erase the quota refresh interval.
    def test_stream_writer_cannot_erase_refresh_throttle(self):
        """A separate last-check key survives canonical cache replacement and expires normally."""
        self.seed()
        self.assertTrue(self.refresh())
        usage.save_claude_usage(self.conn, {'rate_limit_info': {'unifiedWindows': {
            'five_hour': {'utilization': 0.5, 'resetsAt': int(self.reset.timestamp())},
            'seven_day': {'utilization': 0.9, 'resetsAt': int(self.reset.timestamp())}}}})
        self.assertNotIn('checked_at', json.loads(runtime.kv_get(self.conn, '*', 'usage-claude')))
        run = mock.Mock(return_value=self.response())
        self.assertFalse(self.refresh(run=run))
        run.assert_not_called()
        expired = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
        runtime.kv_set(self.conn, '*', 'usage-claude-last-check', expired)
        self.assertTrue(self.refresh(run=run))
        self.assertEqual(run.call_count, 1)

    # The cause of a failed check stays readable after the next check overwrites the kv text.
    def test_failed_check_is_logged_as_event(self):
        """Every failed check writes one usage event with its error text; a good check writes none."""
        self.seed()
        self.assertFalse(self.refresh(run=mock.Mock(side_effect=subprocess.TimeoutExpired('claude', 20)), force=True))
        self.assertFalse(self.refresh(run=mock.Mock(return_value=subprocess.CompletedProcess([], 1, '', 'SECRET')), force=True))
        self.assertTrue(self.refresh(force=True))
        rows = self.conn.execute("SELECT ts, project, text FROM events WHERE kind='usage' ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIn('timed out', rows[0][2])
        self.assertIn('exit 1', rows[1][2])
        self.assertTrue(all(r[0] for r in rows))
        self.assertNotIn('SECRET', ''.join(r[2] for r in rows))

    # Under load Claude needs more than the old 20 seconds.
    def test_timeout_leaves_room_for_load(self):
        """The CLI call gets at least 60 seconds and a timeout is reported as such."""
        self.seed()
        run = mock.Mock(side_effect=subprocess.TimeoutExpired('claude', 60))
        self.refresh(run=run, force=True)
        self.assertGreaterEqual(run.call_args.kwargs['timeout'], 60)
        self.assertIn('timed out', json.loads(runtime.kv_get(self.conn, '*', 'usage-claude'))['refresh_error'])

    # Model Limits keeps the last verified weekly reading while it is young, marked as not current.
    def test_model_limits_show_retained_reading_with_age(self):
        """After a failed check the weekly row shows the old percentage, not current, with its age; after RETAIN_HOURS it is n/a."""
        self.seed()
        providers = [{'name': 'claude', 'protocol': 'claude', 'use': 'preferred', 'slots': 2, 'limit': 'claude',
                      'classes': {'medium': {'model': 'm'}}}]
        saved = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        runtime.kv_set(self.conn, '*', 'usage-claude:claude', json.dumps({'saved': saved, 'refresh_error': 'Claude usage refresh failed: x',
            'sevenDay': {'utilization': 0.9, 'resetsAt': int(self.reset.timestamp())}}))
        env = {k: v for k, v in os.environ.items() if k != 'BZZ_RUNTIME_OVERRIDE'}
        with mock.patch.dict(os.environ, env, clear=True):
            row = overview.provider_lines(self.conn, {'providers': providers}, [])[0]
            self.assertEqual(row['used_pct'], 90)
            self.assertTrue(row['stale'])
            self.assertAlmostEqual(row['stale_hours'], 2, delta=0.1)
            shown = dashboard_data.limit_rows({'provider_limits': [row]})[0]
            self.assertEqual(shown['weekly']['pct'], 90)
            self.assertEqual(shown['reserve']['age'], '2h')
            old = (datetime.now(timezone.utc) - timedelta(hours=usage.RETAIN_HOURS + 1)).isoformat()
            runtime.kv_set(self.conn, '*', 'usage-claude:claude', json.dumps({'saved': old, 'refresh_error': 'x',
                'sevenDay': {'utilization': 0.9, 'resetsAt': int(self.reset.timestamp())}}))
            row = overview.provider_lines(self.conn, {'providers': providers}, [])[0]
            self.assertIsNone(row['used_pct'])
            self.assertFalse(row['stale'])


class ClaudeReadingDisplayTests(ClaudeRefreshTests):
    """Retained five-hour window and expired readings in the panel and in the reserve."""

    def providers(self):
        return [{'name': 'claude', 'protocol': 'claude', 'use': 'preferred', 'slots': 2, 'limit': 'claude', 'configuredSlots': 2,
                 'classes': {'medium': {'model': 'm'}}}]

    def store(self, hours_old):
        saved = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
        reset = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        runtime.kv_set(self.conn, '*', 'usage-claude:claude', json.dumps({
            'saved': saved, 'refresh_error': 'Claude usage refresh failed: x',
            'fiveHour': {'utilization': 0.4, 'resetsAt': reset},
            'sevenDay': {'utilization': 0.5, 'resetsAt': reset + 3 * 24 * 3600}}))

    def row(self):
        env = {k: v for k, v in os.environ.items() if k != 'BZZ_RUNTIME_OVERRIDE'}
        with mock.patch.dict(os.environ, env, clear=True):
            return overview.provider_lines(self.conn, {'providers': self.providers()}, [])[0]

    def test_retained_five_hour_window_stays_in_panel(self):
        self.store(2)
        shown = dashboard_data.limit_rows({'provider_limits': [self.row()]})[0]
        self.assertEqual(shown['five'][:5], '5h 40')
        self.assertNotEqual(shown['reserve']['level'], 'bad')
        self.assertFalse(shown['slots']['blocked'])

    def test_expired_reading_shows_red_unknown_grey_slots_and_blocks_work(self):
        from haivequeen import scheduler
        self.store(usage.RETAIN_HOURS + 1)
        row = self.row()
        shown = dashboard_data.limit_rows({'provider_limits': [row]})[0]
        self.assertEqual((shown['reserve']['text'], shown['reserve']['level']), ('unknown', 'bad'))
        self.assertTrue(shown['slots']['blocked'])
        self.assertEqual(shown['five'], '')
        env = {k: v for k, v in os.environ.items() if k != 'BZZ_RUNTIME_OVERRIDE'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(scheduler.reserve_zero(self.providers()[0], self.conn))

    def test_missing_quotas_root_cause_a_later_report_without_limits(self):
        """claude -p /usage can emit a further usage_report whose rate_limits is null after the one with the limits; only the last report was read."""
        self.seed()
        full = self.response()
        events = [json.loads(line) for line in full.stdout.splitlines()]
        events.insert(1, {'type': 'assistant', 'usage_report': {'rate_limits': None}})
        full.stdout = '\n'.join(json.dumps(e) for e in events)
        self.assertTrue(self.refresh(run=mock.Mock(return_value=full)))

    def test_retained_reading_of_a_named_provider_keeps_its_reset_time(self):
        self.store(2)
        row = self.row()
        self.assertIsNotNone(row['reset_at'])
        self.assertAlmostEqual(row['hours'], 3 * 24 + 2, delta=0.1)
        self.assertAlmostEqual(row['stale_hours'], 2, delta=0.1)

    def test_fresh_five_hour_reading_keeps_its_reset_time_for_the_reserve(self):
        from haivequeen import scheduler
        now = datetime.now(timezone.utc)
        reset = int((now + timedelta(hours=1)).timestamp())
        runtime.kv_set(self.conn, '*', 'usage-claude:claude', json.dumps({
            'saved': now.isoformat(), 'fiveHour': {'utilization': 0.5, 'resetsAt': reset},
            'sevenDay': {'utilization': 0.1, 'resetsAt': int((now + timedelta(days=6)).timestamp())}}))
        env = {k: v for k, v in os.environ.items() if k not in ('BZZ_RUNTIME_OVERRIDE', 'BZZ_PROVIDER_LIMITS_FILE')}
        with mock.patch.dict(os.environ, env, clear=True):
            window = scheduler._five_hour_window(self.providers()[0], self.conn)
        self.assertEqual(window['reset_epoch'], reset)

    def test_weekly_window_that_has_reset_shows_reset_now(self):
        now = datetime.now(timezone.utc)
        runtime.kv_set(self.conn, '*', 'usage-claude:claude', json.dumps({
            'saved': now.isoformat(),
            'sevenDay': {'utilization': 0.9, 'resetsAt': int((now - timedelta(hours=1)).timestamp())}}))
        shown = dashboard_data.limit_rows({'provider_limits': [self.row()]})[0]
        self.assertIn('reset now', shown['weekly']['text'])
        self.assertNotEqual(shown['reserve']['text'], 'unknown')
